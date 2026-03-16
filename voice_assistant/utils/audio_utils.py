"""
Утилиты для работы с аудио.
"""

import numpy as np
from typing import Optional, Callable

# Опциональный импорт sounddevice (нужен только для локального воспроизведения)
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

try:
    from ..config import SAMPLE_RATE, CHUNK_SIZE
except ImportError:
    from .config import SAMPLE_RATE, CHUNK_SIZE

def list_audio_devices():
    """Выводит список доступных аудио устройств."""
    if not SOUNDDEVICE_AVAILABLE:
        print("⚠️ sounddevice не установлен, список устройств недоступен")
        return
    print("\nДоступные аудио устройства:")
    print(sd.query_devices())

def get_default_input_device() -> Optional[int]:
    """Возвращает индекс устройства ввода по умолчанию."""
    if not SOUNDDEVICE_AVAILABLE:
        return None
    try:
        return sd.default.device[0]
    except:
        return None

def normalize_audio(audio: np.ndarray) -> np.ndarray:
    """
    Нормализует аудио сигнал.
    
    Args:
        audio: Аудио массив
        
    Returns:
        Нормализованный аудио массив
    """
    if len(audio) == 0:
        return audio
    
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        return audio / max_val * 0.95  # Оставляем небольшой запас
    return audio

def audio_to_int16(audio: np.ndarray) -> np.ndarray:
    """
    Конвертирует аудио в формат int16.
    
    Args:
        audio: Аудио массив (float32, значения от -1.0 до 1.0)
        
    Returns:
        Аудио массив в формате int16
    """
    return (audio * 32767).astype(np.int16)

def int16_to_audio(audio: np.ndarray) -> np.ndarray:
    """
    Конвертирует аудио из формата int16 в float32.
    
    Args:
        audio: Аудио массив (int16)
        
    Returns:
        Аудио массив в формате float32 (значения от -1.0 до 1.0)
    """
    return audio.astype(np.float32) / 32767.0

def calculate_rms(audio: np.ndarray) -> float:
    """
    Вычисляет RMS (Root Mean Square) амплитуду аудио сигнала.
    Используется для определения уровня громкости.
    
    Args:
        audio: Аудио массив
        
    Returns:
        RMS значение (0.0 - 1.0)
    """
    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio**2)))

