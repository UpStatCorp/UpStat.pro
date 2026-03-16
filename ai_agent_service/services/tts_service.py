import asyncio
import logging
from typing import Optional, Union
import httpx
import json
from config import settings
try:
    from elevenlabs import VoiceSettings
    from elevenlabs.client import ElevenLabs
    _HAS_ELEVEN_SDK = True
except Exception:
    _HAS_ELEVEN_SDK = False

logger = logging.getLogger(__name__)


class TTSService:
    """Сервис для преобразования текста в речь"""
    
    def __init__(self):
        self.elevenlabs_api_key = settings.elevenlabs_api_key
        self.elevenlabs_voice_id = settings.elevenlabs_voice_id
        self.elevenlabs_model_id = settings.elevenlabs_model_id
        self.elevenlabs_url = "https://api.elevenlabs.io/v1"
        
        # Выбираем TTS провайдера
        if self.elevenlabs_api_key:
            self.tts_provider = "elevenlabs"
            logger.info("Using ElevenLabs for TTS")
        else:
            self.tts_provider = "xtts"
            logger.info("Using XTTS for TTS")
    
    async def health_check(self) -> bool:
        """Проверка доступности TTS сервиса"""
        try:
            if self.tts_provider == "elevenlabs":
                # Проверяем ElevenLabs API
                headers = {"xi-api-key": self.elevenlabs_api_key}
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.elevenlabs_url}/voices",
                        headers=headers,
                        timeout=10.0
                    )
                    return response.status_code == 200
            else:
                # Для XTTS проверяем локальную доступность
                return True
        except Exception as e:
            logger.error(f"TTS service health check failed: {e}")
            return False
    
    async def synthesize_speech(
        self, 
        text: str, 
        voice_id: Optional[str] = None,
        language: str = "ru"
    ) -> Optional[bytes]:
        """Синтезирует речь из текста"""
        try:
            if self.tts_provider == "elevenlabs":
                return await self._synthesize_with_elevenlabs(text, voice_id)
            else:
                return await self._synthesize_with_xtts(text, voice_id, language)
        except Exception as e:
            logger.error(f"Speech synthesis failed: {e}")
            return None
    
    async def _synthesize_with_elevenlabs(
        self, 
        text: str, 
        voice_id: Optional[str] = None
    ) -> Optional[bytes]:
        """Синтез речи через ElevenLabs API"""
        try:
            if not voice_id:
                voice_id = self.elevenlabs_voice_id
            
            # Параметры для синтеза
            data = {
                "text": text,
                "model_id": self.elevenlabs_model_id,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.0,
                    "use_speaker_boost": True
                }
            }
            
            headers = {
                "xi-api-key": self.elevenlabs_api_key,
                "Content-Type": "application/json",
                # Запрашиваем MP3 для наилучшей совместимости очереди клипов
                "Accept": "audio/mpeg"
            }
            
            # Вызываем ElevenLabs API
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.elevenlabs_url}/text-to-speech/{voice_id}",
                    json=data,
                    headers=headers,
                    timeout=60.0
                )
                
                if response.status_code == 200:
                    return response.content
                else:
                    logger.error(f"ElevenLabs API error: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"ElevenLabs synthesis failed: {e}")
            return None
    
    async def _synthesize_with_xtts(
        self, 
        text: str, 
        voice_id: Optional[str] = None,
        language: str = "ru"
    ) -> Optional[bytes]:
        """Синтез речи через XTTS (локальный)"""
        try:
            # TODO: Реализовать интеграцию с локальным XTTS
            # Пока возвращаем заглушку
            logger.warning("XTTS integration not implemented yet")
            return None
            
        except Exception as e:
            logger.error(f"XTTS synthesis failed: {e}")
            return None
    
    async def synthesize_stream(
        self, 
        text_stream: asyncio.Queue,
        voice_id: Optional[str] = None,
        language: str = "ru"
    ) -> asyncio.Queue:
        """Синтезирует речь из потока текста в реальном времени"""
        audio_queue = asyncio.Queue()
        
        async def process_text():
            while True:
                try:
                    # Получаем текст
                    text_chunk = await text_stream.get()
                    
                    if text_chunk is None:  # Сигнал остановки
                        break
                    
                    # Используем асинхронный стриминг через httpx (более надежно чем SDK в потоке)
                    if self.tts_provider == "elevenlabs":
                        try:
                            import time
                            _voice = voice_id or self.elevenlabs_voice_id
                            url = f"{self.elevenlabs_url}/text-to-speech/{_voice}/stream"
                            
                            headers = {
                                "xi-api-key": self.elevenlabs_api_key or "",
                                "Accept": "audio/mpeg",
                                "Content-Type": "application/json",
                            }
                            
                            payload = {
                                "text": text_chunk,
                                "model_id": settings.elevenlabs_model_id,
                                "voice_settings": {
                                    "stability": 0.2,
                                    "similarity_boost": 0.8,
                                    "use_speaker_boost": True,
                                    "style": 0.0,
                                },
                                "output_format": "mp3_22050_32",
                            }
                            
                            # Асинхронный стриминг через httpx
                            request_start = time.time()
                            logger.info(f"[TTS_PERF] Sending request to ElevenLabs at {request_start} (text length: {len(text_chunk)})")
                            
                            async with httpx.AsyncClient(timeout=30.0) as client:
                                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                                    first_chunk_time = None
                                    chunk_count = 0
                                    total_bytes = 0
                                    
                                    if resp.status_code == 200:
                                        async for chunk in resp.aiter_bytes(8192):
                                            if chunk:
                                                if first_chunk_time is None:
                                                    first_chunk_time = time.time()
                                                    first_chunk_delta = first_chunk_time - request_start
                                                    logger.info(f"[TTS_PERF] First chunk from ElevenLabs after {first_chunk_delta:.3f}s")
                                                
                                                chunk_count += 1
                                                total_bytes += len(chunk)
                                                await audio_queue.put({
                                                    "audio": chunk,
                                                    "timestamp": time.time()
                                                })
                                        
                                        stream_end = time.time()
                                        total_duration = stream_end - request_start
                                        logger.info(f"[TTS_PERF] Stream completed: {chunk_count} chunks, {total_bytes} bytes, duration: {total_duration:.3f}s")
                                        # успешный stream — переходим к следующему чанку
                                        continue
                                    else:
                                        error_text = await resp.aread()
                                        logger.error(f"ElevenLabs streaming failed: {resp.status_code}, {error_text.decode()}")
                        except Exception as e:
                            logger.error(f"ElevenLabs streaming failed: {e}")

                    # Фолбэк: обычный пакетный синтез (или если SDK недоступен)
                    audio_data = await self.synthesize_speech(
                        text_chunk,
                        voice_id,
                        language,
                    )
                    if audio_data:
                        await audio_queue.put({
                            "audio": audio_data,
                            "timestamp": asyncio.get_event_loop().time(),
                        })
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in text stream processing: {e}")
                    continue
        
        # Запускаем обработку в фоне
        task = asyncio.create_task(process_text())
        
        # Возвращаем очередь с аудио и задачу для отмены
        return audio_queue, task
    
    async def get_available_voices(self) -> list[dict]:
        """Получает список доступных голосов"""
        try:
            if self.tts_provider == "elevenlabs":
                headers = {"xi-api-key": self.elevenlabs_api_key}
                
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.elevenlabs_url}/voices",
                        headers=headers,
                        timeout=10.0
                    )
                    
                    if response.status_code == 200:
                        voices_data = response.json()
                        return [
                            {
                                "id": voice["voice_id"],
                                "name": voice["name"],
                                "category": voice.get("category", "unknown"),
                                "language": voice.get("labels", {}).get("language", "unknown")
                            }
                            for voice in voices_data.get("voices", [])
                        ]
                    else:
                        logger.error(f"Failed to get voices: {response.status_code}")
                        return []
            else:
                # Для XTTS возвращаем базовые голоса
                return [
                    {"id": "default", "name": "Default Voice", "category": "local", "language": "ru"}
                ]
                
        except Exception as e:
            logger.error(f"Error getting voices: {e}")
            return []
    
    async def create_custom_voice(
        self, 
        name: str, 
        description: str,
        audio_file: bytes
    ) -> Optional[str]:
        """Создает кастомный голос (только для ElevenLabs)"""
        try:
            if self.tts_provider != "elevenlabs":
                logger.warning("Custom voice creation only available with ElevenLabs")
                return None
            
            # Создаем временный файл для аудио
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
                temp_file.write(audio_file)
                temp_file_path = temp_file.name
            
            try:
                # Отправляем запрос на создание голоса
                headers = {"xi-api-key": self.elevenlabs_api_key}
                
                data = {
                    "name": name,
                    "description": description
                }
                
                files = {
                    "files": ("voice.wav", open(temp_file_path, "rb"), "audio/wav")
                }
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.elevenlabs_url}/voices/add",
                        data=data,
                        files=files,
                        headers=headers,
                        timeout=60.0
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        voice_id = result.get("voice_id")
                        logger.info(f"Created custom voice: {voice_id}")
                        return voice_id
                    else:
                        logger.error(f"Failed to create voice: {response.status_code} - {response.text}")
                        return None
                        
            finally:
                # Удаляем временный файл
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                    
        except Exception as e:
            logger.error(f"Error creating custom voice: {e}")
            return None
    
    async def delete_custom_voice(self, voice_id: str) -> bool:
        """Удаляет кастомный голос (только для ElevenLabs)"""
        try:
            if self.tts_provider != "elevenlabs":
                logger.warning("Custom voice deletion only available with ElevenLabs")
                return False
            
            headers = {"xi-api-key": self.elevenlabs_api_key}
            
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.elevenlabs_url}/voices/{voice_id}",
                    headers=headers,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    logger.info(f"Deleted custom voice: {voice_id}")
                    return True
                else:
                    logger.error(f"Failed to delete voice: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error deleting custom voice: {e}")
            return False
    
    async def close(self):
        """Закрывает сервис"""
        try:
            # Очищаем ресурсы если нужно
            pass
        except Exception as e:
            logger.error(f"Error closing TTS service: {e}")
