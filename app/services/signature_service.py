import os
import jwt
import time
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ZoomSignatureService:
    """Сервис для генерации JWT подписей для Zoom Meeting SDK"""
    
    def __init__(self):
        self.sdk_key = os.getenv("ZOOM_SDK_KEY")
        self.sdk_secret = os.getenv("ZOOM_SDK_SECRET")
        
        if not self.sdk_key or not self.sdk_secret:
            logger.warning("ZOOM_SDK_KEY and ZOOM_SDK_SECRET not set - SDK signature generation disabled")
            self.sdk_key = None
            self.sdk_secret = None
    
    def generate_zoom_signature(
        self, 
        meeting_number: str, 
        role: int = 0,  # 0 = attendee, 1 = host
        user_identity: Optional[str] = None
    ) -> str:
        """
        Генерирует JWT подпись для Zoom Meeting SDK
        
        Args:
            meeting_number: Номер Zoom встречи
            role: Роль пользователя (0 = attendee, 1 = host)
            user_identity: Уникальный идентификатор пользователя
            
        Returns:
            JWT подпись для подключения к встрече
        """
        try:
            if not self.sdk_key or not self.sdk_secret:
                raise Exception("SDK credentials not configured")
            
            # Время создания и истечения токена (<= 2 минуты по требованиям SDK)
            iat = int(time.time())
            exp = iat + 120
            
            # Payload для JWT
            payload = {
                "iss": self.sdk_key,  # SDK Key как issuer
                "exp": exp,           # Время истечения
                "iat": iat,           # Время создания
                "aud": "zoom",        # Audience - всегда "zoom"
                "appKey": self.sdk_key,
                "tokenExp": exp,
                "alg": "HS256"
            }
            
            # Дополнительные поля для Meeting SDK
            if user_identity:
                payload["userIdentity"] = user_identity
            
            # Генерируем JWT токен
            signature = jwt.encode(
                payload,
                self.sdk_secret,
                algorithm="HS256"
            )
            
            logger.info(f"Generated Zoom signature for meeting {meeting_number}, role {role}")
            
            return signature
            
        except Exception as e:
            logger.error(f"Error generating Zoom signature: {e}")
            raise Exception(f"Failed to generate Zoom signature: {str(e)}")
    
    def validate_signature(self, signature: str) -> bool:
        """
        Проверяет валидность JWT подписи
        
        Args:
            signature: JWT подпись для проверки
            
        Returns:
            True если подпись валидна, False иначе
        """
        try:
            decoded = jwt.decode(
                signature,
                self.sdk_secret,
                algorithms=["HS256"],
                audience="zoom"
            )
            
            # Проверяем время истечения
            exp = decoded.get("exp")
            if exp and exp < time.time():
                return False
                
            return True
            
        except jwt.ExpiredSignatureError:
            logger.warning("Zoom signature has expired")
            return False
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid Zoom signature: {e}")
            return False
        except Exception as e:
            logger.error(f"Error validating signature: {e}")
            return False


# Глобальный экземпляр сервиса
signature_service = ZoomSignatureService()
