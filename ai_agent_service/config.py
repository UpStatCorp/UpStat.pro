import os
try:
    from dotenv import load_dotenv
    # Явно указываем путь, если .env лежит в каталоге ai_agent_service
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(env_path, override=True)
except ImportError:
    print('WARNING: python-dotenv не установлен. Рекомендуем добавить его в requirements.txt для корректной подгрузки всех переменных .env')
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Конфигурация AI Agent Service"""
    
    # Основные настройки сервиса
    service_name: str = "ai_agent_service"
    service_port: int = 8001
    service_host: str = "0.0.0.0"
    
    # OpenAI API (GPT-4o)
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 1000
    openai_temperature: float = 0.7
    
    # ElevenLabs API (TTS и STT)
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel voice
    elevenlabs_model_id: str = "eleven_turbo_v2_5"  # Low-latency модель для минимальной задержки
    elevenlabs_stt_model: str = "scribe_v1"  # ElevenLabs STT модель
    
    # Deepgram API (STT альтернатива Whisper)
    deepgram_api_key: Optional[str] = None
    deepgram_model: str = "nova-2"
    deepgram_language: str = "ru-RU"
    
    # Whisper API (STT по умолчанию)
    openai_whisper_model: str = "whisper-1"
    
    # Zoom API (Server-to-Server OAuth)
    zoom_client_id: Optional[str] = None
    zoom_client_secret: Optional[str] = None
    zoom_account_id: Optional[str] = None
    
    # База данных
    database_url: Optional[str] = None
    
    # Настройки аудио
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_chunk_duration: float = 0.5  # секунды
    audio_buffer_size: int = 8192
    
    # Настройки пайплайна
    max_concurrent_meetings: int = 10
    response_delay_threshold: float = 2.0  # максимальная задержка ответа в секундах
    
    # Логирование
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Создаем экземпляр настроек
settings = Settings()

# Проверяем обязательные переменные
def validate_settings():
    """Проверяет корректность настроек"""
    required_vars = [
        "openai_api_key",
        "elevenlabs_api_key"
    ]
    
    # Проверяем Zoom OAuth настройки если они нужны
    zoom_vars = ["zoom_client_id", "zoom_client_secret", "zoom_account_id"]
    if any(getattr(settings, var) for var in zoom_vars):
        # Если указаны какие-то Zoom настройки, проверяем все
        for var in zoom_vars:
            if not getattr(settings, var):
                missing_vars.append(var)
    
    missing_vars = []
    for var in required_vars:
        if not getattr(settings, var):
            missing_vars.append(var)
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    
    return True


# Автоматическая валидация при импорте
try:
    validate_settings()
except ValueError as e:
    print(f"Configuration error: {e}")
    print("Please check your .env file")
