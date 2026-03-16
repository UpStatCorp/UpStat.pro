"""
Функции безопасности: хеширование паролей, валидация, CSRF защита
"""
import secrets
import hashlib
import re
from typing import Optional, Tuple
from passlib.context import CryptContext
from datetime import datetime, timedelta

# Контекст для хеширования паролей (bcrypt с автоматической миграцией)
pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12  # Увеличенное количество раундов для большей безопасности
)


def hash_password(p: str) -> str:
    """Хеширование пароля с использованием bcrypt"""
    return pwd_ctx.hash(p)


def verify_password(p: str, hashed: str) -> bool:
    """Проверка пароля против хеша"""
    return pwd_ctx.verify(p, hashed)


def validate_password_strength(password: str) -> Tuple[bool, Optional[str]]:
    """
    Проверка надежности пароля
    
    Требования:
    - Минимум 8 символов
    - Хотя бы одна заглавная буква
    - Хотя бы одна строчная буква
    - Хотя бы одна цифра
    - Хотя бы один специальный символ (опционально, но рекомендуется)
    
    Returns:
        Tuple[bool, Optional[str]]: (надежен ли пароль, сообщение об ошибке)
    """
    if len(password) < 8:
        return False, "Пароль должен содержать минимум 8 символов"
    
    if len(password) > 128:
        return False, "Пароль слишком длинный (максимум 128 символов)"
    
    if not re.search(r"[a-z]", password):
        return False, "Пароль должен содержать хотя бы одну строчную букву"
    
    if not re.search(r"[A-Z]", password):
        return False, "Пароль должен содержать хотя бы одну заглавную букву"
    
    if not re.search(r"\d", password):
        return False, "Пароль должен содержать хотя бы одну цифру"
    
    # Проверка на распространенные пароли (можно расширить)
    common_passwords = ["password", "12345678", "qwerty", "abc123", "password123"]
    if password.lower() in common_passwords:
        return False, "Этот пароль слишком распространенный. Выберите более уникальный"
    
    return True, None


def generate_csrf_token() -> str:
    """Генерация CSRF токена"""
    return secrets.token_urlsafe(32)


def validate_csrf_token(token: str, session_token: Optional[str]) -> bool:
    """
    Проверка CSRF токена
    
    Args:
        token: Токен из запроса
        session_token: Токен из сессии
        
    Returns:
        bool: Валиден ли токен
    """
    if not token or not session_token:
        return False
    
    # Используем secrets.compare_digest для защиты от timing attacks
    return secrets.compare_digest(token, session_token)


def sanitize_filename(filename: str) -> str:
    """
    Очистка имени файла от опасных символов
    
    Args:
        filename: Исходное имя файла
        
    Returns:
        str: Безопасное имя файла
    """
    # Удаляем путь, оставляем только имя файла
    filename = filename.split("/")[-1].split("\\")[-1]
    
    # Удаляем опасные символы
    filename = re.sub(r'[^\w\s\.-]', '', filename)
    
    # Ограничиваем длину
    max_length = 255
    if len(filename) > max_length:
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
        name = name[:max_length - len(ext) - 1]
        filename = f"{name}.{ext}" if ext else name
    
    return filename or "file"


def hash_sensitive_data(data: str) -> str:
    """
    Хеширование чувствительных данных (для логирования и т.д.)
    
    Args:
        data: Данные для хеширования
        
    Returns:
        str: SHA256 хеш (первые 16 символов)
    """
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def is_safe_redirect_url(url: str, allowed_hosts: Optional[list] = None) -> bool:
    """
    Проверка безопасности URL для редиректа (защита от open redirect)
    
    Args:
        url: URL для проверки
        allowed_hosts: Список разрешенных хостов
        
    Returns:
        bool: Безопасен ли URL
    """
    if not url:
        return False
    
    # Разрешаем только относительные URLs
    if url.startswith('/') and not url.startswith('//'):
        return True
    
    # Если указаны разрешенные хосты, проверяем
    if allowed_hosts:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            return parsed.netloc in allowed_hosts
        except Exception:
            return False
    
    return False


class SecurityHeaders:
    """Класс для работы с заголовками безопасности"""
    
    @staticmethod
    def get_security_headers() -> dict:
        """
        Получить заголовки безопасности для добавления в ответы
        
        Returns:
            dict: Словарь с заголовками безопасности
        """
        return {
            # Защита от XSS
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            
            # Content Security Policy (базовый, можно расширить)
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "font-src 'self' data:; "
                "connect-src 'self'"
            ),
            
            # Referrer Policy
            "Referrer-Policy": "strict-origin-when-cross-origin",
            
            # Permissions Policy
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()"
        }


def validate_email(email: str) -> Tuple[bool, Optional[str]]:
    """
    Валидация email адреса
    
    Returns:
        Tuple[bool, Optional[str]]: (валиден ли email, сообщение об ошибке)
    """
    if not email:
        return False, "Email не может быть пустым"
    
    # Базовая валидация email через regex
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return False, "Неверный формат email адреса"
    
    if len(email) > 255:
        return False, "Email слишком длинный"
    
    return True, None


def validate_phone(phone: str) -> Tuple[bool, Optional[str]]:
    """
    Валидация номера телефона
    
    Returns:
        Tuple[bool, Optional[str]]: (валиден ли номер, сообщение об ошибке)
    """
    if not phone:
        return True, None  # Телефон опционален
    
    # Удаляем пробелы и дефисы
    phone_clean = re.sub(r'[\s\-\(\)]', '', phone)
    
    # Проверяем, что остались только цифры и опционально +
    if not re.match(r'^\+?\d{10,15}$', phone_clean):
        return False, "Неверный формат номера телефона (должно быть 10-15 цифр)"
    
    return True, None
