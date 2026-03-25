import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from services.stt_service import STTService
from services.llm_service import LLMService
from services.tts_service import TTSService
from services.zoom_client import ZoomClient
from services.websocket_client import ws_client
from services.pii_redactor import redact_pii

logger = logging.getLogger(__name__)


class AudioPipeline:
    """Асинхронный пайплайн для обработки аудио в реальном времени"""
    
    def __init__(
        self,
        stt_service: STTService,
        llm_service: LLMService,
        tts_service: TTSService,
        zoom_client: ZoomClient
    ):
        self.stt_service = stt_service
        self.llm_service = llm_service
        self.tts_service = tts_service
        self.zoom_client = zoom_client
        
        # Состояние пайплайнов для каждой встречи
        self.meeting_pipelines: Dict[str, Dict[str, Any]] = {}
        
        # Настройки пайплайна
        self.audio_chunk_duration = 0.5  # секунды
        self.max_response_delay = 2.0  # максимальная задержка ответа
        self.min_audio_length = 0.1  # минимальная длина аудио для обработки
        
        # Настройки контекста
        self.max_context_messages = 10  # максимальное количество сообщений в контексте
        
        # Статистика
        self.stats = {
            "total_audio_processed": 0,
            "total_responses_generated": 0,
            "average_response_time": 0.0
        }
        
        # Отслеживание TTS
        self.tts_playing: Dict[str, bool] = {}
        
        # Настраиваем обработчики WebSocket
        ws_client.set_audio_handler(self.handle_websocket_audio)
        ws_client.set_status_handler(self.handle_websocket_status)
    
    async def start_processing(
        self, 
        meeting_id: str, 
        user_id: int,
        meeting_topic: Optional[str] = None
    ):
        """Запускает обработку аудио для встречи"""
        try:
            if meeting_id in self.meeting_pipelines:
                logger.warning(f"Pipeline already running for meeting {meeting_id}")
                return
            
            logger.info(f"Starting audio pipeline for meeting {meeting_id}")
            
            # Инициализируем пайплайн для встречи
            pipeline = {
                "meeting_id": meeting_id,
                "user_id": user_id,
                "topic": meeting_topic,
                "status": "running",
                "start_time": asyncio.get_event_loop().time(),
                "audio_queue": asyncio.Queue(),
                "transcript_queue": asyncio.Queue(),
                "response_queue": asyncio.Queue(),
                "audio_queue_task": None,
                "transcript_task": None,
                "response_task": None,
                "conversation_context": [],
                "full_transcript": "",
                "last_activity": asyncio.get_event_loop().time()
            }
            
            self.meeting_pipelines[meeting_id] = pipeline
            
            # Запускаем задачи обработки
            pipeline["audio_queue_task"] = asyncio.create_task(
                self._process_audio_queue(meeting_id)
            )
            
            pipeline["transcript_task"] = asyncio.create_task(
                self._process_transcript_queue(meeting_id)
            )
            
            pipeline["response_task"] = asyncio.create_task(
                self._process_response_queue(meeting_id)
            )
            
            logger.info(f"Audio pipeline started for meeting {meeting_id}")
            
        except Exception as e:
            logger.error(f"Error starting audio pipeline for meeting {meeting_id}: {e}")
    
    async def stop_processing(self, meeting_id: str):
        """Останавливает обработку аудио для встречи"""
        try:
            if meeting_id not in self.meeting_pipelines:
                return
            
            pipeline = self.meeting_pipelines[meeting_id]
            pipeline["status"] = "stopping"
            
            logger.info(f"Stopping audio pipeline for meeting {meeting_id}")
            
            # Отменяем все задачи
            if pipeline["audio_queue_task"]:
                pipeline["audio_queue_task"].cancel()
            
            if pipeline["transcript_task"]:
                pipeline["transcript_task"].cancel()
            
            if pipeline["response_task"]:
                pipeline["response_task"].cancel()
            
            # Ждем завершения задач
            await asyncio.gather(
                pipeline["audio_queue_task"],
                pipeline["transcript_task"],
                pipeline["response_task"],
                return_exceptions=True
            )
            
            # Удаляем пайплайн
            del self.meeting_pipelines[meeting_id]
            
            logger.info(f"Audio pipeline stopped for meeting {meeting_id}")
            
        except Exception as e:
            logger.error(f"Error stopping audio pipeline for meeting {meeting_id}: {e}")
    
    async def add_audio_chunk(
        self, 
        meeting_id: str, 
        audio_data: bytes, 
        timestamp: float
    ):
        """Добавляет аудио чанк в очередь обработки"""
        try:
            if meeting_id not in self.meeting_pipelines:
                logger.warning(f"No pipeline for meeting {meeting_id}")
                return
            
            pipeline = self.meeting_pipelines[meeting_id]
            
            # Проверяем минимальную длину аудио
            if len(audio_data) < 100:  # Минимальный размер в байтах
                return
            
            # Добавляем в очередь
            await pipeline["audio_queue"].put({
                "audio_data": audio_data,
                "timestamp": timestamp,
                "size": len(audio_data)
            })
            
            # Обновляем статистику
            self.stats["total_audio_processed"] += len(audio_data)
            pipeline["last_activity"] = asyncio.get_event_loop().time()
            
        except Exception as e:
            logger.error(f"Error adding audio chunk: {e}")
    
    async def _process_audio_queue(self, meeting_id: str):
        """Обрабатывает очередь аудио чанков"""
        try:
            pipeline = self.meeting_pipelines[meeting_id]
            audio_queue = pipeline["audio_queue"]
            
            while pipeline["status"] == "running":
                try:
                    # Получаем аудио чанк
                    audio_chunk = await asyncio.wait_for(
                        audio_queue.get(), 
                        timeout=1.0
                    )
                    
                    if audio_chunk is None:  # Сигнал остановки
                        break
                    
                    # Транскрибируем аудио
                    start_time = asyncio.get_event_loop().time()
                    text = await self.stt_service.transcribe_audio(
                        audio_chunk["audio_data"],
                        language="ru"
                    )
                    transcription_time = asyncio.get_event_loop().time() - start_time
                    
                    if text and len(text.strip()) > 0:
                        text = redact_pii(text.strip())
                        # Добавляем в очередь транскриптов
                        await pipeline["transcript_queue"].put({
                            "text": text,
                            "timestamp": audio_chunk["timestamp"],
                            "transcription_time": transcription_time
                        })
                        
                        logger.debug(f"Transcribed: '{text}' (took {transcription_time:.2f}s)")
                    else:
                        logger.debug("No text transcribed from audio chunk")
                    
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error processing audio chunk: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in audio queue processing: {e}")
    
    async def _process_transcript_queue(self, meeting_id: str):
        """Обрабатывает очередь транскриптов"""
        try:
            pipeline = self.meeting_pipelines[meeting_id]
            transcript_queue = pipeline["transcript_queue"]
            
            while pipeline["status"] == "running":
                try:
                    # Получаем транскрипт
                    transcript_chunk = await asyncio.wait_for(
                        transcript_queue.get(), 
                        timeout=1.0
                    )
                    
                    if transcript_chunk is None:  # Сигнал остановки
                        break
                    
                    # Добавляем в полный транскрипт
                    pipeline["full_transcript"] += f" {transcript_chunk['text']}"
                    
                    # Добавляем в контекст разговора
                    pipeline["conversation_context"].append({
                        "role": "user",
                        "content": transcript_chunk["text"],
                        "timestamp": transcript_chunk["timestamp"]
                    })
                    
                    # Ограничиваем размер контекста
                    if len(pipeline["conversation_context"]) > self.max_context_messages * 2:
                        pipeline["conversation_context"] = pipeline["conversation_context"][-self.max_context_messages:]
                    
                    # Генерируем ответ
                    start_time = asyncio.get_event_loop().time()
                    response = await self.llm_service.generate_response(
                        transcript_chunk["text"],
                        conversation_context=pipeline["conversation_context"],
                        meeting_topic=pipeline["topic"]
                    )
                    response_generation_time = asyncio.get_event_loop().time() - start_time
                    
                    if response:
                        # Добавляем ответ в контекст
                        pipeline["conversation_context"].append({
                            "role": "assistant",
                            "content": response,
                            "timestamp": asyncio.get_event_loop().time()
                        })
                        
                        # Добавляем в очередь ответов
                        await pipeline["response_queue"].put({
                            "text": response,
                            "timestamp": asyncio.get_event_loop().time(),
                            "generation_time": response_generation_time
                        })
                        
                        logger.debug(f"Generated response: '{response[:50]}...' (took {response_generation_time:.2f}s)")
                    else:
                        logger.warning("Failed to generate response")
                    
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error processing transcript: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in transcript queue processing: {e}")
    
    async def _process_response_queue(self, meeting_id: str):
        """Обрабатывает очередь ответов"""
        try:
            pipeline = self.meeting_pipelines[meeting_id]
            response_queue = pipeline["response_queue"]
            
            while pipeline["status"] == "running":
                try:
                    # Получаем ответ
                    response_chunk = await asyncio.wait_for(
                        response_queue.get(), 
                        timeout=1.0
                    )
                    
                    if response_chunk is None:  # Сигнал остановки
                        break
                    
                    # Синтезируем речь
                    start_time = asyncio.get_event_loop().time()
                    audio_data = await self.tts_service.synthesize_speech(
                        response_chunk["text"],
                        language="ru"
                    )
                    synthesis_time = asyncio.get_event_loop().time() - start_time
                    
                    if audio_data:
                        # Отправляем аудио в Zoom
                        success = await self.zoom_client.send_audio(meeting_id, audio_data)
                        
                        if success:
                            # Обновляем статистику
                            self.stats["total_responses_generated"] += 1
                            total_time = response_chunk["generation_time"] + synthesis_time
                            self.stats["average_response_time"] = (
                                (self.stats["average_response_time"] * (self.stats["total_responses_generated"] - 1) + total_time) /
                                self.stats["total_responses_generated"]
                            )
                            
                            logger.debug(f"Sent audio response (took {total_time:.2f}s total)")
                        else:
                            logger.error("Failed to send audio to Zoom")
                    else:
                        logger.warning("Failed to synthesize speech")
                    
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error processing response: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in response queue processing: {e}")
    
    async def get_transcript(self, meeting_id: str) -> str:
        """Получает полный транскрипт встречи"""
        try:
            if meeting_id not in self.meeting_pipelines:
                return ""
            
            pipeline = self.meeting_pipelines[meeting_id]
            return pipeline.get("full_transcript", "").strip()
            
        except Exception as e:
            logger.error(f"Error getting transcript: {e}")
            return ""
    
    async def get_conversation_context(self, meeting_id: str) -> List[Dict[str, Any]]:
        """Получает контекст разговора"""
        try:
            if meeting_id not in self.meeting_pipelines:
                return []
            
            pipeline = self.meeting_pipelines[meeting_id]
            return pipeline.get("conversation_context", [])
            
        except Exception as e:
            logger.error(f"Error getting conversation context: {e}")
            return []
    
    def get_pipeline_stats(self, meeting_id: str) -> Dict[str, Any]:
        """Получает статистику пайплайна"""
        try:
            if meeting_id not in self.meeting_pipelines:
                return {}
            
            pipeline = self.meeting_pipelines[meeting_id]
            current_time = asyncio.get_event_loop().time()
            
            return {
                "meeting_id": meeting_id,
                "status": pipeline["status"],
                "running_time": current_time - pipeline["start_time"],
                "last_activity": current_time - pipeline["last_activity"],
                "context_messages": len(pipeline["conversation_context"]),
                "transcript_length": len(pipeline["full_transcript"])
            }
            
        except Exception as e:
            logger.error(f"Error getting pipeline stats: {e}")
            return {}
    
    def get_global_stats(self) -> Dict[str, Any]:
        """Получает глобальную статистику"""
        return {
            "total_audio_processed": self.stats["total_audio_processed"],
            "total_responses_generated": self.stats["total_responses_generated"],
            "average_response_time": self.stats["average_response_time"],
            "active_meetings": len(self.meeting_pipelines)
        }
    
    async def handle_websocket_audio(self, meeting_number: str, data: Dict[str, Any]):
        """Обработка аудио данных от SDK Runner через WebSocket"""
        try:
            audio_buffer = data.get('audioBuffer', [])
            timestamp = data.get('timestamp', 0)
            
            if not audio_buffer:
                return
            
            # Обрабатываем разные типы данных
            if isinstance(audio_buffer, list):
                # Если это список чисел, конвертируем в bytes
                audio_data = bytes(audio_buffer)
            elif isinstance(audio_buffer, str):
                # Если это строка, конвертируем в bytes
                audio_data = audio_buffer.encode('utf-8')
            elif isinstance(audio_buffer, bytes):
                # Если это уже bytes, используем как есть
                audio_data = audio_buffer
            else:
                # Для других типов пытаемся конвертировать в bytes
                audio_data = bytes(audio_buffer)
            
            # Проверяем, воспроизводится ли TTS
            if self.tts_playing.get(meeting_number, False):
                # Barge-in: останавливаем TTS если пользователь говорит
                await self.handle_barge_in(meeting_number)
            
            # Добавляем аудио в пайплайн
            await self.add_audio_chunk(meeting_number, audio_data, timestamp)
            
        except Exception as e:
            logger.error(f"Error handling WebSocket audio: {e}")
            logger.error(f"Audio buffer type: {type(audio_buffer)}, value: {audio_buffer}")
    
    async def handle_websocket_status(self, meeting_number: str, data: Dict[str, Any]):
        """Обработка статусных сообщений от SDK Runner"""
        try:
            status = data.get('status')
            logger.info(f"SDK Runner status update for meeting {meeting_number}: {status}")
            
            if meeting_number in self.meeting_pipelines:
                pipeline = self.meeting_pipelines[meeting_number]
                pipeline['sdk_status'] = status
                
        except Exception as e:
            logger.error(f"Error handling WebSocket status: {e}")
    
    async def handle_barge_in(self, meeting_number: str):
        """Обработка barge-in (прерывание TTS пользователем)"""
        try:
            if not self.tts_playing.get(meeting_number, False):
                return
            
            logger.info(f"Barge-in detected for meeting {meeting_number}, stopping TTS")
            
            # Останавливаем TTS
            self.tts_playing[meeting_number] = False
            
            # Отправляем команду остановки в SDK Runner
            await ws_client.stop_tts(meeting_number)
            
        except Exception as e:
            logger.error(f"Error handling barge-in: {e}")
    
    async def send_tts_to_zoom(self, meeting_number: str, audio_data: bytes):
        """Отправка TTS аудио в Zoom через SDK Runner"""
        try:
            # Отмечаем, что TTS воспроизводится
            self.tts_playing[meeting_number] = True
            
            # Отправляем аудио в SDK Runner
            success = await ws_client.send_tts_audio(meeting_number, audio_data)
            
            if not success:
                logger.error(f"Failed to send TTS audio to meeting {meeting_number}")
                self.tts_playing[meeting_number] = False
                
            return success
            
        except Exception as e:
            logger.error(f"Error sending TTS to Zoom: {e}")
            self.tts_playing[meeting_number] = False
            return False

    async def close(self):
        """Закрывает все пайплайны"""
        try:
            # Останавливаем все активные пайплайны
            for meeting_id in list(self.meeting_pipelines.keys()):
                await self.stop_processing(meeting_id)
            
            # Отключаемся от WebSocket
            await ws_client.disconnect()
            
            logger.info("All audio pipelines closed")
            
        except Exception as e:
            logger.error(f"Error closing audio pipelines: {e}")
