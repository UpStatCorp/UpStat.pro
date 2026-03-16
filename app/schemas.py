from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, validator


class CreateMeetingRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255, description="Тема встречи")
    start_time: datetime = Field(..., description="Время начала встречи")
    duration_minutes: int = Field(default=60, ge=15, le=480, description="Длительность в минутах")
    password: Optional[str] = Field(None, max_length=20, description="Пароль для входа")
    ai_agent_enabled: bool = Field(default=True, description="Включить ИИ-агента")

    @validator('start_time')
    def validate_start_time(cls, v):
        now = datetime.now(timezone.utc)
        min_time = now.replace(second=0, microsecond=0)
        
        if v < min_time:
            raise ValueError('Время начала не может быть в прошлом')
        
        # Минимум через 5 минут
        min_future_time = min_time.replace(minute=min_time.minute + 5)
        if v < min_future_time:
            raise ValueError('Время начала должно быть минимум через 5 минут от текущего времени')
        
        return v


class MeetingResponse(BaseModel):
    id: int
    meeting_id: str
    topic: str
    start_time: datetime
    duration_minutes: int
    status: str
    join_url: str
    password: Optional[str]
    ai_agent_enabled: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class MeetingListResponse(BaseModel):
    meetings: list[MeetingResponse]
    total: int


class StartMeetingRequest(BaseModel):
    meeting_id: int = Field(..., description="ID встречи в нашей системе")


class MeetingTranscriptResponse(BaseModel):
    id: int
    meeting_id: int
    full_transcript: str
    summary: str
    participants_count: int
    duration_seconds: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class ZoomMeetingWithTranscript(BaseModel):
    meeting: MeetingResponse
    transcript: Optional[MeetingTranscriptResponse] = None
    
    class Config:
        from_attributes = True


# WebRTC Meeting Schemas
class CreateCustomMeetingRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255, description="Тема встречи")
    duration_minutes: int = Field(default=60, ge=15, le=480, description="Длительность в минутах")
    max_participants: int = Field(default=10, ge=2, le=100, description="Максимальное количество участников")
    password: Optional[str] = Field(None, max_length=50, description="Пароль для входа")
    ai_agent_enabled: bool = Field(default=True, description="Включить ИИ-агента")


class CustomMeetingResponse(BaseModel):
    id: int
    meeting_id: str
    topic: str
    creator_id: int
    status: str
    max_participants: int
    duration_minutes: int
    password: Optional[str]
    ai_agent_enabled: bool
    created_at: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    participants_count: int = 0
    
    class Config:
        from_attributes = True


class CustomMeetingListResponse(BaseModel):
    meetings: list[CustomMeetingResponse]
    total: int = 0


class JoinMeetingRequest(BaseModel):
    meeting_id: str = Field(..., description="ID встречи")
    password: Optional[str] = Field(None, description="Пароль для входа")


class MeetingParticipantResponse(BaseModel):
    id: int
    user_id: int
    user_name: str
    joined_at: datetime
    left_at: Optional[datetime]
    role: str
    
    class Config:
        from_attributes = True


class CustomMeetingTranscriptResponse(BaseModel):
    id: int
    meeting_id: int
    content: str
    summary: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class CustomMeetingWithTranscript(BaseModel):
    meeting: CustomMeetingResponse
    participants: list[MeetingParticipantResponse]
    transcript: Optional[CustomMeetingTranscriptResponse] = None
    
    class Config:
        from_attributes = True


class WebSocketMessage(BaseModel):
    type: str = Field(..., description="Тип сообщения")
    data: dict = Field(default_factory=dict, description="Данные сообщения")
    timestamp: Optional[datetime] = Field(default_factory=datetime.utcnow)


class AudioDataMessage(BaseModel):
    type: str = "audio_data"
    audio_data: str = Field(..., description="Аудио данные в base64")
    timestamp: float = Field(..., description="Временная метка")


class VideoDataMessage(BaseModel):
    type: str = "video_data"
    video_data: str = Field(..., description="Видео данные в base64")
    timestamp: float = Field(..., description="Временная метка")


class ChatMessage(BaseModel):
    type: str = "chat_message"
    message: str = Field(..., description="Текст сообщения")
    user_id: int = Field(..., description="ID пользователя")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
