import asyncio
import os
import uuid
import redis
import json
import logging
import websockets
import httpx
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from models import CustomMeeting, MeetingParticipant, CustomMeetingTranscript, User
from schemas import CreateCustomMeetingRequest, JoinMeetingRequest

logger = logging.getLogger(__name__)


class WebRTCMeetingService:
    """Сервис для управления WebRTC встречами"""
    
    def __init__(self):
        self.redis = redis.Redis(host='redis', port=6379, decode_responses=True)
        self.active_meetings: Dict[str, Dict] = {}
        self.websocket_connections: Dict[str, List[WebSocket]] = {}
        self.ai_agent_connections: Dict[str, WebSocket] = {}
        self._last_audio_chunk_ts = {}  # dict[meeting_id] = timestamp (защита от дублирования)
        
    async def create_meeting(
        self, 
        meeting_data: CreateCustomMeetingRequest,
        creator_id: int,
        db: Session
    ) -> Dict:
        """Создает новую WebRTC встречу"""
        try:
            meeting_id = str(uuid.uuid4())
            
            # Создаем встречу в базе данных
            db_meeting = CustomMeeting(
                meeting_id=meeting_id,
                topic=meeting_data.topic,
                creator_id=creator_id,
                max_participants=meeting_data.max_participants,
                duration_minutes=meeting_data.duration_minutes,
                password=meeting_data.password,
                ai_agent_enabled=meeting_data.ai_agent_enabled,
                status="created"
            )
            
            db.add(db_meeting)
            db.commit()
            db.refresh(db_meeting)
            
            # Сохраняем в Redis для быстрого доступа
            meeting_info = {
                "id": str(db_meeting.id),
                "meeting_id": meeting_id,
                "topic": meeting_data.topic,
                "creator_id": str(creator_id),
                "status": "created",
                "max_participants": str(meeting_data.max_participants),
                "duration_minutes": str(meeting_data.duration_minutes),
                "password": meeting_data.password or "",
                "ai_agent_enabled": str(meeting_data.ai_agent_enabled),
                "created_at": datetime.utcnow().isoformat(),
                "participants": "[]"
            }
            
            self.redis.hset(f"meeting:{meeting_id}", mapping=meeting_info)
            self.redis.expire(f"meeting:{meeting_id}", meeting_data.duration_minutes * 60)
            
            # Инициализируем WebSocket соединения
            self.websocket_connections[meeting_id] = []
            
            logger.info(f"Created WebRTC meeting {meeting_id} for user {creator_id}")
            
            return {
                "meeting_id": meeting_id,
                "join_url": f"/meeting/{meeting_id}",
                "status": "created",
                "db_id": db_meeting.id
            }
            
        except Exception as e:
            logger.error(f"Error creating meeting: {e}")
            db.rollback()
            raise e
    
    async def join_meeting(
        self, 
        meeting_id: str, 
        user_id: int, 
        websocket: WebSocket,
        password: Optional[str] = None,
        db: Session = None
    ) -> Dict:
        """Подключает пользователя к встрече"""
        try:
            # Проверяем существование встречи
            meeting_data = self.redis.hgetall(f"meeting:{meeting_id}")
            if not meeting_data:
                raise ValueError("Meeting not found")
            
            # Проверяем пароль если требуется
            if meeting_data.get("password") and meeting_data.get("password") != password:
                raise ValueError("Invalid password")
            
            # Проверяем лимит участников
            participants = self.redis.smembers(f"meeting:{meeting_id}:participants")
            max_participants = int(meeting_data.get("max_participants", 10))
            
            if len(participants) >= max_participants:
                raise ValueError("Meeting is full")
            
            # Проверяем, не подключен ли уже пользователь
            if str(user_id) in participants:
                raise ValueError("User already in meeting")
            
            # Добавляем участника в Redis
            self.redis.sadd(f"meeting:{meeting_id}:participants", user_id)
            
            # Добавляем участника в базу данных
            if db:
                participant = MeetingParticipant(
                    meeting_id=int(meeting_data["id"]),
                    user_id=user_id,
                    role="participant"
                )
                db.add(participant)
                db.commit()
            
            # Добавляем WebSocket соединение
            self.websocket_connections[meeting_id].append(websocket)
            
            # Обновляем статус встречи на "active" если это первый участник
            if len(participants) == 0:
                self.redis.hset(f"meeting:{meeting_id}", "status", "active")
                self.redis.hset(f"meeting:{meeting_id}", "started_at", datetime.utcnow().isoformat())
            
            # Уведомляем других участников
            await self._notify_participants(meeting_id, {
                "type": "participant_joined",
                "user_id": user_id,
                "participants_count": len(participants) + 1,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            logger.info(f"User {user_id} joined meeting {meeting_id}")
            
            return {
                "status": "joined",
                "meeting_id": meeting_id,
                "participants_count": len(participants) + 1
            }
            
        except Exception as e:
            logger.error(f"Error joining meeting: {e}")
            raise e
    
    async def leave_meeting(self, meeting_id: str, user_id: int, db: Session = None):
        """Отключает пользователя от встречи"""
        try:
            # Удаляем участника из Redis
            self.redis.srem(f"meeting:{meeting_id}:participants", user_id)
            
            # Обновляем в базе данных
            if db:
                participant = db.query(MeetingParticipant).filter(
                    MeetingParticipant.meeting_id == meeting_id,
                    MeetingParticipant.user_id == user_id,
                    MeetingParticipant.left_at.is_(None)
                ).first()
                
                if participant:
                    participant.left_at = datetime.utcnow()
                    db.commit()
            
            # Удаляем WebSocket соединение
            connections = self.websocket_connections.get(meeting_id, [])
            for ws in connections[:]:  # Копируем список для безопасного удаления
                try:
                    # Проверяем, принадлежит ли соединение этому пользователю
                    # В реальной реализации нужно хранить mapping user_id -> websocket
                    connections.remove(ws)
                    break
                except ValueError:
                    pass
            
            # Уведомляем других участников
            participants = self.redis.smembers(f"meeting:{meeting_id}:participants")
            await self._notify_participants(meeting_id, {
                "type": "participant_left",
                "user_id": user_id,
                "participants_count": len(participants),
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # Если участников не осталось, завершаем встречу
            if len(participants) == 0:
                await self._end_meeting(meeting_id, db)
            
            logger.info(f"User {user_id} left meeting {meeting_id}")
            
        except Exception as e:
            logger.error(f"Error leaving meeting: {e}")
            raise e
    
    async def start_ai_agent(self, meeting_id: str) -> Dict:
        """Запускает AI агента для встречи"""
        try:
            meeting_data = self.redis.hgetall(f"meeting:{meeting_id}")
            if not meeting_data:
                raise ValueError("Meeting not found")
            
            if not meeting_data.get("ai_agent_enabled", "false").lower() == "true":
                logger.warning(f"AI agent flag is disabled for meeting {meeting_id}, proceeding by request")
            
            # Подключаемся к AI Agent Service (несколько попыток)
            ai_agent_websocket = None
            connect_error = None
            for attempt in range(3):
                ai_agent_websocket = await self._connect_to_ai_agent(meeting_id)
                if ai_agent_websocket:
                    break
                connect_error = connect_error or Exception("connect failed")
                await asyncio.sleep(0.5 * (attempt + 1))
            if ai_agent_websocket:
                self.ai_agent_connections[meeting_id] = ai_agent_websocket
                
                logger.info(f"AI Agent WebSocket connected for meeting {meeting_id}")
                
                # Уведомляем участников о запуске AI агента
                await self._notify_participants(meeting_id, {
                    "type": "ai_agent_started",
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                logger.info(f"AI Agent started for meeting {meeting_id}")
                
                return {
                    "status": "ai_agent_started",
                    "meeting_id": meeting_id
                }
            else:
                raise ValueError(f"Failed to connect to AI Agent Service. Last error: {connect_error}")
                
        except Exception as e:
            logger.error(f"Error starting AI agent: {e}")
            raise e
    
    async def _connect_to_ai_agent(self, meeting_id: str) -> Optional[WebSocket]:
        """Подключается к AI Agent Service"""
        try:
            # Список кандидатов: ENV → docker-сеть → localhost (локальный режим)
            base_candidates = []
            env_url = os.getenv("AI_AGENT_WS_URL")
            if env_url:
                env_url = env_url[:-1] if env_url.endswith("/") else env_url
                base_candidates.append(env_url)
            base_candidates.append("ws://ai_agent_service:8001/ws")
            base_candidates.append("ws://localhost:8001/ws")

            # Пробуем определить доступный хост по /health
            resolved_ws_url = None
            async with httpx.AsyncClient(timeout=2.0) as client:
                for base_ws in base_candidates:
                    # Преобразуем ws://host:port/ws -> http://host:port/health
                    http_base = base_ws.replace("wss://", "https://").replace("ws://", "http://")
                    http_health = http_base.rsplit("/", 1)[0] + "/health"
                    try:
                        r = await client.get(http_health)
                        if r.status_code == 200:
                            resolved_ws_url = base_ws
                            logger.info(f"AI Agent health OK at {http_health}")
                            break
                        else:
                            logger.error(f"AI Agent health bad at {http_health}: {r.status_code}")
                    except Exception as e:
                        logger.error(f"AI Agent health check failed at {http_health}: {e}")

            candidates = []
            if resolved_ws_url:
                candidates.append(f"{resolved_ws_url}/{meeting_id}")
            else:
                # если не удалось определить — пробуем все
                for base_ws in base_candidates:
                    candidates.append(f"{base_ws}/{meeting_id}")

            last_error = None
            ai_agent_ws = None
            for url in candidates:
                try:
                    logger.info(f"Trying to connect AI Agent WS: {url}")
                    ai_agent_ws = await websockets.connect(url)
                    logger.info(f"Connected AI Agent WS: {url}")
                    # Слушаем сообщения от AI агента
                    asyncio.create_task(self._listen_ai_agent_messages(meeting_id, ai_agent_ws))
                    return ai_agent_ws
                except Exception as e:
                    last_error = e
                    logger.error(f"Failed AI Agent WS connect to {url}: {e}")
                    continue

            if last_error:
                raise last_error
            
            # Не должно сюда дойти, но на всякий случай
            if ai_agent_ws:
                asyncio.create_task(self._listen_ai_agent_messages(meeting_id, ai_agent_ws))
                return ai_agent_ws
            
            raise Exception("No AI agent connection available")
            
        except Exception as e:
            logger.error(f"Error connecting to AI agent: {e}")
            return None
    
    async def _listen_ai_agent_messages(self, meeting_id: str, ai_websocket):
        """Слушает сообщения от AI агента"""
        import time
        logger.info(f"Started listening to AI agent messages for meeting {meeting_id}")
        try:
            async for message in ai_websocket:
                t5 = time.time()
                data = json.loads(message)
                msg_type = data.get('type', 'unknown')
                logger.info(f"[LATENCY] t5: received message from AI agent (type: {msg_type}) at {t5}, meeting_id: {meeting_id}")
                
                if data["type"] == "ai_agent_response":
                    # Защита: если в последние 15 секунд приходили чанки — считаем, что это дубликат и пропускаем
                    last_chunk = self._last_audio_chunk_ts.get(meeting_id)
                    if last_chunk and (time.time() - last_chunk) < 15.0:
                        logger.info(f"[DUPLICATE_FILTER] Ignoring ai_agent_response for {meeting_id} because stream was active {time.time()-last_chunk:.2f}s ago")
                        continue
                    
                    # Пересылаем ответ AI агента участникам (fallback случай)
                    t6 = time.time()
                    await self._notify_participants(meeting_id, {
                        "type": "ai_agent_response",
                        "audio_data": data.get("audio_data"),
                        "text": data.get("text"),
                        "timestamp": data.get("timestamp", datetime.utcnow().isoformat())
                    })
                    t6_end = time.time()
                    logger.info(f"[LATENCY] t6: forwarded ai_agent_response to clients at {t6_end}, delta t6-t5: {t6_end-t5:.3f}s")
                    logger.info(f"Forwarded AI agent response (fallback) to participants in meeting {meeting_id}")
                elif data["type"] == "ai_agent_audio_chunk":
                    # Обновляем метку времени получения чанка
                    self._last_audio_chunk_ts[meeting_id] = time.time()
                    logger.info(f"[DUPLICATE_FILTER] Updated last_audio_chunk_ts for {meeting_id} at {self._last_audio_chunk_ts[meeting_id]}")
                    
                    await self._notify_participants(meeting_id, {
                        "type": "ai_agent_audio_chunk",
                        "audio_data": data.get("audio_data"),
                        "timestamp": data.get("timestamp", datetime.utcnow().isoformat())
                    })
                elif data["type"] == "ai_agent_audio_end":
                    await self._notify_participants(meeting_id, {
                        "type": "ai_agent_audio_end",
                        "timestamp": data.get("timestamp", datetime.utcnow().isoformat())
                    })
                    # Не сбрасываем метку сразу, чтобы защита работала еще 15 секунд
                elif data["type"] == "ai_agent_text":
                    t6_text = time.time()
                    await self._notify_participants(meeting_id, {
                        "type": "ai_agent_text",
                        "text": data.get("text"),
                        "timestamp": data.get("timestamp", datetime.utcnow().isoformat())
                    })
                    t6_text_end = time.time()
                    logger.info(f"[LATENCY] t6: forwarded ai_agent_text to clients at {t6_text_end}, delta t6-t5: {t6_text_end-t5:.3f}s")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"AI Agent connection closed for meeting {meeting_id}")
        except Exception as e:
            logger.error(f"Error listening to AI agent messages: {e}")
    
    async def _notify_participants(self, meeting_id: str, message: Dict):
        """Уведомляет всех участников встречи"""
        connections = self.websocket_connections.get(meeting_id, [])
        logger.info(f"Notifying {len(connections)} participants in meeting {meeting_id}")
        
        for websocket in connections[:]:  # Копируем список для безопасного удаления
            try:
                await websocket.send_text(json.dumps(message))
                logger.info(f"Sent message to participant: {message['type']}")
            except Exception as e:
                logger.error(f"Error sending message to participant: {e}")
                # Удаляем неактивные соединения
                try:
                    connections.remove(websocket)
                except ValueError:
                    pass
    
    async def _end_meeting(self, meeting_id: str, db: Session = None):
        """Завершает встречу"""
        try:
            # Обновляем статус в Redis
            self.redis.hset(f"meeting:{meeting_id}", "status", "ended")
            self.redis.hset(f"meeting:{meeting_id}", "ended_at", datetime.utcnow().isoformat())
            
            # Обновляем в базе данных
            if db:
                meeting = db.query(CustomMeeting).filter(
                    CustomMeeting.meeting_id == meeting_id
                ).first()
                
                if meeting:
                    meeting.status = "ended"
                    meeting.ended_at = datetime.utcnow()
                    db.commit()
            
            # Закрываем AI Agent соединение
            if meeting_id in self.ai_agent_connections:
                ai_ws = self.ai_agent_connections[meeting_id]
                await ai_ws.close()
                del self.ai_agent_connections[meeting_id]
            
            # Очищаем WebSocket соединения
            if meeting_id in self.websocket_connections:
                del self.websocket_connections[meeting_id]
            
            logger.info(f"Meeting {meeting_id} ended")
            
        except Exception as e:
            logger.error(f"Error ending meeting: {e}")
    
    async def get_meeting_info(self, meeting_id: str) -> Optional[Dict]:
        """Получает информацию о встрече"""
        try:
            meeting_data = self.redis.hgetall(f"meeting:{meeting_id}")
            if not meeting_data:
                return None
            
            participants = self.redis.smembers(f"meeting:{meeting_id}:participants")
            meeting_data["participants_count"] = len(participants)
            meeting_data["participants"] = list(participants)
            
            return meeting_data
            
        except Exception as e:
            logger.error(f"Error getting meeting info: {e}")
            return None
    
    async def handle_audio_data(self, meeting_id: str, audio_data: str, user_id: int, timestamp: float = None):
        """Обрабатывает аудио данные от участника"""
        try:
            import time
            # Используем переданный timestamp или текущее время
            if timestamp is None:
                timestamp = time.time()
            
            logger.info(f"🎤 Received audio data from user {user_id} in meeting {meeting_id}, size: {len(audio_data)} chars, timestamp: {timestamp}")
            
            # Пересылаем аудио данные AI агенту
            if meeting_id in self.ai_agent_connections:
                ai_ws = self.ai_agent_connections[meeting_id]
                message = {
                    "type": "audio_data",
                    "audio_data": audio_data,
                    "user_id": user_id,
                    "timestamp": timestamp  # Используем числовой timestamp
                }
                await ai_ws.send(json.dumps(message))
                logger.info(f"✅ Forwarded audio data to AI agent for meeting {meeting_id}")
            else:
                logger.warning(f"⚠️ No AI agent connection found for meeting {meeting_id}")
            
            # Пересылаем аудио другим участникам (опционально)
            await self._notify_participants(meeting_id, {
                "type": "audio_data",
                "audio_data": audio_data,
                "user_id": user_id,
                "timestamp": timestamp
            })
            
        except Exception as e:
            logger.error(f"❌ Error handling audio data: {e}", exc_info=True)
    
    async def handle_voice_message(self, meeting_id: str, audio_data: str, user_id: int):
        """Обрабатывает голосовое сообщение от участника"""
        try:
            import time
            t1 = time.time()
            logger.info(f"[LATENCY] t1: backend received voice message at {t1}, from user {user_id} in meeting {meeting_id}, size: {len(audio_data)} chars")
            
            # Пересылаем голосовое сообщение AI агенту
            if meeting_id in self.ai_agent_connections:
                t2 = time.time()
                ai_ws = self.ai_agent_connections[meeting_id]
                message = {
                    "type": "voice_message",
                    "audio_data": audio_data,
                    "user_id": user_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                await ai_ws.send(json.dumps(message))
                t2_end = time.time()
                logger.info(f"[LATENCY] t2: forwarded to AI agent at {t2_end}, delta t2-t1: {t2_end-t1:.3f}s")
                logger.info(f"Forwarded voice message to AI agent for meeting {meeting_id}")
            else:
                logger.warning(f"No AI agent connection found for meeting {meeting_id}")
                # Уведомляем пользователя, что ИИ-агент недоступен
                await self._notify_participants(meeting_id, {
                    "type": "ai_agent_error",
                    "message": "ИИ-агент недоступен. Запустите ИИ-агента для отправки голосовых сообщений.",
                    "timestamp": datetime.utcnow().isoformat()
                })
        except Exception as e:
            logger.error(f"Error handling voice message: {e}")
    
    async def handle_chat_message(self, meeting_id: str, message: str, user_id: int):
        """Обрабатывает текстовые сообщения в чате"""
        try:
            await self._notify_participants(meeting_id, {
                "type": "chat_message",
                "message": message,
                "user_id": user_id,
                "timestamp": datetime.utcnow().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error handling chat message: {e}")


# Глобальный экземпляр сервиса
webrtc_service = WebRTCMeetingService()
