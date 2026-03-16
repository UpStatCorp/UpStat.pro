"""
Роутер для голосового ассистента.
Интегрируется в существующий FastAPI проект.

МАСШТАБИРУЕМАЯ ВЕРСИЯ с изолированными сессиями для каждого пользователя.
Поддерживает 100+ одновременных пользователей.
"""

import asyncio
import base64
import io
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path
from pydantic import BaseModel
from typing import Optional
import json
import logging
from datetime import datetime

# Импорты из нашего модуля
try:
    from .config import SAMPLE_RATE
    from .session_manager import get_session_manager, UserSession
    from .db_service import VoiceTrainingDBService
    from .vad import VAD
    from .stt_reactive import STTEngine
    from .gpt_logic import GPTDialogue
    from .tts_response import TTSEngine
except ImportError as e:
    logging.warning(f"Модули голосового ассистента не найдены: {e}")
    SAMPLE_RATE = 16000
    get_session_manager = None
    UserSession = None
    VoiceTrainingDBService = None
    VAD = None
    STTEngine = None
    GPTDialogue = None
    TTSEngine = None

logger = logging.getLogger(__name__)

# Создаем роутер (вместо app)
router = APIRouter(prefix="/voice-assistant", tags=["Voice Assistant"])

# Инициализируем компоненты для старого endpoint (для обратной совместимости)
if VAD and STTEngine and GPTDialogue and TTSEngine:
    vad = VAD()
    stt = STTEngine()
    gpt = GPTDialogue()
    tts = TTSEngine()
else:
    vad = None
    stt = None
    gpt = None
    tts = None

# Хранилище активных подключений для старого endpoint
active_connections: dict = {}

# Путь к веб-интерфейсу
web_dir = Path(__file__).parent / "web"


class AudioChunk(BaseModel):
    """Модель для получения аудио чанка."""
    audio: str  # base64 encoded audio
    sample_rate: int = SAMPLE_RATE


class TextRequest(BaseModel):
    """Модель для текстового запроса."""
    text: str


# ==================== РОУТЫ ====================


@router.get("/", response_class=HTMLResponse)
async def get_index():
    """Возвращает главную страницу веб-интерфейса."""
    index_path = web_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>Веб-интерфейс не найден</h1><p>Пожалуйста, скопируйте index.html в voice_assistant/web/</p>")


@router.get("/health")
async def health_check():
    """Проверка здоровья сервера."""
    components_status = {}
    
    if vad:
        components_status["vad"] = "ready"
    else:
        components_status["vad"] = "not_available"
    
    if stt:
        components_status["stt"] = "ready"
    else:
        components_status["stt"] = "not_available"
    
    if gpt:
        components_status["gpt"] = "ready"
    else:
        components_status["gpt"] = "not_available"
    
    if tts:
        components_status["tts"] = "ready"
    else:
        components_status["tts"] = "not_available"
    
    return {
        "status": "ok" if all([vad, stt, gpt, tts]) else "partial",
        "components": components_status
    }


# ==================== РОУТЫ ДЛЯ ГОЛОСОВОЙ ТРЕНИРОВКИ ====================

@router.get("/training", response_class=HTMLResponse)
async def get_training_page(
    request: Request, 
    training_id: Optional[int] = None, 
    session_id: Optional[int] = None
):
    """Возвращает страницу голосовой тренировки."""
    from fastapi.templating import Jinja2Templates
    from database import get_db
    from models import User
    
    # Получаем путь к шаблонам
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent  # /app/voice_assistant -> /app
    templates_dir = project_root / "templates"
    
    # Проверяем, существует ли директория templates
    if not templates_dir.exists():
        templates_dir = project_root / "app" / "templates"
    
    templates = Jinja2Templates(directory=str(templates_dir))
    
    # Получаем user_id из сессии и загружаем пользователя из БД
    user_id = request.session.get("user_id")
    user = None
    
    if user_id:
        db_gen = get_db()
        db = next(db_gen)
        user = db.query(User).filter_by(id=user_id).first()
        db.close()
    
    # Если user нет, создаем заглушку
    if not user:
        # Создаем минимальный объект user для совместимости с _layout_dashboard.html
        class FakeUser:
            def __init__(self):
                self.id = None
                self.name = "Гость"
                self.email = "guest@training.local"
        
        user = FakeUser()
    
    # Данные о тренировке (если есть training_id)
    training_data = {
        "id": training_id or "new",
        "session_id": session_id,
        "topic": "Тренировка продаж с ИИ",
        "scenario": "sales",
        "difficulty": "medium"
    }
    
    # Если передан training_id, получаем данные тренировки из БД
    if training_id:
        try:
            from database import get_db
            from models import Training
            from sqlalchemy.orm import Session
            
            # Получаем БД сессию
            db_gen = get_db()
            db = next(db_gen)
            
            training = db.query(Training).filter_by(id=training_id).first()
            if training:
                training_data.update({
                    "topic": training.title,
                    "description": training.description,
                    "recommendation": training.recommendation,
                    "scenario": training.scenario_type,
                })
            
            db.close()
        except Exception as e:
            logger.error(f"Ошибка загрузки данных тренировки: {e}", exc_info=True)
    
    # Данные для шаблона
    context = {
        "request": request,
        "user": user,
        "current_user": user,  # Для совместимости с обновлённым шаблоном
        "training": training_data
    }
    
    return templates.TemplateResponse("voice_training_conference.html", context)


# ВРЕМЕННО ОТКЛЮЧЕНО - конфликт имён с SQLAlchemy моделью
# @router.post("/training/create")
# async def create_training_session(session: TrainingSession):
#     """Создает новую тренировочную сессию."""
#     import uuid
#     
#     session_id = str(uuid.uuid4())
#     
#     # Сохраняем сессию
#     active_training_sessions_disabled = {
#         "id": session_id,
#         "topic": session.topic,
#         "scenario": session.scenario,
#         "difficulty": session.difficulty,
#         "duration_minutes": session.duration_minutes,
#         "created_at": asyncio.get_event_loop().time(),
#         "stats": {
#             "user_responses": 0,
#             "ai_questions": 0,
#             "score": 0
#         }
#     }
#     
#     logger.info(f"Создана новая тренировочная сессия: {session_id}")
#     
#     return {
#         "success": True,
#         "session_id": session_id,
#         "session": active_training_sessions[session_id]
#     }


@router.post("/training/complete")
async def complete_training(request: Request):
    """Сохраняет результаты завершённой тренировки."""
    try:
        data = await request.json()
        session_id = data.get("session_id")
        training_id = data.get("training_id")
        transcript = data.get("transcript", "")
        score = data.get("score", 0)
        user_responses_count = data.get("user_responses_count", 0)
        ai_questions_count = data.get("ai_questions_count", 0)
        
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        
        logger.info(f"Завершение тренировки: session_id={session_id}, training_id={training_id}, score={score}")
        
        # Если это тренировка из плана, сохраняем в БД
        if training_id and session_id:
            try:
                from database import get_db
                from models import TrainingSession as DBTrainingSession
                from datetime import datetime
                
                # Получаем БД сессию
                db_gen = get_db()
                db = next(db_gen)
                
                # Обновляем сессию тренировки
                db_session = db.query(DBTrainingSession).filter_by(id=session_id).first()
                if db_session:
                    db_session.completed_at = datetime.utcnow()
                    db_session.duration_seconds = int((datetime.utcnow() - db_session.started_at).total_seconds())
                    db_session.transcript = transcript
                    db_session.score = score
                    db_session.user_responses_count = user_responses_count
                    db_session.ai_questions_count = ai_questions_count
                    
                    # Генерируем feedback на основе score
                    if score >= 80:
                        feedback = "Отличная работа! Вы прекрасно справились с тренировкой."
                    elif score >= 70:
                        feedback = "Хорошая работа! Вы прошли тренировку."
                    elif score >= 50:
                        feedback = "Неплохо, но есть что улучшить. Попробуйте ещё раз."
                    else:
                        feedback = "Требуется больше практики. Не сдавайтесь!"
                    
                    db_session.feedback = feedback
                    
                    db.commit()
                    logger.info(f"Результаты тренировки сохранены в БД: session_id={session_id}")
                
                db.close()
                
                return {
                    "success": True,
                    "message": "Результаты сохранены",
                    "score": score,
                    "feedback": feedback if db_session else "Тренировка завершена"
                }
            except Exception as e:
                logger.error(f"Ошибка сохранения результатов в БД: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": str(e)
                }
        else:
            # Обычная тренировка (не из плана)
            return {
                "success": True,
                "message": "Тренировка завершена",
                "score": score
            }
    
    except Exception as e:
        logger.error(f"Ошибка завершения тренировки: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/training/{session_id}")
async def get_training_session(session_id: str):
    """Получает информацию о тренировочной сессии."""
    if session_id not in active_training_sessions:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    return active_training_sessions[session_id]


@router.get("/training/{session_id}/stats")
async def get_training_stats(session_id: str):
    """Получает статистику тренировочной сессии."""
    if session_id not in active_training_sessions:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    session = active_training_sessions[session_id]
    current_time = asyncio.get_event_loop().time()
    duration_seconds = int(current_time - session["created_at"])
    
    return {
        "session_id": session_id,
        "duration_seconds": duration_seconds,
        "stats": session["stats"]
    }


@router.delete("/training/{session_id}")
async def delete_training_session(session_id: str):
    """Удаляет тренировочную сессию."""
    if session_id in active_training_sessions:
        del active_training_sessions[session_id]
        logger.info(f"Тренировочная сессия удалена: {session_id}")
        return {"success": True, "message": "Сессия удалена"}
    else:
        raise HTTPException(status_code=404, detail="Сессия не найдена")


@router.get("/scenarios")
async def get_training_scenarios():
    """Возвращает доступные сценарии тренировок."""
    scenarios = [
        {
            "id": "sales",
            "name": "Продажи",
            "description": "Тренировка навыков продаж: презентация продукта, работа с возражениями",
            "checklist": [
                {"category": "Приветствие", "items": ["Представиться клиенту", "Узнать имя клиента"]},
                {"category": "Выявление потребностей", "items": ["Задать открытый вопрос", "Активно слушать"]},
                {"category": "Презентация", "items": ["Рассказать о продукте", "Упомянуть преимущества"]},
                {"category": "Работа с возражениями", "items": ["Выслушать возражение", "Ответить на возражение"]},
                {"category": "Закрытие сделки", "items": ["Предложить следующий шаг", "Договориться о встрече"]}
            ]
        },
        {
            "id": "customer_service",
            "name": "Обслуживание клиентов",
            "description": "Тренировка работы с клиентами: решение проблем, эмпатия",
            "checklist": [
                {"category": "Приветствие", "items": ["Поприветствовать клиента", "Выразить готовность помочь"]},
                {"category": "Выявление проблемы", "items": ["Задать уточняющие вопросы", "Проявить эмпатию"]},
                {"category": "Решение", "items": ["Предложить решение", "Объяснить следующие шаги"]},
                {"category": "Завершение", "items": ["Убедиться в удовлетворенности", "Попрощаться вежливо"]}
            ]
        },
        {
            "id": "negotiation",
            "name": "Переговоры",
            "description": "Тренировка навыков ведения переговоров и достижения договоренностей",
            "checklist": [
                {"category": "Подготовка", "items": ["Обозначить свою позицию", "Выяснить позицию оппонента"]},
                {"category": "Обсуждение", "items": ["Найти общие интересы", "Предложить варианты"]},
                {"category": "Компромисс", "items": ["Пойти на уступки", "Получить встречные уступки"]},
                {"category": "Договоренность", "items": ["Зафиксировать договоренность", "Обозначить следующие шаги"]}
            ]
        }
    ]
    
    return {"scenarios": scenarios}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint для потоковой передачи аудио и получения ответов.
    С поддержкой VAD (Voice Activity Detection) для автоматического определения конца речи.
    """
    await websocket.accept()
    
    # Проверяем доступность модулей после подключения
    if not all([vad, stt, gpt, tts]):
        await websocket.send_json({
            "type": "error",
            "message": "Voice assistant modules not available. Please check server logs."
        })
        await websocket.close(code=1003, reason="Voice assistant modules not available")
        return
    
    active_connections[websocket] = {
        "current_audio": [],
        "is_processing": False,
        "is_speaking": False,  # Флаг: человек сейчас говорит
        "silence_start": None,  # Время начала тишины
        "last_audio_time": None,  # Время последнего аудио чанка
    }
    logger.info(f"Клиент подключен. Всего подключений: {len(active_connections)}")
    
    # Состояние для текущего запроса
    state = active_connections[websocket]
    current_audio = state["current_audio"]
    
    # Параметры VAD - оптимизировано для мгновенного ответа
    SILENCE_THRESHOLD = 0.65  # Секунды тишины перед обработкой (400мс для максимальной скорости)
    SPEECH_THRESHOLD = 0.02  # Порог для определения речи (RMS) - более чувствительный
    
    async def check_for_silence():
        """Фоновая задача для проверки тишины и автоматической обработки"""
        while websocket in active_connections:
            await asyncio.sleep(0.05)  # Проверяем каждые 50ms для максимальной реактивности
            
            if state["is_processing"]:
                continue
            
            # Проверяем была ли речь и прошло ли достаточно времени тишины
            if state["is_speaking"] and state["silence_start"]:
                silence_duration = asyncio.get_event_loop().time() - state["silence_start"]
                
                if silence_duration >= SILENCE_THRESHOLD:
                    # Тишина достаточно долгая - обрабатываем накопленное аудио
                    if len(current_audio) > 0:
                        logger.info(f"🤫 Обнаружена тишина {silence_duration:.2f}s - начинаем обработку")
                        
                        # Объединяем аудио
                        audio_combined = np.concatenate(current_audio)
                        current_audio.clear()
                        
                        # Сбрасываем флаги
                        state["is_speaking"] = False
                        state["silence_start"] = None
                        
                        # Отправляем транскрипцию
                        await websocket.send_json({
                            "type": "status",
                            "status": "processing"
                        })
                        
                        # Запускаем обработку
                        state["is_processing"] = True
                        asyncio.create_task(process_audio_request(websocket, audio_combined, state))
    
    # Запускаем фоновую задачу проверки тишины
    silence_task = asyncio.create_task(check_for_silence())
    
    try:
        while True:
            # Получаем данные от клиента
            data = await websocket.receive_json()
            
            if data.get("type") == "audio" or data.get("type") == "audio_data":
                # Получаем аудио данные (base64)
                audio_b64 = data.get("audio", "") or data.get("audio_data", "")
                if not audio_b64:
                    continue
                
                try:
                    # Декодируем base64
                    audio_bytes = base64.b64decode(audio_b64)
                    
                    # Клиент отправляет Float32Array, декодируем как float32
                    audio_float32 = np.frombuffer(audio_bytes, dtype=np.float32)
                    
                    # Нормализуем если нужно (убеждаемся что значения в диапазоне [-1, 1])
                    max_val = np.max(np.abs(audio_float32))
                    if max_val > 1.0:
                        audio_float32 = audio_float32 / max_val
                    
                    # Вычисляем RMS для определения речи
                    rms = np.sqrt(np.mean(audio_float32**2))
                    
                    state["last_audio_time"] = asyncio.get_event_loop().time()
                    
                    # Определяем есть ли речь
                    if rms > SPEECH_THRESHOLD:
                        # Есть речь
                        if not state["is_speaking"]:
                            logger.info("🎤 Обнаружена речь")
                            state["is_speaking"] = True
                        state["silence_start"] = None  # Сбрасываем счётчик тишины
                        
                        # Добавляем в буфер
                        current_audio.append(audio_float32)
                    else:
                        # Тишина
                        if state["is_speaking"] and len(current_audio) > 0:
                            # Начало тишины после речи
                            if state["silence_start"] is None:
                                state["silence_start"] = asyncio.get_event_loop().time()
                            
                            # Всё равно добавляем чанк (может быть короткая пауза в речи)
                            current_audio.append(audio_float32)
                    
                except Exception as e:
                    logger.error(f"Ошибка обработки аудио: {e}")
                    continue
            
            elif data.get("type") == "audio_end":
                # Сигнал окончания записи
                if len(current_audio) == 0:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Не получены аудио данные"
                    })
                    continue
                
                # Объединяем все чанки
                audio_combined = np.concatenate(current_audio)
                current_audio.clear()
                
                # Обрабатываем запрос
                if not state["is_processing"]:
                    state["is_processing"] = True
                    asyncio.create_task(process_audio_request(websocket, audio_combined, state))
            
            elif data.get("type") == "text":
                # Текстовый запрос
                text = data.get("text", "").strip()
                if text:
                    asyncio.create_task(process_text_request(websocket, text))
            
            elif data.get("type") == "stop":
                # Остановка обработки
                if hasattr(tts, 'stop_playing'):
                    tts.stop_playing = True
                state["is_processing"] = False
                current_audio.clear()
                await websocket.send_json({
                    "type": "status",
                    "status": "ready",
                    "message": "✅ Остановлено"
                })
    
    except WebSocketDisconnect:
        logger.info("Клиент отключен")
    except Exception as e:
        logger.error(f"Ошибка WebSocket: {e}")
    finally:
        # Отменяем фоновую задачу
        if 'silence_task' in locals():
            silence_task.cancel()
        
        if websocket in active_connections:
            del active_connections[websocket]


async def process_audio_request(websocket: WebSocket, audio: np.ndarray, state: dict):
    """
    Обрабатывает аудио запрос: распознавание → GPT → TTS.
    """
    try:
        # Уведомляем о начале обработки
        await websocket.send_json({
            "type": "status",
            "status": "processing",
            "message": "🎤 Распознавание речи..."
        })
        
        # Проверяем аудио
        if len(audio) == 0:
            logger.warning("Получен пустой аудио массив")
            await websocket.send_json({
                "type": "error",
                "message": "Не получены аудио данные"
            })
            return
        
        logger.info(f"Обработка аудио: {len(audio)} сэмплов ({len(audio)/SAMPLE_RATE:.2f} секунд)")
        
        # Распознаем речь
        text = await asyncio.to_thread(stt.transcribe, audio)
        
        if not text.strip():
            logger.warning("Whisper не распознал речь")
            await websocket.send_json({
                "type": "error",
                "message": "Не удалось распознать речь. Попробуйте говорить четче и громче."
            })
            return
        
        # Отправляем транскрипцию
        await websocket.send_json({
            "type": "transcript",
            "text": text
        })
        
        # Параллельная обработка GPT и TTS
        await websocket.send_json({
            "type": "status",
            "status": "thinking",
            "message": "🤖 Генерация ответа..."
        })
        
        gpt_stream = gpt.get_response_stream(text)
        
        # Очередь для TTS
        tts_queue = asyncio.Queue()
        full_response = ""
        
        async def process_gpt_stream():
            """Обрабатывает поток GPT и добавляет в очередь TTS"""
            nonlocal full_response
            text_buffer = ""  # Буфер для накопления текста
            min_buffer_size = 20  # Минимум символов перед отправкой в TTS (для плавности)
            
            async for chunk in gpt_stream:
                if hasattr(tts, 'stop_playing') and tts.stop_playing:
                    break
                full_response += chunk
                # Отправляем чанк текста клиенту для отображения сразу
                await websocket.send_json({
                    "type": "assistant_chunk",
                    "text": chunk
                })
                
                # Накапливаем текст в буфере
                text_buffer += chunk
                
                # Отправляем в TTS когда накопили достаточно или есть точка/запятая
                if len(text_buffer) >= min_buffer_size:
                    # Проверяем, есть ли естественная пауза (точка, запятая, восклицательный знак)
                    if any(punct in text_buffer for punct in ['.', '!', '?', ',', ';', ':', '\n']):
                        # Находим последнюю естественную паузу
                        last_pause = max(
                            text_buffer.rfind('.'),
                            text_buffer.rfind('!'),
                            text_buffer.rfind('?'),
                            text_buffer.rfind(','),
                            text_buffer.rfind(';'),
                            text_buffer.rfind(':'),
                            text_buffer.rfind('\n')
                        )
                        if last_pause > 0:
                            # Отправляем до паузы
                            await tts_queue.put(text_buffer[:last_pause + 1])
                            text_buffer = text_buffer[last_pause + 1:]
                    else:
                        # Если нет паузы, но буфер большой - отправляем
                        if len(text_buffer) >= min_buffer_size * 2:
                            await tts_queue.put(text_buffer)
                            text_buffer = ""
            
            # Отправляем остаток
            if text_buffer.strip():
                await tts_queue.put(text_buffer)
            await tts_queue.put(None)  # Сигнал завершения
        
        async def process_tts_from_queue():
            """Обрабатывает очередь TTS и отправляет аудио"""
            await websocket.send_json({
                "type": "status",
                "status": "synthesizing",
                "message": "🔊 Синтез речи..."
            })
            
            # Сигнал начала озвучивания - временно отключаем микрофон
            await websocket.send_json({
                "type": "audio_start",
                "message": "Начало озвучивания ответа"
            })
            
            while True:
                text_chunk = await tts_queue.get()
                if text_chunk is None:
                    break
                
                if not text_chunk.strip():
                    continue
                
                # Создаем поток для TTS
                text_stream = async_generator_from_string(text_chunk)
                tts_stream = tts.synthesize_stream(text_stream)
                
                # Отправляем аудио чанки
                async for audio_chunk in tts_stream:
                    if hasattr(tts, 'stop_playing') and tts.stop_playing:
                        break
                    
                    # Конвертируем в base64
                    audio_bytes = (audio_chunk * 32767).astype(np.int16).tobytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                    
                    await websocket.send_json({
                        "type": "audio_chunk",
                        "audio_data": audio_b64,
                        "sample_rate": SAMPLE_RATE
                    })
        
        # Запускаем обе задачи параллельно
        gpt_task = asyncio.create_task(process_gpt_stream())
        tts_task = asyncio.create_task(process_tts_from_queue())
        
        await gpt_task
        await tts_task
        
        await websocket.send_json({
            "type": "assistant_complete",
            "text": full_response
        })
        
        # Сигнал окончания аудио
        await websocket.send_json({
            "type": "audio_end"
        })
        
        await websocket.send_json({
            "type": "status",
            "status": "ready",
            "message": "✅ Готов к работе"
        })
        
    except Exception as e:
        logger.error(f"Ошибка обработки запроса: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Ошибка обработки: {str(e)}"
            })
        except Exception as send_error:
            logger.error(f"Ошибка отправки сообщения об ошибке: {send_error}")
    finally:
        # Сбрасываем флаг обработки - теперь система снова будет слушать
        state["is_processing"] = False
        logger.info("✅ Обработка завершена, снова готов слушать")


async def process_text_request(websocket: WebSocket, text: str):
    """
    Обрабатывает текстовый запрос: GPT → TTS.
    """
    try:
        # Отправляем транскрипцию (текстовый запрос воспринимается как речь пользователя)
        await websocket.send_json({
            "type": "transcript",
            "text": text
        })
        
        await websocket.send_json({
            "type": "status",
            "status": "thinking",
            "message": "🤖 Генерация ответа..."
        })
        
        gpt_stream = gpt.get_response_stream(text)
        
        tts_queue = asyncio.Queue()
        full_response = ""
        
        async def process_gpt_stream():
            nonlocal full_response
            async for chunk in gpt_stream:
                if hasattr(tts, 'stop_playing') and tts.stop_playing:
                    break
                full_response += chunk
                await tts_queue.put(chunk)
            await tts_queue.put(None)
        
        async def process_tts_from_queue():
            await websocket.send_json({
                "type": "status",
                "status": "synthesizing",
                "message": "🔊 Синтез речи..."
            })
            
            # Сигнал начала озвучивания - временно отключаем микрофон
            await websocket.send_json({
                "type": "audio_start",
                "message": "Начало озвучивания ответа"
            })
            
            while True:
                text_chunk = await tts_queue.get()
                if text_chunk is None:
                    break
                
                if not text_chunk.strip():
                    continue
                
                text_stream = async_generator_from_string(text_chunk)
                tts_stream = tts.synthesize_stream(text_stream)
                
                async for audio_chunk in tts_stream:
                    if hasattr(tts, 'stop_playing') and tts.stop_playing:
                        break
                    
                    audio_bytes = (audio_chunk * 32767).astype(np.int16).tobytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                    
                    await websocket.send_json({
                        "type": "audio_chunk",
                        "audio_data": audio_b64,
                        "sample_rate": SAMPLE_RATE
                    })
        
        gpt_task = asyncio.create_task(process_gpt_stream())
        tts_task = asyncio.create_task(process_tts_from_queue())
        
        await gpt_task
        await tts_task
        
        await websocket.send_json({
            "type": "assistant_message",
            "text": full_response
        })
        
        await websocket.send_json({
            "type": "status",
            "status": "ready",
            "message": "✅ Готов к работе"
        })
        
    except Exception as e:
        logger.error(f"Ошибка обработки текста: {e}", exc_info=True)
        await websocket.send_json({
            "type": "error",
            "message": f"Ошибка обработки: {str(e)}"
        })


def async_generator_from_string(text: str):
    """Создает async generator из строки"""
    async def gen():
        yield text
    return gen()

