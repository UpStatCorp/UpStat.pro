"""
Модуль распознавания речи (Speech-to-Text) с поддержкой:
- faster-whisper (локальная модель)
- OpenAI Whisper API
- ElevenLabs Scribe STT
Обеспечивает реактивное распознавание речи в реальном времени.
"""

import numpy as np
from typing import Optional, Callable
from .utils.logger import setup_logger
from .config import (
    WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, SAMPLE_RATE,
    STT_PROVIDER, OPENAI_API_KEY, ELEVENLABS_API_KEY
)

logger = setup_logger("stt")

# Импортируем в зависимости от выбранного провайдера
if STT_PROVIDER == "whisper_openai":
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
        logger.info("Используется OpenAI Whisper API для распознавания речи")
    except ImportError:
        logger.warning("OpenAI не установлен, используем faster-whisper")
        STT_PROVIDER = "whisper_local"
        from faster_whisper import WhisperModel
elif STT_PROVIDER == "elevenlabs":
    try:
        from elevenlabs.client import ElevenLabs
        elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY) if ELEVENLABS_API_KEY else None
        logger.info("Используется ElevenLabs Scribe STT для распознавания речи")
    except ImportError:
        logger.warning("ElevenLabs не установлен, используем faster-whisper")
        STT_PROVIDER = "whisper_local"
        from faster_whisper import WhisperModel
else:
    from faster_whisper import WhisperModel

class STTEngine:
    """
    Движок распознавания речи на базе faster-whisper.
    """
    
    def __init__(self, model_size: str = None, device: str = None, compute_type: str = None):
        """
        Инициализирует движок STT.
        
        Args:
            model_size: Размер модели Whisper (tiny, base, small, medium, large)
            device: Устройство для выполнения (cpu, cuda)
            compute_type: Тип вычислений (int8, float16, float32)
        """
        self.provider = STT_PROVIDER
        
        if self.provider == "elevenlabs":
            if not elevenlabs_client:
                raise ValueError("ELEVENLABS_API_KEY не установлен для ElevenLabs STT!")
            self.client = elevenlabs_client
            logger.info("✅ Инициализирован ElevenLabs Scribe STT (очень высокая точность)")
        elif self.provider == "whisper_openai":
            if not openai_client:
                raise ValueError("OPENAI_API_KEY не установлен для OpenAI Whisper!")
            self.client = openai_client
            logger.info("✅ Инициализирован OpenAI Whisper API (высокая точность)")
        else:  # whisper_local
            self.model_size = model_size or WHISPER_MODEL
            self.device = device or WHISPER_DEVICE
            self.compute_type = compute_type or WHISPER_COMPUTE_TYPE
            
            logger.info(f"Загрузка модели faster-whisper: {self.model_size} на {self.device}")
            try:
                self.model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type
                )
                logger.info(f"✅ Модель Whisper '{self.model_size}' успешно загружена")
            except Exception as e:
                logger.error(f"Ошибка при загрузке модели Whisper: {e}")
                raise
    
    def transcribe(self, audio: np.ndarray, language: Optional[str] = "ru") -> str:
        """
        Распознает речь из аудио данных.
        
        Args:
            audio: Аудио массив (float32, значения от -1.0 до 1.0, частота 16kHz)
            language: Язык для распознавания (None для автоопределения, "ru" для русского)
            
        Returns:
            Распознанный текст
        """
        if len(audio) == 0:
            logger.warning("Получен пустой аудио массив")
            return ""
        
        try:
            # Убеждаемся, что аудио в формате float32 от -1.0 до 1.0
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            
            # КРИТИЧЕСКАЯ ПРОВЕРКА: Проверяем на NaN и Inf значения
            if np.any(np.isnan(audio)) or np.any(np.isinf(audio)):
                logger.error("⚠️ Аудио содержит NaN или Inf значения - это поврежденные данные!")
                return ""
            
            # Проверяем минимальную длину аудио
            min_samples = int(SAMPLE_RATE * 0.5)  # 0.5 секунды
            if len(audio) < min_samples:
                logger.warning(f"Аудио слишком короткое: {len(audio)} сэмплов (нужно минимум {min_samples})")
                return ""
            
            # Вычисляем статистику для проверки качества
            rms = np.sqrt(np.mean(audio**2))
            max_val = np.max(np.abs(audio))
            mean_val = np.mean(np.abs(audio))
            
            logger.info(f"Аудио: {len(audio)} сэмплов ({len(audio)/SAMPLE_RATE:.2f} сек), RMS: {rms:.4f}, Max: {max_val:.4f}, Mean: {mean_val:.4f}")
            
            # Проверяем уровень громкости
            if rms < 0.001:
                logger.warning(f"Аудио слишком тихое (RMS: {rms:.6f}), возможно только шум")
                return ""
            
            # Проверяем, что аудио не является константой (все нули или одно значение)
            if max_val == 0:
                logger.warning("Аудио сигнал полностью пустой (все нули)")
                return ""
            
            # Проверяем вариативность сигнала (не должно быть константы)
            std_val = np.std(audio)
            if std_val < 0.0001:
                logger.warning(f"Аудио сигнал слишком постоянный (std: {std_val:.6f}), возможно поврежден")
                return ""
            
            # Нормализуем аудио правильно
            if max_val > 1.0:
                audio = audio / max_val
                logger.info(f"Аудио нормализовано (максимум был: {max_val:.4f})")
            elif max_val < 0.05:
                # Если аудио очень тихое, усилим его, но не слишком сильно
                audio = audio / max_val * 0.7  # Усиливаем до 70% от максимума
                logger.info(f"Аудио усилено (максимум был: {max_val:.4f})")
            
            # Убеждаемся, что значения в правильном диапазоне
            audio = np.clip(audio, -1.0, 1.0)
            
            # Финальная проверка после нормализации
            if np.any(np.isnan(audio)) or np.any(np.isinf(audio)):
                logger.error("⚠️ Аудио содержит NaN или Inf после обработки!")
                return ""
            
            audio_enhanced = audio
            
            # Убеждаемся, что язык установлен на русский
            if not language:
                language = "ru"
            
            logger.info(f"🎤 Отправка в STT: {len(audio_enhanced)} сэмплов ({len(audio_enhanced)/SAMPLE_RATE:.2f} сек), язык: {language}, провайдер: {self.provider}")
            
            if self.provider == "elevenlabs":
                # Используем ElevenLabs Scribe STT (самое точное)
                full_text = self._transcribe_elevenlabs(audio_enhanced, language)
            elif self.provider == "whisper_openai":
                # Используем OpenAI Whisper API (высокая точность)
                full_text = self._transcribe_openai(audio_enhanced, language)
            else:  # whisper_local
                # Используем локальную модель faster-whisper
                full_text = self._transcribe_local(audio_enhanced, language)
            
            if full_text:
                logger.info(f"🎤 Распознано: {full_text}")
            
            return full_text
            
        except Exception as e:
            logger.error(f"Ошибка при распознавании речи: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return ""
    
    def _transcribe_elevenlabs(self, audio: np.ndarray, language: Optional[str]) -> str:
        """
        Распознавание через ElevenLabs Scribe STT (очень высокая точность).
        
        Args:
            audio: Аудио массив (float32)
            language: Язык для распознавания
            
        Returns:
            Распознанный текст
        """
        try:
            import io
            import wave
            
            # Конвертируем numpy массив в WAV файл в памяти
            audio_clipped = np.clip(audio, -1.0, 1.0)
            audio_int16 = (audio_clipped * 32767).astype(np.int16)
            
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Моно
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(SAMPLE_RATE)
                wav_file.writeframes(audio_int16.tobytes())
            
            wav_buffer.seek(0)
            
            # Отправляем в ElevenLabs Scribe STT API
            whisper_language = language if language else "ru"
            # Для ElevenLabs HTTP API используем "ru" для русского (не "rus")
            logger.info(f"🌐 ElevenLabs Scribe STT: язык распознавания = {whisper_language}")
            
            # Используем правильный метод ElevenLabs API для STT
            # Правильный способ: client.speech_to_text.convert()
            try:
                # Проверяем наличие speech_to_text объекта
                if hasattr(self.client, 'speech_to_text'):
                    # Согласно документации, file должен быть BytesIO или файловый объект
                    # wav_buffer уже является BytesIO, но нужно убедиться, что он в начале
                    wav_buffer.seek(0)
                    
                    # Логируем размер аудио
                    audio_size = len(wav_buffer.getvalue())
                    logger.info(f"📤 Отправка в ElevenLabs: {audio_size} байт аудио, язык: {whisper_language}")
                    
                    # SDK метод возвращает пустые чанки, используем прямой HTTP вызов сразу
                    logger.info("📡 SDK возвращает пустые чанки, используем прямой HTTP вызов")
                    raise AttributeError("Используем прямой HTTP вызов")  # Переходим к HTTP вызову
                else:
                    # Если speech_to_text не найден, используем прямой HTTP вызов
                    logger.warning("speech_to_text не найден, используем прямой HTTP вызов")
                    raise AttributeError("speech_to_text not found")
                    
            except (AttributeError, Exception) as e:
                # Используем прямой HTTP вызов к ElevenLabs API
                logger.info(f"📡 Прямой HTTP вызов к ElevenLabs API")
                import requests
                wav_buffer.seek(0)  # Возвращаемся в начало буфера
                audio_data = wav_buffer.read()
                
                url = "https://api.elevenlabs.io/v1/speech-to-text"
                headers = {
                    "xi-api-key": ELEVENLABS_API_KEY
                }
                files = {
                    "file": ("audio.wav", io.BytesIO(audio_data), "audio/wav")
                }
                # Используем правильный код языка для ElevenLabs
                # Для русского языка нужно использовать "ru" (не "rus")
                language_param = "ru" if whisper_language == "ru" else whisper_language
                data = {
                    "model_id": "scribe_v1",
                    "language": language_param  # "ru" для русского языка
                }
                logger.info(f"🌐 Язык для ElevenLabs: {language_param} (было: {whisper_language})")
                
                # Дополнительно: можно попробовать без указания языка для автоопределения
                # Но сначала пробуем с явным указанием языка
                
                logger.info(f"📤 HTTP запрос: язык={language_param}, размер={len(audio_data)} байт")
                response = requests.post(url, headers=headers, files=files, data=data)
                response.raise_for_status()
                result = response.json()
                logger.info(f"📥 HTTP ответ: type={type(result).__name__}, keys={list(result.keys()) if isinstance(result, dict) else 'N/A'}")
                
                # Извлекаем текст сразу из HTTP ответа
                if isinstance(result, dict):
                    # Пробуем разные возможные поля
                    result_text = (
                        result.get('text') or 
                        result.get('transcription') or 
                        result.get('transcript') or
                        result.get('result') or
                        ""
                    )
                    
                    # Если текст есть, но выглядит странно - логируем
                    if result_text:
                        logger.info(f"✅ ElevenLabs HTTP API распознал: {result_text[:100]}...")
                        # Проверяем, не распознал ли на другом языке
                        if len(result_text) > 5:
                            # Простая проверка: если много латинских букв в русском тексте - возможно ошибка
                            latin_chars = sum(1 for c in result_text if c.isalpha() and ord(c) < 128 and c.isascii())
                            cyrillic_chars = sum(1 for c in result_text if '\u0400' <= c <= '\u04FF')
                            if latin_chars > cyrillic_chars * 2 and cyrillic_chars > 0:
                                logger.warning(f"⚠️ Возможно распознано на другом языке: латинских={latin_chars}, кириллических={cyrillic_chars}")
                    else:
                        logger.warning(f"⚠️ HTTP ответ пустой: {result}")
                else:
                    result_text = ""
                    logger.warning(f"⚠️ Неожиданный тип ответа: {type(result)}")
            
            # result_text уже собран из чанков выше, если это был итератор
            # Если result_text еще не определен, пробуем извлечь из объекта result (fallback)
            if 'result_text' not in locals():
                result_text = ""
            
            # Если текст еще пустой и есть объект result, пробуем извлечь из него
            # Также проверяем, если result - это словарь (из HTTP ответа)
            if not result_text and result is not None:
                try:
                    # Вариант 1: Если это словарь (из HTTP ответа) - проверяем первым
                    if isinstance(result, dict):
                        # HTTP API может вернуть текст в разных полях
                        result_text = (
                            result.get('text') or 
                            result.get('transcription') or 
                            result.get('transcript') or
                            result.get('result') or
                            ""
                        )
                        logger.debug(f"Извлечен текст из dict: '{result_text[:50]}...'")
                    # Вариант 2: Если это объект с атрибутом text
                    elif hasattr(result, 'text'):
                        result_text = result.text or ""
                        logger.debug(f"Извлечен текст через .text: '{result_text[:50]}...'")
                    # Вариант 3: Если это строка
                    elif isinstance(result, str):
                        result_text = result
                        logger.debug(f"Результат - строка: '{result_text[:50]}...'")
                    
                    result_text = result_text.strip() if result_text else ""
                except Exception as e:
                    logger.error(f"Ошибка при извлечении текста из объекта: {e}")
                    result_text = ""
            
            # Финальная проверка
            if result_text:
                logger.info(f"✅ ElevenLabs Scribe STT распознал: {result_text[:100]}...")
            else:
                logger.warning("⚠️ ElevenLabs вернул пустой результат после всех попыток извлечения")
                if result is not None:
                    logger.warning(f"🔍 Информация о результате: type={type(result).__name__}, text='{getattr(result, 'text', 'N/A')}', words={len(getattr(result, 'words', []))}")
            
            # Проверяем на мусорные результаты
            if result_text:
                import re
                if len(result_text) > 10:
                    unique_chars = len(set(result_text.replace(" ", "").replace("-", "").replace("а", "").replace("о", "")))
                    if unique_chars < 3 and len(result_text) > 20:
                        logger.warning(f"⚠️ ElevenLabs вернул мусорный результат: {result_text[:50]}...")
                        return ""
                    elif re.search(r'(.)\1{10,}', result_text):
                        logger.warning(f"⚠️ ElevenLabs вернул мусорный результат (длинные повторы): {result_text[:50]}...")
                        return ""
            
            return result_text
            
        except Exception as e:
            logger.error(f"Ошибка при распознавании через ElevenLabs Scribe: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return ""
    
    def _transcribe_openai(self, audio: np.ndarray, language: Optional[str]) -> str:
        """
        Распознавание через OpenAI Whisper API (более точный).
        
        Args:
            audio: Аудио массив (float32)
            language: Язык для распознавания
            
        Returns:
            Распознанный текст
        """
        try:
            import io
            import wave
            
            # Конвертируем numpy массив в WAV файл в памяти
            # Убеждаемся, что значения в правильном диапазоне перед конвертацией
            audio_clipped = np.clip(audio, -1.0, 1.0)
            audio_int16 = (audio_clipped * 32767).astype(np.int16)
            
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Моно
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(SAMPLE_RATE)
                wav_file.writeframes(audio_int16.tobytes())
            
            wav_buffer.seek(0)
            
            # Отправляем в OpenAI Whisper API с улучшенными параметрами для точности
            # ВАЖНО: Убрали prompt, так как он мог влиять на результат распознавания
            # Явно указываем русский язык для лучшей точности
            whisper_language = language if language else "ru"
            transcript = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=("audio.wav", wav_buffer, "audio/wav"),
                language=whisper_language,  # Явно указываем русский язык для лучшей точности
                response_format="text",
                temperature=0.0  # Детерминированный результат
            )
            logger.info(f"🌐 OpenAI Whisper API: язык распознавания = {whisper_language}")
            
            result = transcript.strip()
            
            # Проверяем, что результат не является текстом из prompt'а или системным сообщением
            suspicious_phrases = [
                "голосовой ассистент говорит",
                "пользователь говорит",
                "разговор с голосовым ассистентом"
            ]
            result_lower = result.lower()
            if any(phrase in result_lower for phrase in suspicious_phrases):
                logger.warning(f"⚠️ Whisper вернул подозрительный результат (возможно из prompt): {result}")
                return ""
            
            # Проверяем на мусорные результаты (повторяющиеся символы, только цифры/буквы)
            if len(result) > 10:
                # Проверяем, не состоит ли результат в основном из повторяющихся символов
                unique_chars = len(set(result.replace(" ", "").replace("-", "").replace("а", "").replace("о", "")))
                if unique_chars < 3 and len(result) > 20:
                    logger.warning(f"⚠️ Whisper вернул мусорный результат (много повторений): {result[:50]}...")
                    return ""
                
                # Проверяем на длинные последовательности одинаковых символов
                import re
                if re.search(r'(.)\1{10,}', result):  # Один символ повторяется 10+ раз
                    logger.warning(f"⚠️ Whisper вернул мусорный результат (длинные повторы): {result[:50]}...")
                    return ""
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка при распознавании через OpenAI Whisper: {e}")
            return ""
    
    def _transcribe_local(self, audio: np.ndarray, language: Optional[str]) -> str:
        """
        Распознавание через локальную модель faster-whisper.
        
        Args:
            audio: Аудио массив (float32)
            language: Язык для распознавания
            
        Returns:
            Распознанный текст
        """
        try:
            # УЛУЧШЕННЫЕ параметры для максимальной точности
            whisper_language = language if language else "ru"
            logger.info(f"🌐 Локальная модель Whisper: язык распознавания = {whisper_language}")
            segments, info = self.model.transcribe(
                audio,
                language=whisper_language,  # Явно указываем русский
                beam_size=5,  # Увеличено для лучшей точности
                best_of=5,  # Увеличено для лучшей точности
                temperature=0.0,  # Детерминированный результат
                vad_filter=False,  # Отключаем VAD фильтр, так как мы уже фильтруем на клиенте
                vad_parameters=dict(min_silence_duration_ms=500),
                condition_on_previous_text=True,  # Включаем для лучшего контекста
                initial_prompt="Разговор на русском языке.",  # Короткая подсказка для модели
                compression_ratio_threshold=2.4,  # Фильтр для улучшения качества
                logprob_threshold=-1.0,  # Фильтр низкокачественных результатов
                no_speech_threshold=0.6  # Порог для определения речи
            )
            
            # Собираем весь текст из сегментов
            text_parts = []
            for segment in segments:
                text = segment.text.strip()
                if text:  # Пропускаем пустые сегменты
                    text_parts.append(text)
                    logger.debug(f"Сегмент: {text} (время: {segment.start:.2f}-{segment.end:.2f} сек, вероятность: {segment.no_speech_prob:.2f})")
            
            full_text = " ".join(text_parts).strip()
            
            # Проверяем результат на мусор
            if full_text:
                import re
                if len(full_text) > 10:
                    # Проверяем на повторяющиеся символы
                    unique_chars = len(set(full_text.replace(" ", "").replace("-", "").replace("а", "").replace("о", "")))
                    if unique_chars < 3 and len(full_text) > 20:
                        logger.warning(f"⚠️ Локальная модель вернула мусорный результат (много повторений): {full_text[:50]}...")
                        full_text = ""
                    elif re.search(r'(.)\1{10,}', full_text):
                        logger.warning(f"⚠️ Локальная модель вернула мусорный результат (длинные повторы): {full_text[:50]}...")
                        full_text = ""
            
            if not full_text:
                logger.warning("Whisper не распознал речь (пустой результат)")
                # Попробуем без указания языка (автоопределение)
                if language:
                    logger.info("Пробую распознавание с автоопределением языка...")
                    segments_auto, info_auto = self.model.transcribe(
                        audio,
                        language=None,  # Автоопределение языка
                        beam_size=5,
                        best_of=5,
                        temperature=0.0,
                        vad_filter=False,
                        initial_prompt="Разговор на русском языке."
                    )
                    text_parts_auto = []
                    for segment in segments_auto:
                        text = segment.text.strip()
                        if text:
                            text_parts_auto.append(text)
                    full_text = " ".join(text_parts_auto).strip()
                    if full_text:
                        logger.info(f"🎤 Распознано (автоопределение языка): {full_text}")
            
            return full_text
            
        except Exception as e:
            logger.error(f"Ошибка при локальном распознавании: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return ""
    
    def transcribe_stream(self, audio_stream, language: Optional[str] = "ru") -> str:
        """
        Распознает речь из потока аудио данных (для будущего использования).
        
        Args:
            audio_stream: Поток аудио данных
            language: Язык для распознавания
            
        Returns:
            Распознанный текст
        """
        # Для потокового распознавания можно использовать метод segments_iter
        # Пока используем обычное распознавание
        audio_array = np.concatenate(list(audio_stream))
        return self.transcribe(audio_array, language)

