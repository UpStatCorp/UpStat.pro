"""
Модуль определения активности речи (Voice Activity Detection).
Использует пороговый метод на основе RMS амплитуды для определения, говорит ли пользователь.
"""

import numpy as np
from typing import Optional
from .utils.audio_utils import calculate_rms
from .config import VAD_THRESHOLD, VAD_FRAME_MS, SAMPLE_RATE, SILENCE_DURATION_MS

class VAD:
    """
    Класс для определения активности речи на основе порога громкости.
    """
    
    def __init__(self, threshold: float = None, silence_duration_ms: int = None):
        """
        Инициализирует VAD детектор.
        
        Args:
            threshold: Порог RMS для определения речи (0.0 - 1.0)
            silence_duration_ms: Длительность тишины для завершения записи
        """
        self.threshold = threshold or VAD_THRESHOLD
        self.silence_duration_ms = silence_duration_ms or SILENCE_DURATION_MS
        self.frame_samples = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)
        self.silence_samples = int(SAMPLE_RATE * self.silence_duration_ms / 1000)
        
        # Состояние детектора
        self.is_speaking = False
        self.silence_counter = 0
        self.speech_started = False
        
    def process_chunk(self, audio_chunk: np.ndarray) -> bool:
        """
        Обрабатывает чанк аудио и определяет, есть ли в нем речь.
        
        Args:
            audio_chunk: Чанк аудио данных
            
        Returns:
            True если обнаружена речь, False иначе
        """
        if len(audio_chunk) == 0:
            return False
        
        # Вычисляем RMS для этого чанка
        rms = calculate_rms(audio_chunk)
        
        # Определяем, превышает ли уровень порог
        has_speech = rms > self.threshold
        
        if has_speech:
            self.is_speaking = True
            self.speech_started = True
            self.silence_counter = 0
            return True
        else:
            # Если речь уже началась, отсчитываем тишину
            if self.speech_started:
                self.silence_counter += len(audio_chunk)
                
                # Если тишина длится достаточно долго, считаем речь завершенной
                if self.silence_counter >= self.silence_samples:
                    self.is_speaking = False
                    self.speech_started = False
                    self.silence_counter = 0
                    return False
            return False
    
    def reset(self):
        """Сбрасывает состояние детектора."""
        self.is_speaking = False
        self.silence_counter = 0
        self.speech_started = False
    
    def is_active(self) -> bool:
        """
        Возвращает текущее состояние активности речи.
        
        Returns:
            True если пользователь говорит, False иначе
        """
        return self.is_speaking

