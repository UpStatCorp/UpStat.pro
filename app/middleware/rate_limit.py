"""
Rate Limiting Middleware для защиты от злоупотребления API
"""
import time
import hashlib
from typing import Dict, Optional, Tuple
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """Простой in-memory rate limiter (для production лучше использовать Redis)"""
    
    def __init__(self):
        # Хранилище: {key: [(timestamp1, count1), (timestamp2, count2), ...]}
        self.requests: Dict[str, list] = defaultdict(list)
        self.cleanup_interval = 300  # Очистка каждые 5 минут
        self.last_cleanup = time.time()
    
    def _cleanup_old_requests(self):
        """Очистка старых запросов"""
        current_time = time.time()
        if current_time - self.last_cleanup < self.cleanup_interval:
            return
        
        # Удаляем записи старше 1 часа
        cutoff_time = current_time - 3600
        for key in list(self.requests.keys()):
            self.requests[key] = [
                (ts, count) for ts, count in self.requests[key]
                if ts > cutoff_time
            ]
            if not self.requests[key]:
                del self.requests[key]
        
        self.last_cleanup = current_time
    
    def is_rate_limited(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> Tuple[bool, Optional[int]]:
        """
        Проверить, превышен ли лимит запросов
        
        Args:
            key: Уникальный ключ (обычно IP или user_id)
            max_requests: Максимальное количество запросов
            window_seconds: Временное окно в секундах
            
        Returns:
            Tuple[bool, Optional[int]]: (превышен ли лимит, секунды до сброса)
        """
        self._cleanup_old_requests()
        
        current_time = time.time()
        cutoff_time = current_time - window_seconds
        
        # Фильтруем запросы в текущем окне
        recent_requests = [
            (ts, count) for ts, count in self.requests[key]
            if ts > cutoff_time
        ]
        
        # Подсчитываем общее количество запросов
        total_requests = sum(count for _, count in recent_requests)
        
        if total_requests >= max_requests:
            # Находим самый старый запрос в окне
            if recent_requests:
                oldest_request = min(ts for ts, _ in recent_requests)
                seconds_until_reset = int(window_seconds - (current_time - oldest_request)) + 1
                return True, seconds_until_reset
            return True, window_seconds
        
        # Добавляем текущий запрос
        self.requests[key].append((current_time, 1))
        
        return False, None
    
    def get_stats(self, key: str, window_seconds: int = 60) -> Dict[str, int]:
        """Получить статистику запросов для ключа"""
        current_time = time.time()
        cutoff_time = current_time - window_seconds
        
        recent_requests = [
            count for ts, count in self.requests[key]
            if ts > cutoff_time
        ]
        
        return {
            "total_requests": sum(recent_requests),
            "window_seconds": window_seconds,
            "max_requests": len(recent_requests)
        }


# Глобальный экземпляр rate limiter
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware для ограничения частоты запросов
    
    Правила:
    - Общий лимит: 100 запросов в минуту на IP
    - API endpoints: 30 запросов в минуту на IP
    - Аутентифицированные пользователи: 200 запросов в минуту
    - Загрузка файлов: 10 запросов в 5 минут
    """
    
    def __init__(self, app):
        super().__init__(app)
        self.limiter = rate_limiter
        
        # Правила для разных endpoints
        self.rules = {
            "default": (100, 60),  # 100 запросов в минуту
            "api": (30, 60),  # 30 запросов в минуту для API
            "upload": (10, 300),  # 10 загрузок в 5 минут
            "auth": (5, 60),  # 5 попыток входа в минуту
            "authenticated": (200, 60),  # 200 запросов в минуту для авторизованных
        }
        
        # Endpoints, которые нужно проверять особо строго
        self.strict_endpoints = {
            "/login": "auth",
            "/register": "auth",
            "/chat/send": "upload",
            "/chat_trener/send": "upload",
        }
        
        # Endpoints API
        self.api_prefixes = ["/api/", "/voice-training/", "/voice-assistant/"]
    
    def _get_client_identifier(self, request: Request) -> str:
        """Получить уникальный идентификатор клиента"""
        # Для авторизованных пользователей используем user_id
        user_id = request.session.get("user_id")
        if user_id:
            return f"user_{user_id}"
        
        # Для неавторизованных - IP адрес
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        
        # Хешируем IP для приватности (опционально)
        ip_hash = hashlib.md5(ip.encode()).hexdigest()[:16]
        return f"ip_{ip_hash}"
    
    def _get_rate_limit_rule(self, request: Request) -> Tuple[str, int, int]:
        """
        Определить правило rate limit для запроса
        
        Returns:
            Tuple[str, int, int]: (название правила, макс. запросов, окно в секундах)
        """
        path = request.url.path
        
        # Проверяем строгие endpoints
        if path in self.strict_endpoints:
            rule_name = self.strict_endpoints[path]
            max_requests, window = self.rules[rule_name]
            return rule_name, max_requests, window
        
        # Проверяем API префиксы
        for prefix in self.api_prefixes:
            if path.startswith(prefix):
                max_requests, window = self.rules["api"]
                return "api", max_requests, window
        
        # Для авторизованных пользователей - более мягкий лимит
        if request.session.get("user_id"):
            max_requests, window = self.rules["authenticated"]
            return "authenticated", max_requests, window
        
        # Дефолтное правило
        max_requests, window = self.rules["default"]
        return "default", max_requests, window
    
    async def dispatch(self, request: Request, call_next):
        """Обработка запроса с проверкой rate limit"""
        
        # Пропускаем статические файлы
        if request.url.path.startswith("/static/"):
            return await call_next(request)
        
        # Получаем идентификатор клиента
        client_id = self._get_client_identifier(request)
        
        # Определяем правило rate limit
        rule_name, max_requests, window = self._get_rate_limit_rule(request)
        
        # Формируем ключ для rate limiter
        rate_limit_key = f"{client_id}:{rule_name}"
        
        # Проверяем rate limit
        is_limited, seconds_until_reset = self.limiter.is_rate_limited(
            rate_limit_key,
            max_requests,
            window
        )
        
        if is_limited:
            logger.warning(
                f"Rate limit exceeded for {client_id} on {request.url.path}. "
                f"Rule: {rule_name} ({max_requests}/{window}s). "
                f"Reset in {seconds_until_reset}s"
            )
            
            # Возвращаем 429 Too Many Requests
            return Response(
                content=f"Слишком много запросов. Попробуйте через {seconds_until_reset} секунд",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={
                    "Retry-After": str(seconds_until_reset),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Window": str(window),
                    "X-RateLimit-Reset": str(int(time.time()) + seconds_until_reset)
                }
            )
        
        # Получаем статистику для заголовков ответа
        stats = self.limiter.get_stats(rate_limit_key, window)
        remaining = max_requests - stats["total_requests"]
        
        # Обрабатываем запрос
        response = await call_next(request)
        
        # Добавляем заголовки rate limit в ответ
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Window"] = str(window)
        
        return response


def get_rate_limiter() -> RateLimiter:
    """Получить глобальный экземпляр rate limiter"""
    return rate_limiter

