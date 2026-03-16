"""
Сервис кеширования для улучшения производительности
"""
import hashlib
import json
import pickle
from typing import Any, Optional, Callable
from datetime import datetime, timedelta
from functools import wraps
import logging

logger = logging.getLogger(__name__)

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available, using in-memory cache")


class CacheService:
    """Сервис кеширования с поддержкой Redis и in-memory fallback"""
    
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_client = None
        self.memory_cache = {}
        self.cache_metadata = {}  # Для TTL в memory cache
        
        if REDIS_AVAILABLE and redis_url:
            try:
                self.redis_client = redis.from_url(redis_url, decode_responses=False)
                self.redis_client.ping()
                logger.info("Redis cache initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to connect to Redis: {e}, using memory cache")
                self.redis_client = None
    
    def _generate_key(self, prefix: str, *args, **kwargs) -> str:
        """Генерация ключа кеша на основе аргументов"""
        key_data = {
            "args": args,
            "kwargs": sorted(kwargs.items())
        }
        key_hash = hashlib.md5(
            json.dumps(key_data, sort_keys=True).encode()
        ).hexdigest()
        return f"{prefix}:{key_hash}"
    
    def get(self, key: str) -> Optional[Any]:
        """Получить значение из кеша"""
        try:
            if self.redis_client:
                # Redis cache
                value = self.redis_client.get(key)
                if value:
                    return pickle.loads(value)
            else:
                # Memory cache
                if key in self.memory_cache:
                    metadata = self.cache_metadata.get(key)
                    if metadata and metadata['expires_at'] > datetime.utcnow():
                        return self.memory_cache[key]
                    else:
                        # Expired
                        self.delete(key)
            return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Установить значение в кеш"""
        try:
            if self.redis_client:
                # Redis cache
                pickled = pickle.dumps(value)
                return self.redis_client.setex(key, ttl, pickled)
            else:
                # Memory cache
                self.memory_cache[key] = value
                self.cache_metadata[key] = {
                    'expires_at': datetime.utcnow() + timedelta(seconds=ttl)
                }
                return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """Удалить значение из кеша"""
        try:
            if self.redis_client:
                return bool(self.redis_client.delete(key))
            else:
                if key in self.memory_cache:
                    del self.memory_cache[key]
                if key in self.cache_metadata:
                    del self.cache_metadata[key]
                return True
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False
    
    def delete_pattern(self, pattern: str) -> int:
        """Удалить все ключи по шаблону"""
        try:
            if self.redis_client:
                keys = self.redis_client.keys(pattern)
                if keys:
                    return self.redis_client.delete(*keys)
                return 0
            else:
                # Memory cache - простой wildcard matching
                import fnmatch
                keys_to_delete = [
                    k for k in self.memory_cache.keys()
                    if fnmatch.fnmatch(k, pattern)
                ]
                for key in keys_to_delete:
                    self.delete(key)
                return len(keys_to_delete)
        except Exception as e:
            logger.error(f"Cache delete pattern error: {e}")
            return 0
    
    def clear_expired(self):
        """Очистка устаревших записей (только для memory cache)"""
        if self.redis_client:
            return  # Redis сам управляет TTL
        
        now = datetime.utcnow()
        expired_keys = [
            key for key, metadata in self.cache_metadata.items()
            if metadata['expires_at'] <= now
        ]
        for key in expired_keys:
            self.delete(key)
        
        logger.info(f"Cleared {len(expired_keys)} expired cache entries")
    
    def get_stats(self) -> dict:
        """Получить статистику кеша"""
        if self.redis_client:
            try:
                info = self.redis_client.info('stats')
                return {
                    'type': 'redis',
                    'hits': info.get('keyspace_hits', 0),
                    'misses': info.get('keyspace_misses', 0),
                    'keys': self.redis_client.dbsize()
                }
            except:
                return {'type': 'redis', 'error': 'Unable to fetch stats'}
        else:
            return {
                'type': 'memory',
                'keys': len(self.memory_cache)
            }


# Глобальный экземпляр
_cache_service: Optional[CacheService] = None


def get_cache_service(redis_url: Optional[str] = None) -> CacheService:
    """Получить глобальный экземпляр сервиса кеширования"""
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService(redis_url)
    return _cache_service


def cached(prefix: str, ttl: int = 3600):
    """
    Декоратор для кеширования результатов функций
    
    Args:
        prefix: префикс ключа кеша
        ttl: время жизни в секундах
    
    Usage:
        @cached('user_profile', ttl=300)
        def get_user_profile(user_id: int):
            # expensive operation
            return profile
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            cache = get_cache_service()
            cache_key = cache._generate_key(prefix, *args, **kwargs)
            
            # Попытка получить из кеша
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                logger.debug(f"Cache hit: {cache_key}")
                return cached_value
            
            # Вычисляем значение
            logger.debug(f"Cache miss: {cache_key}")
            result = func(*args, **kwargs)
            
            # Сохраняем в кеш
            cache.set(cache_key, result, ttl)
            return result
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            cache = get_cache_service()
            cache_key = cache._generate_key(prefix, *args, **kwargs)
            
            # Попытка получить из кеша
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                logger.debug(f"Cache hit: {cache_key}")
                return cached_value
            
            # Вычисляем значение
            logger.debug(f"Cache miss: {cache_key}")
            result = await func(*args, **kwargs)
            
            # Сохраняем в кеш
            cache.set(cache_key, result, ttl)
            return result
        
        # Определяем, асинхронная ли функция
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def invalidate_cache(prefix: str, *args, **kwargs):
    """Инвалидация кеша для конкретных аргументов"""
    cache = get_cache_service()
    cache_key = cache._generate_key(prefix, *args, **kwargs)
    cache.delete(cache_key)
    logger.info(f"Cache invalidated: {cache_key}")


def invalidate_cache_pattern(pattern: str):
    """Инвалидация всех ключей по шаблону"""
    cache = get_cache_service()
    count = cache.delete_pattern(pattern)
    logger.info(f"Cache pattern invalidated: {pattern} ({count} keys)")

