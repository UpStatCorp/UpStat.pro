import os
import httpx
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from models import ZoomMeeting, User
from schemas import CreateMeetingRequest
import logging

logger = logging.getLogger(__name__)


class ZoomService:
    def __init__(self):
        self.client_id = os.getenv("ZOOM_CLIENT_ID")
        self.client_secret = os.getenv("ZOOM_CLIENT_SECRET")
        self.account_id = os.getenv("ZOOM_ACCOUNT_ID")
        self.base_url = "https://api.zoom.us/v2"
        self.access_token = None
        self.token_expires_at = None
        
        if not all([self.client_id, self.client_secret, self.account_id]):
            logger.warning("Zoom API credentials not fully configured")
    
    async def _get_access_token(self) -> str:
        """Получает access_token через Server-to-Server OAuth"""
        try:
            # Проверяем, не истек ли текущий токен
            if (self.access_token and self.token_expires_at and 
                datetime.utcnow() < self.token_expires_at):
                return self.access_token
            
            # Получаем новый токен согласно Zoom API документации
            token_url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={self.account_id}"
            
            # Создаем Basic Auth заголовок с base64 кодировкой
            import base64
            auth_string = f"{self.client_id}:{self.client_secret}"
            auth_header = base64.b64encode(auth_string.encode()).decode()
            
            headers = {
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    token_url,
                    headers=headers,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    token_data = response.json()
                    self.access_token = token_data["access_token"]
                    # Токен истекает через expires_in секунд
                    self.token_expires_at = datetime.utcnow() + timedelta(
                        seconds=token_data.get("expires_in", 3600)
                    )
                    logger.info("Successfully obtained new Zoom access token")
                    return self.access_token
                else:
                    logger.error(f"Failed to get access token: {response.status_code} - {response.text}")
                    raise Exception(f"Failed to get access token: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"Error getting access token: {e}")
            raise
    
    async def create_meeting(self, db: Session, user: User, meeting_data: CreateMeetingRequest) -> ZoomMeeting:
        """Создает новую Zoom встречу"""
        try:
            # Получаем OAuth access token
            token = await self._get_access_token()
            
            # Формируем данные для Zoom API
            zoom_meeting_data = {
                "topic": meeting_data.topic,
                "type": 2,  # Scheduled meeting
                "start_time": meeting_data.start_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "duration": meeting_data.duration_minutes,
                "timezone": "Europe/Moscow",
                "password": meeting_data.password or self._generate_password(),
                "settings": {
                    "host_video": True,
                    "participant_video": True,
                    "join_before_host": True,
                    "mute_upon_entry": False,
                    "waiting_room": False,
                    "audio": "both"
                }
            }
            
            # Вызываем Zoom API
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/users/me/meetings",
                    json=zoom_meeting_data,
                    headers=headers
                )
                
                if response.status_code != 201:
                    logger.error(f"Zoom API error: {response.status_code} - {response.text}")
                    raise Exception(f"Failed to create Zoom meeting: {response.status_code}")
                
                zoom_response = response.json()
                
                # Создаем запись в нашей БД
                db_meeting = ZoomMeeting(
                    user_id=user.id,
                    meeting_id=zoom_response["id"],
                    topic=meeting_data.topic,
                    start_time=meeting_data.start_time,
                    duration_minutes=meeting_data.duration_minutes,
                    status="scheduled",
                    join_url=zoom_response["join_url"],
                    password=zoom_response.get("password"),
                    ai_agent_enabled=meeting_data.ai_agent_enabled
                )
                
                db.add(db_meeting)
                db.commit()
                db.refresh(db_meeting)
                
                logger.info(f"Created Zoom meeting {db_meeting.id} for user {user.id}")
                return db_meeting
                
        except Exception as e:
            logger.error(f"Error creating Zoom meeting: {str(e)}")
            raise
    
    def _generate_password(self) -> str:
        """Генерирует случайный пароль для встречи"""
        import random
        import string
        return ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
    async def get_meeting_info(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        """Получает информацию о встрече из Zoom API"""
        try:
            token = await self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/meetings/{meeting_id}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"Failed to get meeting info: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting meeting info: {str(e)}")
            return None
    
    async def update_meeting_status(self, db: Session, meeting_id: int, status: str) -> bool:
        """Обновляет статус встречи в БД"""
        try:
            meeting = db.query(ZoomMeeting).filter(ZoomMeeting.id == meeting_id).first()
            if meeting:
                meeting.status = status
                db.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating meeting status: {str(e)}")
            return False
    
    async def delete_meeting(self, db: Session, meeting_id: int, zoom_meeting_id: str) -> bool:
        """Удаляет встречу из Zoom и нашей БД"""
        try:
            # Удаляем из Zoom API
            token = await self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.base_url}/meetings/{zoom_meeting_id}",
                    headers=headers
                )
                
                if response.status_code not in [200, 204]:
                    logger.warning(f"Failed to delete from Zoom: {response.status_code}")
            
            # Удаляем из нашей БД
            meeting = db.query(ZoomMeeting).filter(ZoomMeeting.id == meeting_id).first()
            if meeting:
                db.delete(meeting)
                db.commit()
                return True
            return False
            
        except Exception as e:
            logger.error(f"Error deleting meeting: {str(e)}")
            return False

    async def update_meeting_agent_status(self, zoom_meeting_id: str, agent_active: bool) -> bool:
        """Обновляет статус агента для встречи в БД"""
        try:
            from database import get_db
            db = next(get_db())
            
            meeting = db.query(ZoomMeeting).filter(ZoomMeeting.meeting_id == zoom_meeting_id).first()
            if meeting:
                meeting.agent_active = agent_active
                db.commit()
                logger.info(f"Updated agent status for meeting {zoom_meeting_id}: {agent_active}")
                return True
            else:
                logger.warning(f"Meeting not found: {zoom_meeting_id}")
                return False
        except Exception as e:
            logger.error(f"Error updating agent status: {str(e)}")
            return False
