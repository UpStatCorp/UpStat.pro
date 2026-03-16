"""
Модуль озвучивания ответов (Text-to-Speech).
Поддерживает OpenAI TTS и ElevenLabs.
"""

import asyncio
import numpy as np
from typing import Optional, AsyncGenerator

# Опциональный импорт sounddevice (нужен только для локального воспроизведения)
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False
from openai import AsyncOpenAI
from .utils.logger import setup_logger
from .utils.audio_utils import normalize_audio
from .config import (
    OPENAI_API_KEY, ELEVENLABS_API_KEY, TTS_PROVIDER, TTS_VOICE, 
    TTS_MODEL, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL, SAMPLE_RATE,
    ELEVENLABS_STABILITY, ELEVENLABS_SIMILARITY_BOOST, ELEVENLABS_STYLE,
    ELEVENLABS_USE_SPEAKER_BOOST, OPENAI_TTS_SPEED
)

# Импортируем для проверки типов ошибок
try:
    from elevenlabs.core.api_error import ApiError as ElevenLabsApiError
except ImportError:
    ElevenLabsApiError = None

logger = setup_logger("tts")

class TTSEngine:
    """
    Движок озвучивания текста с поддержкой разных провайдеров.
    """
    
    def __init__(
        self, 
        provider: str = None, 
        voice: str = None,
        model: str = None,
        api_key: str = None,
        elevenlabs_model: str = None
    ):
        """
        Инициализирует движок TTS.
        
        Args:
            provider: Провайдер TTS ("openai" или "elevenlabs")
            voice: Голос для озвучивания
            model: Модель TTS (для OpenAI)
            api_key: API ключ (если отличается от конфига)
            elevenlabs_model: Модель ElevenLabs (если отличается от конфига)
        """
        self.provider = provider or TTS_PROVIDER
        self.voice = voice or TTS_VOICE
        self.model = model or TTS_MODEL
        self.elevenlabs_model = elevenlabs_model or ELEVENLABS_MODEL
        
        if self.provider == "openai":
            self.api_key = api_key or OPENAI_API_KEY
            if not self.api_key:
                raise ValueError("OPENAI_API_KEY не установлен для OpenAI TTS!")
            self.client = AsyncOpenAI(api_key=self.api_key)
            logger.info(f"TTS инициализирован: OpenAI, голос={self.voice}, модель={self.model}")
            logger.info(f"🎙️  Скорость речи: {OPENAI_TTS_SPEED}x (1.0 = нормальная)")
            
        elif self.provider == "elevenlabs":
            self.api_key = api_key or ELEVENLABS_API_KEY
            self.voice_id = ELEVENLABS_VOICE_ID
            # Проверяем наличие API ключа и voice_id
            if not self.api_key or not self.voice_id:
                logger.warning(f"⚠️  ElevenLabs не настроен (api_key={bool(self.api_key)}, voice_id={bool(self.voice_id)}), будет использован OpenAI TTS")
                # Переключаемся на OpenAI если ElevenLabs не настроен
                if OPENAI_API_KEY:
                    self.provider = "openai"
                    self.api_key = OPENAI_API_KEY
                    self.client = AsyncOpenAI(api_key=self.api_key)
                    logger.info(f"TTS инициализирован: OpenAI (fallback), голос={self.voice}, модель={self.model}")
                else:
                    raise ValueError("ELEVENLABS_API_KEY и ELEVENLABS_VOICE_ID не установлены, а OPENAI_API_KEY тоже отсутствует!")
            else:
                try:
                    from elevenlabs.client import ElevenLabs
                    from elevenlabs.core.api_error import ApiError
                    self.client = ElevenLabs(api_key=self.api_key)
                    self.elevenlabs_api_error = ApiError  # Сохраняем класс для обработки ошибок
                    logger.info(f"TTS инициализирован: ElevenLabs, voice_id={self.voice_id}, model={self.elevenlabs_model}")
                    logger.info(f"🎙️  Параметры стабильности голоса: stability={ELEVENLABS_STABILITY}, similarity={ELEVENLABS_SIMILARITY_BOOST}, style={ELEVENLABS_STYLE}, speaker_boost={ELEVENLABS_USE_SPEAKER_BOOST}")
                except ImportError:
                    logger.error("Библиотека elevenlabs не установлена!")
                    raise
        
        else:
            raise ValueError(f"Неподдерживаемый провайдер TTS: {self.provider}")
        
        # Флаг для остановки воспроизведения
        self.stop_playing = False
    
    def reset_stop_flag(self):
        """Сбрасывает флаг остановки воспроизведения."""
        self.stop_playing = False
    
    def request_stop(self):
        """Запрашивает остановку текущего воспроизведения."""
        self.stop_playing = True
        logger.debug("Запрошена остановка воспроизведения TTS")
    
    async def synthesize_stream(self, text_stream: AsyncGenerator[str, None]) -> AsyncGenerator[np.ndarray, None]:
        """
        Синтезирует речь из потока текста и возвращает поток аудио данных.
        ОПТИМИЗАЦИЯ: Синтезирует текст напрямую (поток уже обработан).
        
        Args:
            text_stream: Асинхронный генератор текста
            
        Yields:
            Чанки аудио данных (numpy массивы float32)
        """
        self.reset_stop_flag()
        
        # Собираем весь текст из потока
        full_text = ""
        async for chunk in text_stream:
            if self.stop_playing:
                logger.info("Воспроизведение остановлено по запросу")
                break
            full_text += chunk
        
        if not full_text.strip() or self.stop_playing:
            return
        
        # Синтезируем речь (для скорости синтезируем весь текст сразу)
        async for audio_chunk in self._synthesize(full_text):
            if self.stop_playing:
                break
            yield audio_chunk
    
    async def synthesize(self, text: str) -> AsyncGenerator[np.ndarray, None]:
        """
        Синтезирует речь из текста.
        
        Args:
            text: Текст для озвучивания
            
        Yields:
            Чанки аудио данных
        """
        async for chunk in self._synthesize(text):
            yield chunk
    
    async def _synthesize(self, text: str) -> AsyncGenerator[np.ndarray, None]:
        """
        Внутренний метод синтеза речи (зависит от провайдера).
        
        Args:
            text: Текст для озвучивания
            
        Yields:
            Чанки аудио данных
            
        Note:
            Если ElevenLabs недоступен (403 ошибка), автоматически переключается на OpenAI TTS
        """
        if not text.strip():
            return
        
        try:
            if self.provider == "openai":
                async for chunk in self._synthesize_openai(text):
                    yield chunk
            elif self.provider == "elevenlabs":
                try:
                    async for chunk in self._synthesize_elevenlabs(text):
                        yield chunk
                except Exception as e:
                    # Проверяем, является ли это ошибкой доступа ElevenLabs
                    error_str = str(e)
                    error_msg = error_str.lower()
                    
                    # Проверяем статус код если это ApiError
                    status_code = None
                    if ElevenLabsApiError and isinstance(e, ElevenLabsApiError):
                        status_code = getattr(e, 'status_code', None)
                    
                    # Проверяем различные типы ошибок ElevenLabs
                    is_access_error = (
                        status_code == 403 or
                        status_code == 404 or  # voice_not_found тоже переключаем на OpenAI
                        "403" in error_str or 
                        "404" in error_str or
                        "voice_not_found" in error_msg or
                        "only_for_creator" in error_msg or 
                        "professional voices" in error_msg or
                        "creator+" in error_msg
                    )
                    
                    if is_access_error:
                        if status_code == 404 or "voice_not_found" in error_msg:
                            logger.warning("⚠️  ElevenLabs голос не найден (404), переключаюсь на OpenAI TTS")
                        else:
                            logger.warning("⚠️  ElevenLabs голос недоступен (требует Creator+), переключаюсь на OpenAI TTS")
                        # Используем OpenAI как fallback
                        if OPENAI_API_KEY:
                            logger.info("✅ Используется OpenAI TTS вместо ElevenLabs")
                            async for chunk in self._synthesize_openai(text):
                                yield chunk
                            return  # Успешно использовали fallback
                        else:
                            logger.error("❌ OpenAI API ключ не установлен для fallback!")
                            raise ValueError("ElevenLabs недоступен, а OpenAI API ключ не установлен!")
                    else:
                        # Другая ошибка - пробрасываем дальше
                        logger.error(f"Ошибка ElevenLabs (не 403/404): {e}")
                        raise
        except Exception as e:
            logger.error(f"Ошибка при синтезе речи: {e}")
    
    async def _synthesize_openai(self, text: str) -> AsyncGenerator[np.ndarray, None]:
        """
        Синтезирует речь через OpenAI TTS.
        
        Args:
            text: Текст для озвучивания
            
        Yields:
            Чанки аудио данных
        """
        try:
            # OpenAI TTS возвращает аудио в формате mp3 по умолчанию
            # Используем opus для лучшего качества и меньшего размера
            response = await self.client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text,
                response_format="opus",  # opus формат
                speed=OPENAI_TTS_SPEED  # Постоянная скорость для стабильности
            )
            
            # Читаем аудио данные
            audio_data = response.content
            
            # Конвертируем аудио в numpy массив с помощью pydub
            import io
            try:
                from pydub import AudioSegment
            except ImportError:
                logger.error("Требуется установить pydub: pip install pydub")
                logger.error("Также нужен ffmpeg: brew install ffmpeg (macOS) или apt-get install ffmpeg (Linux)")
                raise
            
            # Загружаем opus аудио
            try:
                audio_segment = AudioSegment.from_file(
                    io.BytesIO(audio_data),
                    format="opus"
                )
            except Exception:
                # Если opus не поддерживается, пробуем mp3
                logger.warning("Opus не поддерживается, используем mp3")
                response_mp3 = await self.client.audio.speech.create(
                    model=self.model,
                    voice=self.voice,
                    input=text,
                    response_format="mp3",
                    speed=OPENAI_TTS_SPEED  # Постоянная скорость для стабильности
                )
                audio_segment = AudioSegment.from_mp3(io.BytesIO(response_mp3.content))
            
            # Конвертируем в моно и нужную частоту дискретизации
            audio_segment = audio_segment.set_channels(1)
            audio_segment = audio_segment.set_frame_rate(SAMPLE_RATE)
            
            # Конвертируем в numpy массив
            samples = audio_segment.get_array_of_samples()
            audio_array = np.array(samples, dtype=np.float32) / 32767.0
            
            # Нормализуем весь массив сразу (для лучшего качества)
            max_val = np.max(np.abs(audio_array))
            if max_val > 0:
                audio_array = audio_array / max_val * 0.9  # Немного снижаем громкость для избежания клиппинга
            
            # Разбиваем на чанки для потоковой передачи
            # Увеличиваем размер чанка для лучшего качества (меньше щелчков)
            chunk_size = 16384  # Большой размер чанка для плавного воспроизведения
            for i in range(0, len(audio_array), chunk_size):
                if self.stop_playing:
                    break
                chunk = audio_array[i:i + chunk_size]
                if len(chunk) > 0:
                    yield chunk
            
        except Exception as e:
            logger.error(f"Ошибка при синтезе через OpenAI: {e}")
    
    async def _synthesize_elevenlabs(self, text: str) -> AsyncGenerator[np.ndarray, None]:
        """
        Синтезирует речь через ElevenLabs TTS.
        
        Args:
            text: Текст для озвучивания
            
        Yields:
            Чанки аудио данных
        """
        try:
            # ElevenLabs синхронный API, используем asyncio.to_thread для асинхронности
            def generate_audio():
                try:
                    # Используем правильный API метод convert для синтеза речи
                    # В ElevenLabs SDK доступны: convert, convert_as_stream, convert_realtime
                    if hasattr(self.client.text_to_speech, 'convert_as_stream'):
                        # Потоковый метод (предпочтительно)
                        # Используем оптимизированные параметры для стабильного голоса
                        voice_settings = {
                            "stability": ELEVENLABS_STABILITY,  # Высокая стабильность для постоянного голоса
                            "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,  # Высокое сходство с оригиналом
                            "style": ELEVENLABS_STYLE,  # Минимальная экспрессивность для нейтральности
                            "use_speaker_boost": ELEVENLABS_USE_SPEAKER_BOOST  # Улучшение четкости
                        }
                        audio_generator = self.client.text_to_speech.convert_as_stream(
                            voice_id=self.voice_id,
                            text=text,
                            model_id=self.elevenlabs_model,
                            voice_settings=voice_settings
                        )
                        return audio_generator
                    elif hasattr(self.client.text_to_speech, 'convert'):
                        # Обычный метод convert (возвращает bytes)
                        voice_settings = {
                            "stability": ELEVENLABS_STABILITY,
                            "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
                            "style": ELEVENLABS_STYLE,
                            "use_speaker_boost": ELEVENLABS_USE_SPEAKER_BOOST
                        }
                        audio_bytes = self.client.text_to_speech.convert(
                            voice_id=self.voice_id,
                            text=text,
                            model_id=self.elevenlabs_model,
                            voice_settings=voice_settings
                        )
                        # Возвращаем как генератор с одним элементом
                        return iter([audio_bytes])
                    else:
                        raise AttributeError("ElevenLabs client не поддерживает convert или convert_as_stream методы")
                except Exception as e:
                    # Пробрасываем ошибку для обработки выше
                    raise
            
            # Запускаем генерацию в отдельном потоке
            try:
                audio_generator = await asyncio.to_thread(generate_audio)
            except Exception as e:
                # Проверяем, является ли это ошибкой доступа
                error_msg = str(e).lower()
                is_access_error = (
                    "403" in str(e) or 
                    "only_for_creator" in error_msg or 
                    "professional voices" in error_msg or
                    (ElevenLabsApiError and isinstance(e, ElevenLabsApiError) and hasattr(e, 'status_code') and e.status_code == 403)
                )
                
                if is_access_error:
                    logger.warning(f"⚠️  ElevenLabs голос недоступен (требует Creator+), переключение на OpenAI")
                    raise  # Пробрасываем для обработки в _synthesize
                raise
            
            # Собираем все аудио данные перед декодированием (для правильной обработки MP3)
            audio_data_chunks = []
            try:
                for audio_chunk in audio_generator:
                    if self.stop_playing:
                        break
                    audio_data_chunks.append(audio_chunk)
            except Exception as e:
                # Проверяем ошибку при получении данных
                error_str = str(e)
                error_msg = error_str.lower()
                
                # Проверяем статус код если это ApiError
                status_code = None
                if ElevenLabsApiError and isinstance(e, ElevenLabsApiError):
                    status_code = getattr(e, 'status_code', None)
                
                is_access_error = (
                    status_code == 403 or
                    status_code == 404 or
                    "403" in error_str or 
                    "404" in error_str or
                    "voice_not_found" in error_msg or
                    "only_for_creator" in error_msg or 
                    "professional voices" in error_msg
                )
                
                if is_access_error:
                    # Пробрасываем для обработки в _synthesize (fallback на OpenAI)
                    raise
                logger.error(f"Ошибка при получении аудио от ElevenLabs: {e}")
                raise
            
            # Если нет данных, выходим
            if not audio_data_chunks:
                logger.warning("Нет аудио данных от ElevenLabs")
                return
            
            # Объединяем все чанки в один буфер
            import io
            from pydub import AudioSegment
            
            try:
                # Объединяем все байты
                full_audio_data = b''.join(audio_data_chunks)
                
                # Декодируем MP3
                audio_segment = AudioSegment.from_file(io.BytesIO(full_audio_data), format="mp3")
                audio_segment = audio_segment.set_channels(1).set_frame_rate(SAMPLE_RATE)
                
                # Конвертируем в numpy массив
                samples = audio_segment.get_array_of_samples()
                audio_float = np.array(samples, dtype=np.float32) / 32767.0
                
                # Нормализуем весь массив сразу (для лучшего качества)
                max_val = np.max(np.abs(audio_float))
                if max_val > 0:
                    audio_float = audio_float / max_val * 0.9  # Немного снижаем громкость
                
                # Разбиваем на чанки для потоковой передачи
                # Увеличиваем размер чанка для лучшего качества
                chunk_size = 16384  # Большой размер чанка для плавного воспроизведения
                for i in range(0, len(audio_float), chunk_size):
                    if self.stop_playing:
                        break
                    chunk = audio_float[i:i + chunk_size]
                    if len(chunk) > 0:
                        yield chunk
                        
            except Exception as e:
                logger.error(f"Ошибка при декодировании аудио от ElevenLabs: {e}")
                raise
                
        except Exception as e:
            # Проверяем тип ошибки еще раз на верхнем уровне
            error_msg = str(e).lower()
            is_access_error = (
                "403" in str(e) or 
                "only_for_creator" in error_msg or 
                "professional voices" in error_msg or
                (ElevenLabsApiError and isinstance(e, ElevenLabsApiError) and hasattr(e, 'status_code') and getattr(e, 'status_code', None) == 403)
            )
            
            if is_access_error:
                # Пробрасываем для fallback на OpenAI
                raise
            
            logger.error(f"Ошибка при синтезе через ElevenLabs: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise
    
    async def play_audio_stream(self, audio_stream: AsyncGenerator[np.ndarray, None]):
        """
        Воспроизводит поток аудио данных через динамики.
        
        Args:
            audio_stream: Поток аудио чанков
        """
        if not SOUNDDEVICE_AVAILABLE:
            logger.warning("⚠️ sounddevice не установлен, локальное воспроизведение недоступно")
            # Просто потребляем поток, но не воспроизводим
            async for _ in audio_stream:
                if self.stop_playing:
                    break
            return
        
        self.reset_stop_flag()
        
        try:
            async for audio_chunk in audio_stream:
                if self.stop_playing:
                    logger.info("Воспроизведение остановлено")
                    break
                
                if len(audio_chunk) > 0:
                    # Воспроизводим чанк
                    sd.play(audio_chunk, samplerate=SAMPLE_RATE)
                    sd.wait()  # Ждем завершения воспроизведения чанка
            
            logger.debug("Воспроизведение завершено")
            
        except Exception as e:
            logger.error(f"Ошибка при воспроизведении аудио: {e}")

