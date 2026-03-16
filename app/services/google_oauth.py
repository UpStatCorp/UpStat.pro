import os
import requests
from typing import Optional, Dict, Any
from fastapi import HTTPException
from sqlalchemy.orm import Session
from models import User
from database import get_db


class GoogleOAuthService:
    def __init__(self):
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        # Автоматически определяем redirect URI в зависимости от окружения
        environment = os.getenv("ENVIRONMENT", "development")
        if environment == "production":
            self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "https://upstat.pro/auth/google/callback")
        else:
            self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
        
        if not self.client_id or not self.client_secret:
            raise ValueError("Google OAuth credentials not configured")
    
    def get_authorization_url(self, state: str = None) -> str:
        """Генерирует URL для авторизации Google OAuth"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile",
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent"
        }
        
        if state:
            params["state"] = state
            
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query_string}"
    
    def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Обменивает код авторизации на токен доступа"""
        token_url = "https://oauth2.googleapis.com/token"
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri
        }
        
        response = requests.post(token_url, data=data)
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to exchange code for token: {response.text}"
            )
        
        return response.json()
    
    def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Получает информацию о пользователе от Google"""
        user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = requests.get(user_info_url, headers=headers)
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to get user info: {response.text}"
            )
        
        return response.json()
    
    def get_or_create_user(self, db: Session, google_user_info: Dict[str, Any]) -> tuple:
        """Получает существующего пользователя или создает нового.
        Возвращает кортеж (user, is_new) — is_new=True если пользователь создан впервые."""
        google_id = google_user_info.get("id")
        email = google_user_info.get("email", "").lower().strip()
        name = google_user_info.get("name", "").strip()
        avatar = google_user_info.get("picture")
        
        if not google_id or not email:
            raise HTTPException(
                status_code=400,
                detail="Invalid Google user information"
            )
        
        # Ищем пользователя по Google ID
        user = db.query(User).filter(User.google_id == google_id).first()
        
        if user:
            # Обновляем информацию пользователя
            user.email = email
            user.name = name
            if avatar:
                user.avatar = avatar
            user.is_oauth_user = True
            db.commit()
            return user, False
        
        # Ищем пользователя по email (возможна связка аккаунтов)
        user = db.query(User).filter(User.email == email).first()
        
        if user:
            # Связываем существующий аккаунт с Google
            user.google_id = google_id
            user.is_oauth_user = True
            if avatar:
                user.avatar = avatar
            db.commit()
            return user, False
        
        # Создаем нового пользователя
        user = User(
            email=email,
            name=name,
            google_id=google_id,
            is_oauth_user=True,
            avatar=avatar,
            role="user"
        )
        
        db.add(user)
        db.flush()
        
        # Создаем первую беседу для нового пользователя
        from models import Conversation
        conversation = Conversation(user_id=user.id, title="Мой первый диалог")
        db.add(conversation)
        db.commit()
        
        return user, True


# Создаем глобальный экземпляр сервиса
google_oauth_service = GoogleOAuthService()
