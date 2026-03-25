import os
import asyncio
import logging
import base64
import time
from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import uvicorn
import json

from config import settings
from services.stt_service import STTService
from services.llm_service import LLMService
from services.tts_service import TTSService
from services.zoom_client import ZoomClient
from services.websocket_client import ws_client
from pipeline.audio_pipeline import AudioPipeline
from routers.tts_proxy import router as tts_router
from services.pii_redactor import redact_pii

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Agent Service", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене ограничить
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация сервисов
stt_service = STTService()
llm_service = LLMService()
tts_service = TTSService()
zoom_client = ZoomClient()
audio_pipeline = AudioPipeline(stt_service, llm_service, tts_service, zoom_client)

# Подключаем API для низколатентного TTS прокси
app.include_router(tts_router, prefix="/api")

async def stream_tts_and_send(websocket: WebSocket, meeting_id: str, text: str):
    """Стримит озвучку текста порциями аудио для минимальной задержки.
       Гарантирует, что НЕ будет отправлен ai_agent_response, если хотя бы один chunk был отправлен."""
    try:
        import base64
        import time
        # Разбиваем текст на короткие фразы
        import re
        sentences = [s.strip() for s in re.split(r"([.!?]+\s+)", text)]
        # Склеиваем, сохраняя разделители, чтобы не потерять пунктуацию
        merged = []
        for i in range(0, len(sentences), 2):
            part = sentences[i]
            if i + 1 < len(sentences):
                part += sentences[i + 1]
            if part:
                merged.append(part)

        # Очередь входных текстов для TTS
        text_queue: asyncio.Queue = asyncio.Queue()
        for chunk in merged if merged else [text]:
            await text_queue.put(chunk)
        await text_queue.put(None)  # сигнал окончания

        # Запускаем стриминг в TTS
        tts_start_time = time.time()
        logger.info(f"[TTS_PERF] Starting TTS synthesis for text (length: {len(text)}) at {tts_start_time}")
        
        audio_queue, task = await tts_service.synthesize_stream(
            text_queue,
            voice_id=settings.elevenlabs_voice_id,
            language="ru"
        )

        stream_sent = False
        last_chunk_ts = None
        first_chunk_received = False

        # Читаем аудио-чанки и отправляем в WebSocket
        while True:
            try:
                audio_item = await asyncio.wait_for(audio_queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                break
            if not audio_item:
                break
            audio_bytes = audio_item.get("audio")
            if not audio_bytes:
                continue

            # Отметка — отправляемые чанки
            stream_sent = True
            last_chunk_ts = time.time()
            
            if not first_chunk_received:
                first_chunk_received = True
                first_chunk_delta = last_chunk_ts - tts_start_time
                logger.info(f"[TTS_PERF] First chunk received after {first_chunk_delta:.3f}s (text length: {len(text)})")
            
            logger.info(f"SENT CHUNK for meeting {meeting_id} at {last_chunk_ts} (chunk size: {len(audio_bytes)} bytes)")

            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            await websocket.send_text(json.dumps({
                "type": "ai_agent_audio_chunk",
                "meeting_id": meeting_id,
                "audio_data": audio_b64,
                "timestamp": last_chunk_ts
            }))

        # Отменяем задачу на всякий случай
        try:
            task.cancel()
        except Exception:
            pass

        # Если был хотя бы один chunk — отправляем ai_agent_audio_end и НЕ делаем fallback
        if stream_sent:
            await websocket.send_text(json.dumps({
                "type": "ai_agent_audio_end",
                "meeting_id": meeting_id,
                "timestamp": asyncio.get_event_loop().time()
            }))
            logger.info(f"Stream completed successfully for meeting {meeting_id}, no fallback needed")
            return

        # Если сюда дошли — стрим не дал чанков — делаем fallback (единственный путь для ai_agent_response)
        logger.info(f"TTS stream produced no chunks for meeting {meeting_id}, using fallback batch synth")
        try:
            audio_data = await tts_service.synthesize_speech(text, settings.elevenlabs_voice_id)
            if audio_data:
                audio_b64 = base64.b64encode(audio_data).decode("utf-8")
                await websocket.send_text(json.dumps({
                    "type": "ai_agent_response",
                    "meeting_id": meeting_id,
                    "text": text,
                    "audio_data": audio_b64,
                    "timestamp": time.time()
                }))
                logger.info(f"SENT FALLBACK ai_agent_response for meeting {meeting_id}")
        except Exception as e2:
            logger.exception(f"Fallback synthesis failed for meeting {meeting_id}: {e2}")

    except Exception as e:
        logger.exception(f"Streaming TTS failed for meeting {meeting_id}: {e}")
        # На ошибки не шлём дубли — просто логируем

# Модели данных
class MeetingStartRequest(BaseModel):
    meeting_id: str
    user_id: int
    topic: str

class MeetingStartWithGreetingRequest(BaseModel):
    meeting_id: str
    user_name: str
    duration_minutes: int
    greeting_message: str

class AgentStartRequest(BaseModel):
    meeting_id: str
    user_name: str
    duration_minutes: int

class MeetingEndRequest(BaseModel):
    meeting_id: str
    user_id: int

class AudioChunkRequest(BaseModel):
    meeting_id: str
    audio_data: bytes
    timestamp: float

# Глобальное состояние активных встреч
active_meetings: Dict[str, Dict[str, Any]] = {}


@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске"""
    logger.info("AI Agent Service starting up...")
    
    # Подключаемся к SDK Runner через WebSocket
    try:
        await ws_client.connect()
        logger.info("Connected to SDK Runner WebSocket")
    except Exception as e:
        logger.error(f"Failed to connect to SDK Runner: {e}")
    
    # Проверяем доступность внешних сервисов
    try:
        await stt_service.health_check()
        await llm_service.health_check()
        # Временно отключаем проверку TTS для отладки
        # await tts_service.health_check()
        logger.info("All external services are available")
    except Exception as e:
        logger.error(f"Service health check failed: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Очистка при завершении"""
    logger.info("AI Agent Service shutting down...")
    
    # Завершаем все активные встречи
    for meeting_id in list(active_meetings.keys()):
        await end_meeting_internal(meeting_id)
    
    # Отключаемся от WebSocket
    try:
        await ws_client.disconnect()
        logger.info("Disconnected from SDK Runner WebSocket")
    except Exception as e:
        logger.error(f"Error disconnecting from WebSocket: {e}")


@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "service": "ai_agent",
        "active_meetings": len(active_meetings)
    }


@app.post("/meetings/start")
async def start_meeting(
    request: MeetingStartRequest,
    background_tasks: BackgroundTasks
):
    """Запускает встречу с ИИ-агентом"""
    try:
        meeting_id = request.meeting_id
        
        if meeting_id in active_meetings:
            raise HTTPException(status_code=400, detail="Meeting already active")
        
        # Подключаемся к Zoom встрече
        await zoom_client.connect_to_meeting(meeting_id)
        
        # Запускаем аудио пайплайн в фоне
        background_tasks.add_task(
            audio_pipeline.start_processing,
            meeting_id,
            request.user_id
        )
        
        # Сохраняем информацию о встрече
        active_meetings[meeting_id] = {
            "user_id": request.user_id,
            "topic": request.topic,
            "start_time": asyncio.get_event_loop().time(),
            "status": "active"
        }
        
        logger.info(f"Started meeting {meeting_id} for user {request.user_id}")
        
        return {
            "message": "Meeting started successfully",
            "meeting_id": meeting_id,
            "status": "active"
        }
        
    except Exception as e:
        logger.error(f"Error starting meeting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/start")
async def start_agent(request: AgentStartRequest):
    """Запускает ИИ-агента для встречи"""
    try:
        meeting_id = request.meeting_id
        
        logger.info(f"🚀 Запускаем ИИ-агента для встречи {meeting_id}")
        
        # Сохраняем информацию о встрече
        active_meetings[meeting_id] = {
            "user_name": request.user_name,
            "duration_minutes": request.duration_minutes,
            "start_time": asyncio.get_event_loop().time(),
            "status": "active"
        }
        
        # Отправляем однократное приветствие (через локальный TTS → ElevenLabs)
        try:
            greeting_text = "Привет! Я твой ИИ-тренер. Готов помочь тебе с тренировкой!"
            greeting_audio = await tts_service.synthesize_speech(greeting_text, settings.elevenlabs_voice_id)
            if greeting_audio:
                await zoom_client.send_audio(meeting_id, greeting_audio)
            else:
                logger.warning("TTS вернул пустое аудио, приветствие не отправлено")
        except Exception as e:
            logger.error(f"Не удалось отправить приветствие: {e}")
        
        logger.info(f"✅ ИИ-агент успешно запущен для встречи {meeting_id}")
        
        return {
            "message": "AI Agent started successfully",
            "meeting_id": meeting_id,
            "status": "active",
            "greeting_sent": True
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска ИИ-агента: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Удалено бесконечное приветствие, чтобы бот не повторял приветственную фразу


@app.post("/meetings/start-with-greeting")
async def start_meeting_with_greeting(
    request: MeetingStartWithGreetingRequest,
    background_tasks: BackgroundTasks
):
    """Запускает встречу с ИИ-агентом и приветственным сообщением"""
    try:
        meeting_id = request.meeting_id
        
        if meeting_id in active_meetings:
            raise HTTPException(status_code=400, detail="Meeting already active")
        
        # Подключаемся к Zoom встрече
        await zoom_client.connect_to_meeting(meeting_id)
        
        # Запускаем аудио пайплайн в фоне
        background_tasks.add_task(
            audio_pipeline.start_processing,
            meeting_id,
            None  # user_id не нужен для этого типа встречи
        )
        
        # Сохраняем информацию о встрече
        active_meetings[meeting_id] = {
            "user_name": request.user_name,
            "duration_minutes": request.duration_minutes,
            "greeting_message": request.greeting_message,
            "start_time": asyncio.get_event_loop().time(),
            "status": "active"
        }
        
        # Отправляем приветственное сообщение через TTS
        background_tasks.add_task(
            send_greeting_message,
            meeting_id,
            request.greeting_message
        )
        
        logger.info(f"Started meeting {meeting_id} with greeting for {request.user_name}")
        
        return {
            "message": "Meeting started with greeting successfully",
            "meeting_id": meeting_id,
            "status": "active",
            "greeting_sent": True
        }
        
    except Exception as e:
        logger.error(f"Error starting meeting with greeting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def send_greeting_message(meeting_id: str, greeting_message: str):
    """Отправляет приветственное сообщение через TTS"""
    try:
        # Генерируем аудио из текста
        audio_data = await tts_service.synthesize_speech(greeting_message, settings.elevenlabs_voice_id)
        
        # Отправляем аудио в Zoom
        await zoom_client.send_audio(meeting_id, audio_data)
        
        logger.info(f"Greeting message sent for meeting {meeting_id}")
        
    except Exception as e:
        logger.error(f"Error sending greeting message: {e}")


@app.post("/meetings/end")
async def end_meeting(request: MeetingEndRequest):
    """Завершает встречу и генерирует отчет"""
    try:
        meeting_id = request.meeting_id
        
        if meeting_id not in active_meetings:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        # Завершаем встречу
        result = await end_meeting_internal(meeting_id)
        
        return {
            "message": "Meeting ended successfully",
            "meeting_id": meeting_id,
            "transcript": result.get("transcript"),
            "summary": result.get("summary")
        }
        
    except Exception as e:
        logger.error(f"Error ending meeting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/audio/chunk")
async def process_audio_chunk(request: AudioChunkRequest):
    """Обрабатывает аудио чанк от Zoom"""
    try:
        meeting_id = request.meeting_id
        
        if meeting_id not in active_meetings:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        # Добавляем аудио в очередь обработки
        await audio_pipeline.add_audio_chunk(
            meeting_id,
            request.audio_data,
            request.timestamp
        )
        
        return {"message": "Audio chunk processed"}
        
    except Exception as e:
        logger.error(f"Error processing audio chunk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def end_meeting_internal(meeting_id: str) -> Dict[str, Any]:
    """Внутренняя функция завершения встречи"""
    try:
        if meeting_id not in active_meetings:
            return {}
        
        meeting_info = active_meetings[meeting_id]
        
        # Останавливаем аудио пайплайн
        await audio_pipeline.stop_processing(meeting_id)
        
        # Отключаемся от Zoom
        await zoom_client.disconnect_from_meeting(meeting_id)
        
        # Генерируем финальный отчет
        transcript = await audio_pipeline.get_transcript(meeting_id)
        transcript = redact_pii(transcript)
        summary = await llm_service.generate_summary(transcript)
        
        # Удаляем из активных встреч
        del active_meetings[meeting_id]
        
        logger.info(f"Ended meeting {meeting_id}")
        
        return {
            "transcript": transcript,
            "summary": summary,
            "duration": asyncio.get_event_loop().time() - meeting_info["start_time"]
        }
        
    except Exception as e:
        logger.error(f"Error ending meeting internally: {e}")
        return {}


@app.get("/meetings/active")
async def get_active_meetings():
    """Получает список активных встреч"""
    return {
        "active_meetings": len(active_meetings),
        "meetings": [
            {
                "meeting_id": mid,
                "user_id": info["user_id"],
                "topic": info["topic"],
                "duration": asyncio.get_event_loop().time() - info["start_time"]
            }
            for mid, info in active_meetings.items()
        ]
    }


@app.websocket("/ws/{meeting_id}")
async def websocket_endpoint(websocket: WebSocket, meeting_id: str):
    """WebSocket endpoint для подключения к AI агенту"""
    await websocket.accept()
    logger.info(f"AI Agent WebSocket connected for meeting {meeting_id}")
    
    try:
        # Добавляем встречу в активные, если её нет
        if meeting_id not in active_meetings:
            active_meetings[meeting_id] = {
                "user_id": 1,  # Временная заглушка
                "topic": "WebRTC Meeting",
                "start_time": asyncio.get_event_loop().time(),
                "websocket": websocket
            }
            
            # Отправляем приветствие
            await send_ws_greeting_message(websocket, meeting_id)
        
        # Слушаем сообщения от backend
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message["type"] == "audio_data":
                    # Обрабатываем аудио данные в реальном времени
                    await process_realtime_audio(
                        meeting_id,
                        message["audio_data"],
                        message.get("timestamp", asyncio.get_event_loop().time()),
                        websocket
                    )
                    
                    # Отправляем подтверждение
                    await websocket.send_text(json.dumps({
                        "type": "audio_received",
                        "meeting_id": meeting_id,
                        "timestamp": asyncio.get_event_loop().time()
                    }))
                    
                elif message["type"] == "voice_message":
                    # Обрабатываем голосовое сообщение
                    await process_voice_message(
                        meeting_id,
                        message["audio_data"],
                        message.get("user_id"),
                        websocket,
                        message
                    )
                    
                elif message["type"] == "ping":
                    # Отвечаем на ping
                    await websocket.send_text(json.dumps({
                        "type": "pong",
                        "timestamp": message.get("timestamp")
                    }))
                    
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON format"
                }))
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Internal server error"
                }))
                
    except WebSocketDisconnect:
        logger.info(f"AI Agent WebSocket disconnected for meeting {meeting_id}")
    except Exception as e:
        logger.error(f"WebSocket error for meeting {meeting_id}: {e}")
    finally:
            # Очищаем встречу при отключении
        if meeting_id in active_meetings:
            del active_meetings[meeting_id]


async def send_ws_greeting_message(websocket: WebSocket, meeting_id: str):
    """Отправляет приветственное сообщение от ИИ-агента"""
    try:
        # Генерируем приветственное сообщение
        greeting_text = "Привет! Я твой ИИ-тренер. Готов помочь тебе с тренировкой!"
        
        # Генерируем аудио приветствие
        greeting_audio = await tts_service.synthesize_speech(greeting_text, settings.elevenlabs_voice_id)
        
        # Кодируем аудио в base64 если оно есть
        audio_data = None
        if greeting_audio:
            import base64
            audio_data = base64.b64encode(greeting_audio).decode('utf-8')
        
        # Отправляем сообщение с аудио
        message = {
            "type": "ai_agent_response",
            "text": greeting_text,
            "audio_data": audio_data,
            "timestamp": asyncio.get_event_loop().time()
        }
        
        await websocket.send_text(json.dumps(message))
        logger.info(f"Sent WS greeting message for meeting {meeting_id}")
        
    except Exception as e:
        logger.error(f"Error sending greeting message: {e}")
        # Отправляем текстовое сообщение без аудио
        try:
            message = {
                "type": "ai_agent_response",
                "text": "Привет! Я твой ИИ-тренер. Готов помочь тебе с тренировкой!",
                "timestamp": asyncio.get_event_loop().time()
            }
            await websocket.send_text(json.dumps(message))
        except Exception as e2:
            logger.error(f"Error sending text-only greeting: {e2}")


# Глобальные переменные для управления состоянием встреч
meeting_states = {}
audio_buffers = {}
speech_detection_tasks = {}


async def process_voice_message(meeting_id: str, audio_data: str, user_id: int, websocket: WebSocket, message: dict = None):
    """Обрабатывает голосовое сообщение от пользователя"""
    try:
        logger.info(f"Processing voice message from user {user_id} in meeting {meeting_id}")
        
        # Декодируем аудио данные
        audio_bytes = base64.b64decode(audio_data)
        
        # Определяем расширение файла на основе типа аудио
        audio_type = message.get("audio_type", "audio/webm")
        if "wav" in audio_type:
            file_extension = "wav"
        elif "mp4" in audio_type:
            file_extension = "mp4"
        elif "ogg" in audio_type:
            file_extension = "ogg"
        else:
            file_extension = "webm"
        
        # Сохраняем аудио во временный файл
        temp_audio_path = f"/tmp/voice_message_{meeting_id}_{user_id}_{int(time.time())}.{file_extension}"
        with open(temp_audio_path, "wb") as f:
            f.write(audio_bytes)
        
        logger.info(f"Saved voice message to {temp_audio_path}, type: {audio_type}")
        
        try:
            # УСКОРЕНИЕ: пропускаем конвертацию через ffmpeg и сразу шлём оригинал в STT
            with open(temp_audio_path, "rb") as f:
                original_audio_data = f.read()
            transcription = await stt_service.transcribe_audio(original_audio_data, "ru")
            if transcription:
                transcription = redact_pii(transcription)

            logger.info(f"Voice message transcribed: {transcription}")
            
            if transcription and transcription.strip():
                # Стримим ответ ИИ по предложениям для минимальной задержки
                t0 = time.time()  # Логирование времени
                logger.info(f"[LATENCY] t0: voice message received at {t0}")
                
                meeting_info = active_meetings.get(meeting_id, {})
                meeting_topic = meeting_info.get("topic")
                
                t3 = time.time()
                logger.info(f"[LATENCY] t3: starting LLM stream at {t3}")
                
                # Используем стриминг LLM по предложениям
                full_text = ""
                first_sentence = True
                
                # Очередь для TTS обработки предложений (чтобы не блокировать получение следующих)
                tts_queue = asyncio.Queue()
                tts_processing = False
                
                async def process_tts_queue():
                    """Обрабатывает очередь TTS последовательно"""
                    nonlocal tts_processing
                    tts_processing = True
                    try:
                        while True:
                            item = await tts_queue.get()
                            if item is None:  # Сигнал окончания
                                break
                            
                            sentence_text, is_first = item
                            t5_start = time.time()
                            logger.info(f"[LATENCY] t5_start: TTS processing started for sentence at {t5_start}")
                            
                            # Для первого предложения используем минимальный чанк
                            if is_first:
                                words = sentence_text.split()
                                if len(words) > 2:
                                    short_phrase = " ".join(words[:2])
                                    await stream_tts_and_send(websocket, meeting_id, short_phrase)
                                    remaining = " ".join(words[2:])
                                    if remaining.strip():
                                        await stream_tts_and_send(websocket, meeting_id, remaining)
                                else:
                                    await stream_tts_and_send(websocket, meeting_id, sentence_text)
                            else:
                                await stream_tts_and_send(websocket, meeting_id, sentence_text)
                            
                            t5_end = time.time()
                            logger.info(f"[LATENCY] t5_end: TTS completed for sentence at {t5_end}, delta: {t5_end-t5_start:.3f}s")
                            tts_queue.task_done()
                    except Exception as e:
                        logger.error(f"Error in TTS queue processing: {e}")
                    finally:
                        tts_processing = False
                
                # Запускаем обработчик очереди TTS в фоне
                tts_task = asyncio.create_task(process_tts_queue())
                
                async for sentence in llm_service.generate_response_stream(
                    user_message=transcription,
                    conversation_context=None,
                    meeting_topic=meeting_topic
                ):
                    if not sentence:
                        continue
                    
                    full_text += sentence + " "
                    
                    t4 = time.time()
                    logger.info(f"[LATENCY] t4: LLM sentence ready at {t4}, delta: {t4-t3:.3f}s")
                    
                    # Отправляем текст клиенту сразу
                    await websocket.send_text(json.dumps({
                        "type": "ai_agent_text",
                        "meeting_id": meeting_id,
                        "text": sentence,
                        "timestamp": t4
                    }))
                    
                    # Добавляем в очередь TTS (не блокируем получение следующего предложения)
                    await tts_queue.put((sentence, first_sentence))
                    if first_sentence:
                        first_sentence = False
                    
                    t5 = time.time()
                    logger.info(f"[LATENCY] t5: TTS queued for sentence at {t5}, delta: {t5-t4:.3f}s")
                
                # Сигнализируем об окончании
                await tts_queue.put(None)
                await tts_task  # Ждем завершения обработки очереди
                
                if full_text.strip():
                    logger.info(f"[LATENCY] Full response generated: {full_text.strip()}")
                else:
                    logger.error("Failed to generate AI response")
            else:
                logger.warning("Empty transcription from voice message")
                
        finally:
            # Удаляем временные файлы
            if os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
            if 'wav_audio_path' in locals() and os.path.exists(wav_audio_path):
                os.remove(wav_audio_path)
                
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")

async def process_realtime_audio(meeting_id: str, audio_data: str, timestamp: float, websocket: WebSocket):
    """Обрабатывает аудио данные в реальном времени для реактивного общения"""
    try:
        # Используем серверное время для всех сравнений (timestamp от клиента может быть относительным)
        server_time = asyncio.get_event_loop().time()
        
        # Логируем получение аудио данных
        logger.info(f"🎤 Received audio_data for meeting {meeting_id}, client_timestamp: {timestamp}, server_time: {server_time}, data_len: {len(audio_data) if audio_data else 0}")
        
        # Инициализируем состояние встречи если его нет
        if meeting_id not in meeting_states:
            meeting_states[meeting_id] = {
                "is_speaking": False,
                "is_ai_speaking": False,
                "last_audio_time": server_time,  # Используем серверное время
                "audio_buffer": [],
                "speech_detection_task": None,
                "ai_response_task": None
            }
            logger.info(f"Initialized meeting state for {meeting_id}")
        
        state = meeting_states[meeting_id]
        state["last_audio_time"] = server_time  # Используем серверное время для сравнений
        
        # Добавляем аудио в буфер
        if meeting_id not in audio_buffers:
            audio_buffers[meeting_id] = []
        
        # Сохраняем с серверным временем для правильного сравнения
        audio_buffers[meeting_id].append({
            "data": audio_data,
            "timestamp": server_time  # Используем серверное время
        })
        
        # Ограничиваем размер буфера (последние 10 секунд)
        audio_buffers[meeting_id] = [
            chunk for chunk in audio_buffers[meeting_id]
            if server_time - chunk["timestamp"] < 10.0
        ]
        
        # Проверяем, говорит ли пользователь
        # ВАЖНО: Не запускаем детекцию если уже обрабатывается ответ ИИ
        audio_chunks_count = len(audio_buffers.get(meeting_id, []))
        ai_task_running = state.get("ai_response_task") and not state["ai_response_task"].done()
        
        if not state["is_speaking"] and audio_chunks_count >= 3 and not ai_task_running:
            # Запускаем детекцию речи только если:
            # 1. Пользователь еще не говорит (is_speaking = False)
            # 2. Есть достаточно чанков (минимум 3 = ~300ms)
            # 3. Не идет обработка ответа ИИ
            state["is_speaking"] = True
            logger.info(f"User started speaking in meeting {meeting_id} (chunks: {audio_chunks_count}, ai_task_running: {ai_task_running})")
            
            # Если ИИ говорит, прерываем его
            if state["is_ai_speaking"]:
                await interrupt_ai_speech(meeting_id, websocket)
            
            # Запускаем задачу детекции окончания речи (отменяем предыдущую если есть)
            if state.get("speech_detection_task") and not state["speech_detection_task"].done():
                state["speech_detection_task"].cancel()
            
            state["speech_detection_task"] = asyncio.create_task(
                detect_speech_end(meeting_id, websocket)
            )
        
        # Обновляем время последнего аудио (уже обновлено выше с server_time)
        
    except Exception as e:
        logger.error(f"Error processing realtime audio: {e}")


async def detect_speech_end(meeting_id: str, websocket: WebSocket):
    """Детектирует окончание речи пользователя с улучшенным VAD для ChatGPT Voice режима"""
    try:
        state = meeting_states.get(meeting_id)
        if not state:
            return
        
        # Более агрессивная детекция для быстрой реакции (как в ChatGPT Voice)
        # Используем 700ms тишины для надежности (<1 секунды как просил пользователь)
        silence_threshold = 0.7  # 700ms тишины для надежности и скорости
        
        # Проверяем паузу тишины с частыми проверками
        check_interval = 0.05  # Проверяем каждые 50ms для быстрой реакции
        total_wait = 0.0
        max_wait = 3.0  # Максимум 3 секунды ожидания
        
        while total_wait < max_wait:
            await asyncio.sleep(check_interval)
            total_wait += check_interval
            
            # Проверяем, не было ли новых аудио данных
            current_time = asyncio.get_event_loop().time()
            time_since_last_audio = current_time - state["last_audio_time"]
            
            # Если прошло больше threshold - пользователь закончил говорить
            if time_since_last_audio >= silence_threshold:
                # Проверяем, есть ли аудио для обработки
                audio_chunks = audio_buffers.get(meeting_id, [])
                if not audio_chunks or len(audio_chunks) < 5:
                    # Слишком мало данных - пропускаем (нужно минимум 5 чанков = ~500ms речи)
                    logger.info(f"⚠️ [DETECT] Not enough audio chunks ({len(audio_chunks)} < 5), skipping processing")
                    state["is_speaking"] = False
                    return
                
                state["is_speaking"] = False
                logger.info(f"✅ [DETECT] User finished speaking in meeting {meeting_id} (silence: {time_since_last_audio:.3f}s, total_wait: {total_wait:.3f}s, chunks: {len(audio_chunks)})")
                
                # Обрабатываем накопленное аудио
                await process_accumulated_audio(meeting_id, websocket)
                return
        
        # Если прошло слишком много времени - принудительно обрабатываем только если есть данные
        # ВАЖНО: Проверяем, не идет ли уже обработка ответа ИИ
        state["is_speaking"] = False
        audio_chunks = audio_buffers.get(meeting_id, [])
        
        # Не обрабатываем если уже идет обработка ответа ИИ
        if state.get("ai_response_task") and not state["ai_response_task"].done():
            logger.info(f"⚠️ [DETECT] Speech detection timeout but AI response task already running, skipping")
            return
        
        if audio_chunks and len(audio_chunks) >= 5:
            logger.warning(f"⚠️ [DETECT] Speech detection timeout for meeting {meeting_id}, processing accumulated audio (chunks: {len(audio_chunks)})")
            await process_accumulated_audio(meeting_id, websocket)
        else:
            logger.info(f"⚠️ [DETECT] Speech detection timeout but no audio to process (chunks: {len(audio_chunks)})")
        
    except asyncio.CancelledError:
        logger.info(f"Speech detection cancelled for meeting {meeting_id}")
    except Exception as e:
        logger.error(f"Error detecting speech end: {e}")


async def process_accumulated_audio(meeting_id: str, websocket: WebSocket):
    """Обрабатывает накопленное аудио и генерирует ответ ИИ"""
    try:
        logger.info(f"🎯 [PROCESS] Starting to process accumulated audio for meeting {meeting_id}")
        
        state = meeting_states.get(meeting_id)
        if not state:
            logger.warning(f"⚠️ [PROCESS] No state found for meeting {meeting_id}")
            return
            
        audio_chunks = audio_buffers.get(meeting_id, [])
        if not audio_chunks:
            logger.warning(f"⚠️ [PROCESS] No audio chunks in buffer for meeting {meeting_id}")
            return
        
        logger.info(f"✅ [PROCESS] Found {len(audio_chunks)} audio chunks in buffer for meeting {meeting_id}")
        
        # ВАЖНО: Защита от множественных вызовов - не запускаем новую задачу если уже идет обработка
        if state.get("ai_response_task") and not state["ai_response_task"].done():
            logger.info(f"⚠️ [PROCESS] AI response task already running for meeting {meeting_id}, skipping new task")
            return  # Не запускаем новую задачу - ждем завершения текущей
        
        # Запускаем новую задачу ответа
        logger.info(f"🚀 [PROCESS] Starting new AI response task for meeting {meeting_id}")
        state["ai_response_task"] = asyncio.create_task(
            generate_ai_response(meeting_id, websocket)
        )
        logger.info(f"✅ [PROCESS] AI response task started for meeting {meeting_id}")
        
    except Exception as e:
        logger.error(f"❌ [PROCESS] Error processing accumulated audio: {e}", exc_info=True)


async def generate_ai_response_for_voice_message(meeting_id: str, user_text: str, user_id: int) -> Optional[str]:
    """Генерирует ответ ИИ на голосовое сообщение"""
    try:
        # Получаем тему встречи
        meeting_info = active_meetings.get(meeting_id, {})
        meeting_topic = meeting_info.get("topic")
        
        # Пытаемся сгенерировать ответ через LLM
        response = await llm_service.generate_response(
            user_message=user_text,
            conversation_context=None,
            meeting_topic=meeting_topic
        )
        if response:
            logger.info(f"Generated LLM response for voice message: {response}")
        return response
        
        # Фолбэк: простая контекстная логика, если LLM недоступен
        text_lower = user_text.lower()
        if any(kw in text_lower for kw in ["как дела", "как ты", "как у тебя"]):
            return "Спасибо, у меня все отлично! Готов приступить к тренировке. Чем помочь?"
        if any(kw in text_lower for kw in ["что будем тренировать", "что ты будешь тренировать", "что тренировать"]):
            return "Предлагаю начать с отработки возражений и структуры короткого питча. Как звучит?"
        return "Готов помочь. Расскажи, что хочешь улучшить: питч, обработку возражений или презентацию?"
            
    except Exception as e:
        logger.error(f"Error generating AI response for voice message: {e}")
        return None


async def generate_ai_response(meeting_id: str, websocket: WebSocket):
    """Генерирует ответ ИИ на основе накопленного аудио"""
    try:
        logger.info(f"🎯 [GENERATE] Starting AI response generation for meeting {meeting_id}")
        
        state = meeting_states.get(meeting_id)
        if not state:
            logger.warning(f"⚠️ [GENERATE] No state found for meeting {meeting_id}")
            return
        
        # Получаем накопленное аудио
        audio_chunks = audio_buffers.get(meeting_id, [])
        if not audio_chunks:
            logger.warning(f"⚠️ [GENERATE] No audio chunks found for meeting {meeting_id}")
            return
        
        logger.info(f"✅ [GENERATE] Found {len(audio_chunks)} audio chunks, total size: {sum(len(chunk.get('data', '')) for chunk in audio_chunks)} chars")
        
        # Объединяем аудио данные
        logger.info(f"🔄 [GENERATE] Combining audio chunks...")
        combined_audio = b"".join([
            base64.b64decode(chunk["data"]) for chunk in audio_chunks
        ])
        logger.info(f"✅ [GENERATE] Combined audio size: {len(combined_audio)} bytes")
        
        # Очищаем буфер
        audio_buffers[meeting_id] = []
        logger.info(f"🧹 [GENERATE] Cleared audio buffer for meeting {meeting_id}")
        
        # Конвертируем WebM в WAV для ElevenLabs STT (обязательно!)
        logger.info(f"🔄 [GENERATE] Converting WebM to WAV for STT (audio size: {len(combined_audio)} bytes)...")
        wav_audio = None
        
        try:
            import tempfile
            import subprocess
            import os
            
            # Создаем временный файл для WebM
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as webm_file:
                webm_file.write(combined_audio)
                webm_path = webm_file.name
            
            # Создаем временный файл для WAV
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as wav_file:
                wav_path = wav_file.name
            
            try:
                # Конвертируем через ffmpeg (обязательно для ElevenLabs STT)
                cmd = [
                    "ffmpeg", "-y", "-i", webm_path,
                    "-ac", "1",  # моно
                    "-ar", "16000",  # 16kHz
                    "-f", "wav",
                    wav_path
                ]
                
                logger.info(f"🔄 [GENERATE] Running ffmpeg: {' '.join(cmd)}")
                # Используем asyncio.to_thread для неблокирующего выполнения ffmpeg
                # Обертка для передачи timeout
                def run_ffmpeg():
                    return subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=10.0
                    )
                
                result = await asyncio.to_thread(run_ffmpeg)
                
                if result.returncode != 0:
                    error_msg = result.stderr.decode() if result.stderr else "Unknown ffmpeg error"
                    logger.error(f"❌ [GENERATE] FFmpeg conversion failed (returncode: {result.returncode}): {error_msg}")
                    # Fallback: попробуем Whisper с оригинальным аудио
                    logger.warning(f"⚠️ [GENERATE] Using original audio as fallback")
                    wav_audio = combined_audio
                else:
                    # Читаем конвертированный WAV
                    with open(wav_path, "rb") as f:
                        wav_audio = f.read()
                    logger.info(f"✅ [GENERATE] Successfully converted to WAV: {len(wav_audio)} bytes")
                    
            finally:
                # Удаляем временные файлы
                for path in [webm_path, wav_path]:
                    try:
                        if os.path.exists(path):
                            os.unlink(path)
                    except Exception as e:
                        logger.warning(f"⚠️ [GENERATE] Failed to delete temp file {path}: {e}")
        
        except Exception as e:
            logger.error(f"❌ [GENERATE] Audio conversion failed: {e}", exc_info=True)
            # Fallback: используем исходные данные
            wav_audio = combined_audio
        
        if not wav_audio:
            logger.error(f"❌ [GENERATE] No audio data after conversion!")
            return
        
        # Преобразуем речь в текст
        logger.info(f"🎤 [GENERATE] Starting STT transcription...")
        user_text = await stt_service.transcribe_audio(wav_audio)
        if not user_text or len(user_text.strip()) < 2:
            logger.warning(f"⚠️ [GENERATE] No meaningful speech detected in meeting {meeting_id}, text: '{user_text}'")
            return
        
        logger.info(f"✅ [GENERATE] User said: {user_text}")
        
        # Генерируем ответ ИИ
        meeting_topic = active_meetings.get(meeting_id, {}).get("topic")
        ai_response = await llm_service.generate_response(
            user_text,
            conversation_context=None,
            meeting_topic=meeting_topic
        )
        
        if ai_response:
            # Отправляем текст (для сабов) и стримим аудио порциями
            await websocket.send_text(json.dumps({
                "type": "ai_agent_text",
                "text": ai_response,
                "timestamp": asyncio.get_event_loop().time()
            }))
            state["is_ai_speaking"] = True
            await stream_tts_and_send(websocket, meeting_id, ai_response)
            logger.info(f"AI responded (streamed): {ai_response}")
            asyncio.create_task(reset_ai_speaking_flag(meeting_id, 5.0))
        
    except Exception as e:
        logger.error(f"Error generating AI response: {e}")


async def interrupt_ai_speech(meeting_id: str, websocket: WebSocket):
    """Прерывает речь ИИ-агента"""
    try:
        state = meeting_states.get(meeting_id)
        if not state:
            return
        
        # Отменяем текущую задачу ответа ИИ
        if state["ai_response_task"]:
            state["ai_response_task"].cancel()
            state["ai_response_task"] = None
        
        # Отправляем сигнал прерывания
        message = {
            "type": "ai_agent_interrupted",
            "timestamp": asyncio.get_event_loop().time()
        }
        
        await websocket.send_text(json.dumps(message))
        state["is_ai_speaking"] = False
        logger.info(f"AI speech interrupted in meeting {meeting_id}")
        
    except Exception as e:
        logger.error(f"Error interrupting AI speech: {e}")


async def reset_ai_speaking_flag(meeting_id: str, delay: float):
    """Сбрасывает флаг речи ИИ через указанную задержку"""
    try:
        await asyncio.sleep(delay)
        state = meeting_states.get(meeting_id)
        if state:
            state["is_ai_speaking"] = False
            logger.info(f"AI speaking flag reset for meeting {meeting_id}")
    except Exception as e:
        logger.error(f"Error resetting AI speaking flag: {e}")


@app.get("/voices")
async def get_voices():
    voices = await tts_service.get_available_voices()
    # Диагностика: все env-переменные связанные с elevenlabs и openai
    env_vars = {k: v for k, v in os.environ.items() if "ELEVEN" in k or "OPENAI" in k}
    return {
        "voices": voices,
        "current_voice_id": settings.elevenlabs_voice_id,
        "current_model_id": settings.elevenlabs_model_id,
        "current_api_key_short": settings.elevenlabs_api_key[:5] + "..." if settings.elevenlabs_api_key else None,
        "from_env": env_vars,
        "configured": bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id)
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info"
    )
