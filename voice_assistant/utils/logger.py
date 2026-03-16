"""
Модуль для настройки логирования.
"""

import logging
import sys
from ..config import LOG_LEVEL

def setup_logger(name: str = "voice_assistant") -> logging.Logger:
    """
    Настраивает и возвращает логгер с форматированием.
    
    Args:
        name: Имя логгера
        
    Returns:
        Настроенный логгер
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL))
    
    # Очищаем существующие обработчики
    logger.handlers.clear()
    
    # Создаем обработчик для консоли
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, LOG_LEVEL))
    
    # Форматирование логов
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    return logger

