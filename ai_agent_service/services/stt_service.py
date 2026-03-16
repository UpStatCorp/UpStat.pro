import asyncio
import logging
from typing import Optional, Union
import openai
from openai import AsyncOpenAI
import httpx
from config import settings

logger = logging.getLogger(__name__)


class STTService:
    """Сервис для преобразования речи в текст"""
    
    def __init__(self):
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.deepgram_api_key = settings.deepgram_api_key
        self.deepgram_url = "https://api.deepgram.com/v1/listen"
        
        # Выбираем STT провайдера - используем ElevenLabs
        self.stt_provider = "elevenlabs"
        logger.info("Using ElevenLabs for STT")
    
    async def health_check(self) -> bool:
        """Проверка доступности сервиса"""
        try:
            if self.stt_provider == "whisper":
                # Простая проверка OpenAI API
                return bool(settings.openai_api_key)
            elif self.stt_provider == "elevenlabs":
                # Проверка ElevenLabs API
                return bool(settings.elevenlabs_api_key)
            else:
                # Проверка Deepgram API
                return bool(self.deepgram_api_key)
        except Exception as e:
            logger.error(f"STT service health check failed: {e}")
            return False
    
    async def transcribe_audio(
        self, 
        audio_data: bytes, 
        language: str = "ru"
    ) -> Optional[str]:
        """Преобразует аудио в текст"""
        try:
            if self.stt_provider == "whisper":
                return await self._transcribe_with_whisper(audio_data, language)
            elif self.stt_provider == "elevenlabs":
                return await self._transcribe_with_elevenlabs(audio_data, language)
            else:
                return await self._transcribe_with_deepgram(audio_data, language)
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return None
    
    async def _transcribe_with_whisper(
        self, 
        audio_data: bytes, 
        language: str
    ) -> Optional[str]:
        """Транскрипция через OpenAI Whisper"""
        try:
            # Создаем временный файл для аудио
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
                temp_file.write(audio_data)
                temp_file_path = temp_file.name
            
            try:
                # Отправляем файл в Whisper API
                with open(temp_file_path, "rb") as audio_file:
                    response = await self.openai_client.audio.transcriptions.create(
                        model=settings.openai_whisper_model,
                        file=audio_file,
                        language=language,
                        response_format="text"
                    )
                
                return response.strip()
                
            finally:
                # Удаляем временный файл
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                    
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return None
    
    async def _transcribe_with_deepgram(
        self, 
        audio_data: bytes, 
        language: str
    ) -> Optional[str]:
        """Транскрипция через Deepgram API"""
        try:
            headers = {
                "Authorization": f"Token {self.deepgram_api_key}",
                "Content-Type": "audio/wav"
            }
            
            params = {
                "model": settings.deepgram_model,
                "language": settings.deepgram_language,
                "punctuate": "true",
                "diarize": "false",
                "utterances": "false"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.deepgram_url,
                    headers=headers,
                    params=params,
                    content=audio_data,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    # Извлекаем текст из ответа Deepgram
                    if "results" in result and "channels" in result["results"]:
                        transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"]
                        return transcript.strip()
                    else:
                        logger.warning("Unexpected Deepgram response format")
                        return None
                else:
                    logger.error(f"Deepgram API error: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Deepgram transcription failed: {e}")
            return None
    
    async def _transcribe_with_elevenlabs(
        self, 
        audio_data: bytes, 
        language: str
    ) -> Optional[str]:
        """Транскрипция через ElevenLabs STT API"""
        try:
            # ElevenLabs STT API endpoint
            url = "https://api.elevenlabs.io/v1/speech-to-text"
            
            # Создаем multipart form data согласно документации
            import io
            files = {
                "file": ("audio.wav", io.BytesIO(audio_data), "audio/wav")
            }
            
            data = {
                "model_id": settings.elevenlabs_stt_model,
                "language": language
            }
            
            headers = {
                "xi-api-key": settings.elevenlabs_api_key
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "").strip()
                    logger.info(f"ElevenLabs STT successful: {text}")
                    return text
                else:
                    logger.error(f"ElevenLabs STT API error: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"ElevenLabs STT transcription failed: {e}")
            return None
    
    async def transcribe_stream(
        self, 
        audio_stream: asyncio.Queue,
        language: str = "ru"
    ) -> asyncio.Queue:
        """Транскрибирует поток аудио в реальном времени"""
        transcript_queue = asyncio.Queue()
        
        async def process_audio():
            while True:
                try:
                    # Получаем аудио чанк
                    audio_chunk = await audio_stream.get()
                    
                    if audio_chunk is None:  # Сигнал остановки
                        break
                    
                    # Транскрибируем
                    text = await self.transcribe_audio(audio_chunk, language)
                    
                    if text:
                        await transcript_queue.put({
                            "text": text,
                            "timestamp": asyncio.get_event_loop().time()
                        })
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in audio stream processing: {e}")
                    continue
        
        # Запускаем обработку в фоне
        task = asyncio.create_task(process_audio())
        
        # Возвращаем очередь с транскриптами и задачу для отмены
        return transcript_queue, task
    
    def get_supported_languages(self) -> list[str]:
        """Возвращает список поддерживаемых языков"""
        if self.stt_provider == "whisper":
            return ["ru", "en", "de", "fr", "es", "it", "pt", "nl", "pl", "tr"]
        elif self.stt_provider == "elevenlabs":
            return ["ru", "en", "de", "fr", "es", "it", "pt", "nl", "pl", "tr"]
        else:
            return ["ru-RU", "en-US", "de-DE", "fr-FR", "es-ES", "it-IT", "pt-BR", "nl-NL", "pl-PL", "tr-TR"]
    
    async def close(self):
        """Закрывает сервис"""
        try:
            # Очищаем ресурсы если нужно
            pass
        except Exception as e:
            logger.error(f"Error closing STT service: {e}")
