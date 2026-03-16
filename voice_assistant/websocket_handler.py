"""
WebSocket обработчик для Azure Voice Live API.
Проксирует соединение между клиентом и Azure Voice Live API.
"""

import asyncio
import base64
import json
import logging
import numpy as np
from datetime import datetime
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from .session_manager import get_session_manager, UserSession
from .db_service import VoiceTrainingDBService
from .config import (
    USE_AZURE_VOICE_LIVE,
    AZURE_VOICE_LIVE_ENDPOINT,
    AZURE_VOICE_LIVE_API_KEY,
    AZURE_VOICE_LIVE_MODEL,
    AZURE_VOICE_LIVE_API_VERSION,
    AZURE_VOICE_LIVE_VOICE,
    AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
    AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE,
    SYSTEM_PROMPT
)
from .azure_voice_live import AzureVoiceLiveConnection, get_azure_token

logger = logging.getLogger(__name__)


async def handle_websocket_connection(
    websocket: WebSocket,
    user_id: int,
    training_id: int,
    db: Session
):
    """
    Обрабатывает WebSocket подключение для голосовой тренировки с Azure Voice Live API.
    
    Args:
        websocket: WebSocket соединение
        user_id: ID пользователя
        training_id: ID тренировки
        db: Сессия БД
    """
    session_manager = get_session_manager()
    user_session: Optional[UserSession] = None
    azure_connection: Optional[AzureVoiceLiveConnection] = None
    
    try:
        # Проверяем, используется ли Azure Voice Live
        if not USE_AZURE_VOICE_LIVE:
            error_msg = (
                "⚠️ Azure Voice Live API не настроен.\n\n"
                "Для работы голосовой тренировки необходимо:\n"
                "1. Установить USE_AZURE_VOICE_LIVE=true в .env файле\n"
                "2. Установить AZURE_VOICE_LIVE_ENDPOINT (URL вашего Azure ресурса)\n"
                "3. Установить AZURE_VOICE_LIVE_API_KEY (API ключ Azure)\n\n"
                "Или установите USE_AZURE_VOICE_LIVE=false для использования локального режима."
            )
            logger.error(error_msg)
            await websocket.send_json({
                "type": "error",
                "message": error_msg
            })
            await websocket.close(code=1008, reason="Azure Voice Live not configured")
            return
        
        if not AZURE_VOICE_LIVE_ENDPOINT:
            error_msg = (
                "⚠️ AZURE_VOICE_LIVE_ENDPOINT не настроен.\n\n"
                "Установите переменную окружения AZURE_VOICE_LIVE_ENDPOINT в .env файле.\n"
                "Пример: AZURE_VOICE_LIVE_ENDPOINT=https://your-resource.cognitiveservices.azure.com/"
            )
            logger.error(error_msg)
            await websocket.send_json({
                "type": "error",
                "message": error_msg
            })
            await websocket.close(code=1008, reason="Azure endpoint not configured")
            return
        
        # Создаём изолированную сессию для пользователя
        user_session = await session_manager.create_session(user_id, training_id)
        
        if not user_session:
            await websocket.send_json({
                "type": "error",
                "message": "⚠️ Достигнут лимит одновременных пользователей. Попробуйте позже."
            })
            await websocket.close(code=1008, reason="Server capacity reached")
            return
        
        # Создаём запись в БД
        db_session_id = await VoiceTrainingDBService.create_training_session(
            db, user_id, training_id, user_session.session_id
        )
        user_session.db_session_id = db_session_id
        user_session.websocket = websocket
        
        logger.info(f"✅ Пользователь {user_id} подключён к тренировке {training_id}, session={user_session.session_id}")
        
        # Получаем Azure токен (если не используется API key)
        azure_token = None
        if not AZURE_VOICE_LIVE_API_KEY:
            logger.info("API ключ не указан, пытаемся получить Azure AD токен")
            azure_token = await get_azure_token(AZURE_VOICE_LIVE_ENDPOINT)
            if not azure_token:
                await websocket.send_json({
                    "type": "error",
                    "message": "⚠️ Не удалось получить Azure токен. Проверьте настройки Azure."
                })
                await websocket.close(code=1008, reason="Azure authentication failed")
                return
            logger.info("✅ Azure AD токен получен")
        else:
            logger.info("Используется API ключ для аутентификации")
            if len(AZURE_VOICE_LIVE_API_KEY) < 10:
                logger.warning(f"⚠️ API ключ слишком короткий ({len(AZURE_VOICE_LIVE_API_KEY)} символов), возможно неправильный")
        
        # Подключаемся к Azure Voice Live API
        logger.info(f"🔌 Подключение к Azure Voice Live: endpoint={AZURE_VOICE_LIVE_ENDPOINT[:50]}..., model={AZURE_VOICE_LIVE_MODEL}")
        try:
            azure_connection = AzureVoiceLiveConnection(
                endpoint=AZURE_VOICE_LIVE_ENDPOINT,
                api_key=AZURE_VOICE_LIVE_API_KEY,
                token=azure_token,
                api_version=AZURE_VOICE_LIVE_API_VERSION,
                model=AZURE_VOICE_LIVE_MODEL
            )
            
            logger.info("📡 Вызываю azure_connection.connect()...")
            await azure_connection.connect()
            logger.info("✅ Azure Voice Live подключен успешно")
        except ConnectionError as e:
            logger.error(f"❌ Ошибка подключения к Azure: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Не удалось подключиться к Azure Voice Live API: {str(e)}"
            })
            await websocket.close(code=1011, reason="Azure connection failed")
            return
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при подключении к Azure: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Ошибка подключения к Azure Voice Live API: {str(e)}"
            })
            await websocket.close(code=1011, reason="Azure connection error")
            return
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Не удалось подключиться к Azure: {str(e)}"
            })
            await websocket.close(code=1008, reason="Azure connection failed")
            return
        except Exception as e:
            logger.error(f"Неожиданная ошибка подключения к Azure: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Ошибка подключения к Azure: {str(e)}"
            })
            await websocket.close(code=1011, reason="Azure connection error")
            return
        
        # Отправляем конфигурацию сессии
        logger.info("Отправка конфигурации сессии в Azure...")
        await azure_connection.send_session_update(
            instructions=SYSTEM_PROMPT,
            voice_name=AZURE_VOICE_LIVE_VOICE,
            transcription_model=AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
            transcription_language=AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE
        )
        logger.info("✅ Конфигурация сессии отправлена в Azure")
        
        # Отправляем подтверждение подключения
        await websocket.send_json({
            "type": "connected",
            "session_id": user_session.session_id,
            "message": "✅ Подключение установлено"
        })
        logger.info("✅ Подтверждение подключения отправлено клиенту")
        
        # Запускаем задачу для получения сообщений от Azure
        azure_receive_task = asyncio.create_task(
            receive_from_azure(azure_connection, websocket, user_session, db)
        )
        
        # Основной цикл обработки сообщений от клиента
        try:
            async for message in websocket.iter_text():
                try:
                    data = json.loads(message) if message else {}
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"⚠️ Невалидный JSON: {message[:100]}")
                    continue
                
                msg_type = data.get("type")
                
                if msg_type == "input_audio_buffer.append":
                    # Получили аудио чанк в формате input_audio_buffer.append (как в оригинале)
                    audio_base64 = data.get("audio", "")
                    if audio_base64:
                        logger.debug(f"📤 Получен аудио чанк от клиента (размер base64: {len(audio_base64)})")
                        try:
                            # Проверяем что соединение с Azure активно
                            if not azure_connection.is_connected:
                                logger.warning("Соединение с Azure разорвано, пропускаем аудио")
                                await websocket.send_json({
                                    "type": "error",
                                    "message": "Соединение с Azure прервано. Переподключение..."
                                })
                                break
                            
                            # Клиент отправляет int16 PCM (AudioWorklet уже конвертировал)
                            # Просто передаем в Azure как есть
                            await azure_connection.send_audio(audio_base64)
                            logger.debug(f"Отправлен аудио чанк в Azure")
                        except ConnectionError as e:
                            logger.error(f"Соединение с Azure разорвано: {e}")
                            await websocket.send_json({
                                "type": "error",
                                "message": "Соединение с Azure прервано"
                            })
                            break
                        except Exception as e:
                            logger.error(f"Ошибка отправки аудио в Azure: {e}", exc_info=True)
                
                elif msg_type == "audio" or msg_type == "audio_data":
                    # Старый формат для обратной совместимости
                    audio_base64 = data.get("audio") or data.get("audio_data", "")
                    if audio_base64:
                        logger.debug(f"📤 Получен аудио чанк (старый формат, размер base64: {len(audio_base64)})")
                        try:
                            if not azure_connection.is_connected:
                                logger.warning("Соединение с Azure разорвано, пропускаем аудио")
                                break
                            
                            # Конвертируем если нужно (для обратной совместимости)
                            audio_bytes = base64.b64decode(audio_base64)
                            if len(audio_bytes) % 2 == 0:
                                # Вероятно int16
                                await azure_connection.send_audio(audio_base64)
                            else:
                                # Конвертируем float32 -> int16
                                audio_float32 = np.frombuffer(audio_bytes, dtype=np.float32)
                                audio_float32 = np.clip(audio_float32, -1.0, 1.0)
                                audio_int16 = np.round(audio_float32 * 32767).astype(np.int16)
                                audio_int16_base64 = base64.b64encode(audio_int16.tobytes()).decode('utf-8')
                                await azure_connection.send_audio(audio_int16_base64)
                        except Exception as e:
                            logger.error(f"Ошибка отправки аудио в Azure: {e}", exc_info=True)
                
                elif msg_type == "stop":
                    # Пользователь нажал стоп
                    await handle_stop(user_session, websocket, azure_connection)
                
                elif msg_type == "response.cancel":
                    # Клиент хочет прервать текущий ответ ИИ
                    response_id = data.get("response_id")
                    if response_id:
                        logger.info(f"⛔ Получен запрос на отмену ответа от клиента (response_id: {response_id})")
                        try:
                            # Отправляем response.cancel в Azure
                            await azure_connection.send_response_cancel(response_id)
                            logger.info(f"✅ Запрос на отмену отправлен в Azure для response_id: {response_id}")
                            
                            # Подтверждаем клиенту
                            await websocket.send_json({
                                "type": "status",
                                "status": "cancelling",
                                "response_id": response_id,
                                "message": "Прерывание ответа..."
                            })
                        except Exception as e:
                            logger.error(f"❌ Ошибка отправки response.cancel в Azure: {e}", exc_info=True)
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Не удалось прервать ответ: {str(e)}"
                            })
                    else:
                        logger.warning("⚠️ Получен response.cancel без response_id")
                        await websocket.send_json({
                            "type": "error",
                            "message": "response_id не указан для отмены"
                        })
                
                elif msg_type == "text":
                    # Текстовый запрос (для тестирования)
                    # Azure Voice Live не поддерживает текстовые запросы напрямую
                    # Можно отправить как аудио или пропустить
                    logger.warning("Текстовые запросы не поддерживаются Azure Voice Live API")
                
                elif msg_type == "end_session":
                    # Завершение сессии
                    await handle_end_session(user_session, websocket, db, azure_connection)
                    break
        
        except WebSocketDisconnect:
            logger.info(f"🔌 Пользователь {user_id} отключился")
        
        finally:
            # Отменяем задачу получения сообщений
            azure_receive_task.cancel()
            try:
                await azure_receive_task
            except asyncio.CancelledError:
                pass
    
    except Exception as e:
        logger.error(f"❌ Ошибка WebSocket для user_id={user_id}: {e}", exc_info=True)
        
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Ошибка сервера: {str(e)}"
            })
        except:
            pass
    
    finally:
        # Закрываем Azure соединение
        if azure_connection:
            await azure_connection.close()
        
        # Закрываем сессию
        if user_session:
            # Сохраняем в БД как прерванную если не завершена
            if user_session.db_session_id:
                await VoiceTrainingDBService.abort_training_session(db, user_session.db_session_id)
            
            await session_manager.close_session(user_session.session_id)
        
        try:
            await websocket.close()
        except:
            pass


async def receive_from_azure(
    azure_connection: AzureVoiceLiveConnection,
    websocket: WebSocket,
    user_session: UserSession,
    db: Session
):
    """
    Получает сообщения от Azure Voice Live API и отправляет их клиенту.
    
    Args:
        azure_connection: Соединение с Azure
        websocket: WebSocket соединение с клиентом
        user_session: Сессия пользователя
        db: Сессия БД
    """
    pending_user_transcript = ""
    current_response_id = None
    current_response_text = ""
    response_transcripts = {}
    
    try:
        while azure_connection.is_connected:
            try:
                message = await azure_connection.recv(timeout=0.5)
                if not message:
                    # Проверяем что соединение еще активно
                    if not azure_connection.is_connected:
                        logger.warning("Соединение с Azure разорвано в receive_from_azure")
                        break
                    continue
            except Exception as recv_error:
                logger.error(f"Ошибка получения сообщения от Azure: {recv_error}")
                if not azure_connection.is_connected:
                    break
                continue
            
            try:
                event = json.loads(message)
                event_type = event.get("type")
                
                # Логируем все события для отладки
                logger.info(f"📨 Получено событие от Azure: {event_type}")
                # Детальное логирование для важных событий
                if event_type in ["response.audio.delta", "response.audio.done", "response.created", "response.audio_transcript.delta", "response.audio_transcript.done"]:
                    logger.info(f"🔍 Детали события {event_type}: {json.dumps(event, indent=2, ensure_ascii=False)[:500]}")
                
                # Проверяем что WebSocket с клиентом еще открыт
                try:
                    # Простая проверка - пытаемся отправить пустое сообщение (не отправится, но проверит состояние)
                    pass  # Пропускаем проверку, так как она может быть дорогой
                except:
                    logger.warning("WebSocket с клиентом закрыт")
                    break
                
                if event_type == "session.created":
                    logger.info("✅ Сессия Azure создана")
                    # Проксируем событие клиенту
                    await websocket.send_json({
                        "type": "session.created"
                    })
                
                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("🎤 Обнаружена речь пользователя (возможное прерывание)")
                    # Проксируем событие напрямую клиенту (как в оригинале)
                    await websocket.send_json({
                        "type": "input_audio_buffer.speech_started"
                    })
                
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # Транскрипция речи пользователя завершена
                    user_transcript = event.get("transcript", "")
                    if user_transcript:
                        logger.info(f"📝 Распознано: '{user_transcript}'")
                        
                        # Отправляем клиенту
                        await websocket.send_json({
                            "type": "user_text",
                            "text": user_transcript
                        })
                        
                        # Сохраняем в БД
                        try:
                            await VoiceTrainingDBService.save_voice_message(
                                db, user_session.db_session_id, "user", user_transcript
                            )
                        except Exception as e:
                            logger.error(f"⚠️ Ошибка сохранения сообщения пользователя: {e}")
                        
                        pending_user_transcript = ""
                
                elif event_type == "conversation.item.input_audio_transcription.delta":
                    # Частичная транскрипция
                    delta = event.get("delta", "")
                    if delta:
                        pending_user_transcript += delta
                
                elif event_type == "response.created":
                    # ИИ начал генерировать ответ
                    response_id = event.get("response", {}).get("id") or event.get("response_id") or event.get("item_id")
                    current_response_id = response_id
                    current_response_text = ""
                    
                    logger.info(f"🤖 ИИ начал генерировать ответ (response_id: {response_id})")
                    
                    # Передаем response_id клиенту для возможности прерывания
                    await websocket.send_json({
                        "type": "status",
                        "status": "thinking",
                        "message": "🤔 Думаю...",
                        "response_id": response_id
                    })
                
                elif event_type == "response.audio_transcript.delta":
                    # Частичный текст ответа ИИ
                    delta = event.get("delta", "")
                    response_id = event.get("response_id") or event.get("item_id")
                    
                    if delta and response_id:
                        if response_id not in response_transcripts:
                            response_transcripts[response_id] = ""
                        response_transcripts[response_id] += delta
                        current_response_text = response_transcripts[response_id]
                
                elif event_type == "response.audio_transcript.done":
                    # Текст ответа ИИ завершён
                    response_id = event.get("response_id") or event.get("item_id")
                    final_text = response_transcripts.get(response_id, current_response_text)
                    
                    if final_text:
                        logger.info(f"💬 ИИ ответил: '{final_text}'")
                        logger.info(f"Ожидаем аудио для response_id: {response_id}")
                        
                        # Отправляем полный текст клиенту
                        await websocket.send_json({
                            "type": "ai_text",
                            "text": final_text
                        })
                        
                        # Сохраняем в БД
                        asyncio.create_task(_save_ai_response_async(
                            db, user_session.db_session_id, final_text, []
                        ))
                    else:
                        logger.warning(f"⚠️ response.audio_transcript.done без текста для response_id: {response_id}")
                
                elif event_type == "response.audio.delta":
                    # Аудио чанк ответа ИИ - проксируем напрямую (как в оригинале)
                    audio_data = event.get("delta", "")
                    response_id_for_audio = event.get("response_id") or event.get("item_id") or current_response_id
                    
                    logger.debug(f"🔊 Получен аудио чанк от Azure (длина: {len(audio_data) if audio_data else 0})")
                    if audio_data:
                        # Проксируем событие напрямую клиенту
                        await websocket.send_json({
                            "type": "response.audio.delta",
                            "delta": audio_data,
                            "response_id": response_id_for_audio,
                            "item_id": event.get("item_id")
                        })
                    else:
                        logger.warning("⚠️ Получен response.audio.delta без данных")
                
                elif event_type == "response.audio.done":
                    # Аудио ответа завершено - проксируем напрямую
                    response_id = event.get("response_id") or event.get("item_id") or current_response_id
                    logger.info(f"✅ Аудио ответа завершено (response_id: {response_id})")
                    
                    # Проксируем событие напрямую клиенту
                    await websocket.send_json({
                        "type": "response.audio.done",
                        "response_id": response_id,
                        "item_id": event.get("item_id")
                    })
                    
                    current_response_id = None
                
                elif event_type == "response.cancelled":
                    # Ответ был отменен - проксируем напрямую
                    response_id = event.get("response_id") or event.get("item_id") or current_response_id
                    logger.info(f"⛔ Ответ отменен (response_id: {response_id})")
                    
                    # Проксируем событие напрямую клиенту
                    await websocket.send_json({
                        "type": "response.cancelled",
                        "response_id": response_id,
                        "item_id": event.get("item_id")
                    })
                    
                    current_response_id = None
                
                elif event_type == "error":
                    # Ошибка от Azure
                    error = event.get("error", {})
                    error_message = error.get("message", "Неизвестная ошибка")
                    error_code = error.get("code", "unknown")
                    logger.error(f"❌ Ошибка от Azure: {error_code} - {error_message}")
                    logger.error(f"Полное событие ошибки: {json.dumps(event, indent=2)}")
                    
                    # Не критичные ошибки не прерывают соединение
                    if error_code in ["rate_limit", "quota_exceeded"]:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Ошибка Azure: {error_message}. Попробуйте позже."
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Ошибка Azure: {error_message}"
                        })
                
                else:
                    # Логируем неизвестные события для отладки
                    logger.info(f"⚠️ Неизвестное событие от Azure: {event_type}")
                    logger.debug(f"Полное событие: {json.dumps(event, indent=2)[:500]}")
            
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON от Azure: {e}, message: {message[:100]}")
            except Exception as e:
                logger.error(f"Ошибка обработки события от Azure: {e}", exc_info=True)
                # Продолжаем работу, не прерываем соединение
    
    except asyncio.CancelledError:
        logger.info("Задача получения сообщений от Azure отменена")
    except Exception as e:
        logger.error(f"Ошибка в receive_from_azure: {e}", exc_info=True)


async def _save_ai_response_async(
    db: Session,
    session_id: int,
    ai_response: str,
    conversation_history: list
):
    """
    Асинхронно сохраняет ответ ИИ в БД (не блокирует основной поток).
    """
    try:
        await VoiceTrainingDBService.save_voice_message(
            db, session_id, "assistant", ai_response
        )
        await VoiceTrainingDBService.update_conversation_history(
            db, session_id, conversation_history
        )
    except Exception as e:
        logger.error(f"⚠️ Ошибка сохранения ответа ИИ (асинхронно): {e}")


async def handle_stop(user_session: UserSession, websocket: WebSocket, azure_connection: Optional[AzureVoiceLiveConnection] = None):
    """
    Обрабатывает команду остановки.
    
    Args:
        user_session: Сессия пользователя
        websocket: WebSocket соединение
        azure_connection: Соединение с Azure (опционально)
    """
    logger.info(f"⏹️ Остановка (session={user_session.session_id})")
    
    # Отправляем команду отмены в Azure если есть активный ответ
    if azure_connection:
        try:
            await azure_connection.send({
                "type": "response.cancel",
                "event_id": ""
            })
        except:
            pass
    
    await websocket.send_json({
        "type": "stopped",
        "message": "Остановлено"
    })


async def handle_end_session(user_session: UserSession, websocket: WebSocket, db: Session, azure_connection: Optional[AzureVoiceLiveConnection] = None):
    """
    Обрабатывает завершение сессии тренировки.
    
    Args:
        user_session: Сессия пользователя
        websocket: WebSocket соединение
        db: Сессия БД
        azure_connection: Соединение с Azure (опционально)
    """
    if not user_session.db_session_id:
        return
    
    try:
        # Получаем историю из БД
        try:
            from models import VoiceTrainingMessage
        except ImportError:
            from app.models import VoiceTrainingMessage
        
        messages = db.query(VoiceTrainingMessage).filter(
            VoiceTrainingMessage.session_id == user_session.db_session_id
        ).all()
        
        user_responses = sum(1 for msg in messages if msg.role == "user")
        ai_questions = sum(1 for msg in messages if msg.role == "assistant")
        
        # Вычисляем длительность
        duration = int((datetime.utcnow() - user_session.created_at).total_seconds())
        
        # Сохраняем в БД
        await VoiceTrainingDBService.complete_training_session(
            db,
            user_session.db_session_id,
            duration,
            user_responses,
            ai_questions
        )
        
        logger.info(f"✅ Сессия {user_session.session_id} завершена: {duration}s, {user_responses} ответов")
        
        await websocket.send_json({
            "type": "session_ended",
            "duration": duration,
            "messages_count": len(messages)
        })
    
    except Exception as e:
        logger.error(f"❌ Ошибка завершения сессии: {e}")
