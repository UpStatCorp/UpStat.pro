"""
Централизованная система обработки ошибок
"""
import logging
from typing import Dict, Any, Optional, Callable, Type, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Категории ошибок"""
    FILE_PROCESSING = "File Processing"
    EXTERNAL_API = "External API"
    DATABASE = "Database"
    VALIDATION = "Validation"
    AUTHENTICATION = "Authentication"
    AUTHORIZATION = "Authorization"
    UNKNOWN = "Unknown"


class CustomError(Exception):
    """Базовый класс для пользовательских ошибок"""
    
    def __init__(
        self,
        message: str,
        user_message: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        self.user_message = user_message
        self.category = category
        self.details = details if details is not None else {}


class FileProcessingError(CustomError):
    """Ошибка обработки файла"""
    
    def __init__(
        self,
        message: str,
        filename: str,
        user_message: str = "Ошибка обработки файла",
        details: Optional[Dict[str, Any]] = None
    ):
        # Объединяем details с filename, если details передан
        merged_details = {"filename": filename}
        if details:
            merged_details.update(details)
        
        super().__init__(
            message,
            user_message,
            ErrorCategory.FILE_PROCESSING,
            merged_details
        )


class ExternalAPIError(CustomError):
    """Ошибка внешнего API"""
    
    def __init__(
        self,
        message: str,
        service: str,
        status_code: Optional[int] = None,
        endpoint: Optional[str] = None,
        user_message: str = "Ошибка внешнего сервиса"
    ):
        details = {
            "service": service,
            "status_code": status_code,
            "endpoint": endpoint
        }
        super().__init__(
            message,
            user_message,
            ErrorCategory.EXTERNAL_API,
            {k: v for k, v in details.items() if v is not None}
        )


class DatabaseError(CustomError):
    """Ошибка базы данных"""
    
    def __init__(
        self,
        message: str,
        operation: str,
        user_message: str = "Ошибка базы данных"
    ):
        super().__init__(
            message,
            user_message,
            ErrorCategory.DATABASE,
            {"operation": operation}
        )


class ValidationError(CustomError):
    """Ошибка валидации"""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        user_message: str = "Ошибка валидации"
    ):
        super().__init__(
            message,
            user_message,
            ErrorCategory.VALIDATION,
            {"field": field} if field else {}
        )


class AuthenticationError(CustomError):
    """Ошибка аутентификации"""
    
    def __init__(
        self,
        message: str,
        user_message: str = "Ошибка аутентификации"
    ):
        super().__init__(
            message,
            user_message,
            ErrorCategory.AUTHENTICATION
        )


class AuthorizationError(CustomError):
    """Ошибка авторизации"""
    
    def __init__(
        self,
        message: str,
        user_message: str = "Ошибка авторизации"
    ):
        super().__init__(
            message,
            user_message,
            ErrorCategory.AUTHORIZATION
        )


class ErrorHandler:
    """Класс для обработки ошибок"""
    
    @staticmethod
    def log_error(error: CustomError, context: Optional[Dict[str, Any]] = None):
        """Логирование ошибки с контекстом"""
        ctx = context if context is not None else {}
        logger.error(
            f"[{error.category.value}] {error.message} | "
            f"User Message: {error.user_message} | "
            f"Details: {error.details} | "
            f"Context: {ctx}",
            exc_info=True
        )
    
    @staticmethod
    def handle_exception(
        exception: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Обработка исключения и возврат структурированного ответа"""
        if isinstance(exception, CustomError):
            ErrorHandler.log_error(exception, context)
            return {
                "error": exception.user_message,
                "category": exception.category.value,
                "details": exception.details
            }
        else:
            # Неизвестная ошибка
            ctx = context if context is not None else {}
            logger.error(
                f"[Unknown] {str(exception)} | Context: {ctx}",
                exc_info=True
            )
            return {
                "error": "Произошла неожиданная ошибка",
                "category": ErrorCategory.UNKNOWN.value
            }
    
    @staticmethod
    def get_user_friendly_message(error: CustomError) -> str:
        """Получить понятное сообщение для пользователя"""
        return error.user_message


def handle_errors(func: Callable) -> Callable:
    """Декоратор для автоматической обработки ошибок"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except CustomError as e:
            ErrorHandler.log_error(e)
            raise
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
            raise
    
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except CustomError as e:
            ErrorHandler.log_error(e)
            raise
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
            raise
    
    import asyncio
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return wrapper


def retry_on_error(
    exceptions: Tuple[Type[Exception], ...],
    retries: int = 3,
    delay: float = 1.0
) -> Callable:
    """Декоратор для повторных попыток при ошибках"""
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < retries - 1:
                        import time
                        time.sleep(delay * (attempt + 1))
                        logger.warning(
                            f"Retry {attempt + 1}/{retries} for {func.__name__}: {e}"
                        )
                    else:
                        raise
        
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < retries - 1:
                        import asyncio
                        await asyncio.sleep(delay * (attempt + 1))
                        logger.warning(
                            f"Retry {attempt + 1}/{retries} for {func.__name__}: {e}"
                        )
                    else:
                        raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return wrapper
    
    return decorator

