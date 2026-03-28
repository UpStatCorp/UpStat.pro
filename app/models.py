from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, Float, func as sa_func
from sqlalchemy.orm import relationship, Mapped, mapped_column
from database import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Может быть None для OAuth пользователей
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)  # user, admin, manager, sale_manager
    # Google OAuth поля
    google_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    is_oauth_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Время последнего входа
    # Поля подписки/лимитов
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    free_analyses_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    analyses_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    premium_granted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    premium_granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    conversations = relationship("Conversation", back_populates="user")
    zoom_meetings = relationship("ZoomMeeting", back_populates="user")
    custom_meetings = relationship("CustomMeeting", back_populates="creator")
    training_plans = relationship("AnalysisTrainingPlan", back_populates="user")
    training_sessions = relationship("TrainingSession", back_populates="user")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    # Команды
    managed_teams = relationship("Team", foreign_keys="Team.manager_id", back_populates="manager")
    team_memberships = relationship("TeamMember", back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="Мой диалог")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # None => бот
    role: Mapped[str] = mapped_column(String(10))  # 'user' | 'bot'
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")
    attachments = relationship("Attachment", back_populates="message", cascade="all, delete-orphan")


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512))  # путь в uploads/
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    message = relationship("Message", back_populates="attachments")


class ZoomMeeting(Base):
    __tablename__ = "zoom_meetings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    meeting_id: Mapped[str] = mapped_column(String(255), unique=True)  # Zoom ID
    topic: Mapped[str] = mapped_column(String(255))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled, active, completed
    join_url: Mapped[str] = mapped_column(String(512))
    password: Mapped[Optional[str]] = mapped_column(String(20))
    ai_agent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    agent_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="zoom_meetings")
    transcript = relationship("MeetingTranscript", back_populates="meeting", uselist=False)


class MeetingTranscript(Base):
    __tablename__ = "meeting_transcripts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("zoom_meetings.id"), index=True, nullable=False)
    full_transcript: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    participants_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    meeting = relationship("ZoomMeeting", back_populates="transcript")


class Prompt(Base):
    __tablename__ = "prompts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # Название промпта (например, "sales_audit")
    title: Mapped[str] = mapped_column(String(255), nullable=False)  # Человекочитаемое название
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Описание промпта
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Содержимое промпта
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # Версия промпта
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # Активен ли промпт
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)  # Кто создал
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    creator = relationship("User")


class CustomMeeting(Base):
    __tablename__ = "custom_meetings"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    meeting_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="created")  # created, active, ended
    max_participants: Mapped[int] = mapped_column(Integer, default=10)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    password: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ai_agent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Связи
    creator = relationship("User", back_populates="custom_meetings")
    participants = relationship("MeetingParticipant", back_populates="meeting")
    transcript = relationship("CustomMeetingTranscript", back_populates="meeting", uselist=False)


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("custom_meetings.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="participant")  # participant, moderator
    
    # Связи
    meeting = relationship("CustomMeeting", back_populates="participants")
    user = relationship("User")


class CustomMeetingTranscript(Base):
    __tablename__ = "custom_meeting_transcripts"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("custom_meetings.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связи
    meeting = relationship("CustomMeeting", back_populates="transcript")


class AnalysisTrainingPlan(Base):
    """План тренировок на основе анализа звонка"""
    __tablename__ = "analysis_training_plans"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    report_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    recommendations_json: Mapped[str] = mapped_column(Text, nullable=False)
    total_trainings: Mapped[int] = mapped_column(Integer, default=0)
    completed_trainings: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, completed, archived
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", back_populates="training_plans")
    report_message = relationship("Message")
    trainings = relationship("Training", back_populates="plan", cascade="all, delete-orphan")


class Training(Base):
    """Отдельная тренировка (этап) в плане"""
    __tablename__ = "trainings"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("analysis_training_plans.id"), index=True, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(50), default="custom")
    checklist_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    status: Mapped[str] = mapped_column(String(20), default="locked")  # locked, available, in_progress, completed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    best_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связи
    plan = relationship("AnalysisTrainingPlan", back_populates="trainings")
    sessions = relationship("TrainingSession", back_populates="training", cascade="all, delete-orphan")


class TrainingSession(Base):
    """Сессия прохождения тренировки (каждая попытка)"""
    __tablename__ = "training_sessions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checklist_results_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    user_responses_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_questions_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Новые поля для голосовой тренировки
    session_type: Mapped[str] = mapped_column(String(50), default="text")
    websocket_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    conversation_history_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    
    # Связи
    training = relationship("Training", back_populates="sessions")
    user = relationship("User", back_populates="training_sessions")
    voice_messages = relationship("VoiceTrainingMessage", back_populates="session", cascade="all, delete-orphan")


class VoiceTrainingMessage(Base):
    """Отдельные сообщения в голосовой тренировке"""
    __tablename__ = "voice_training_messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("training_sessions.id"), index=True, nullable=False)
    
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user, assistant
    text: Mapped[str] = mapped_column(Text, nullable=False)  # Распознанный/сгенерированный текст
    audio_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Путь к аудио файлу (опционально)
    
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Integer, nullable=True)  # Длительность аудио
    
    # Связи
    session = relationship("TrainingSession", back_populates="voice_messages")


class Notification(Base):
    """Уведомления пользователя"""
    __tablename__ = "notifications"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'training_ready', 'analysis_complete', etc.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str] = mapped_column(String(10), default="🔔")  # Эмодзи иконка
    
    link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # URL для перехода
    link_text: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Текст кнопки
    
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Метаданные (JSON-строка для дополнительной информации)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Связи
    user = relationship("User", back_populates="notifications")


class CRMIntegration(Base):
    """Настройки интеграции пользователя с CRM системами"""
    __tablename__ = "crm_integrations"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    
    crm_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'amocrm', 'bitrix24', 'salesforce', etc.
    crm_name: Mapped[str] = mapped_column(String(255), nullable=False)  # Название для отображения
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # OAuth данные (зашифрованные)
    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Зашифрованный токен
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Зашифрованный refresh токен
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Дополнительные настройки CRM
    crm_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Домен для AmoCRM/Bitrix
    client_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    client_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Зашифрованный
    webhook_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    webhook_secret: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # Статистика
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    recordings_count: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Sync state
    initial_sync_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    sync_cursor_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Связи
    user = relationship("User", back_populates="crm_integrations")
    recordings = relationship("CRMRecording", back_populates="integration", cascade="all, delete-orphan")


class CRMRecording(Base):
    """Записи звонков из CRM систем"""
    __tablename__ = "crm_recordings"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("crm_integrations.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    
    # ID записи в CRM
    crm_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    crm_call_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # ID звонка в CRM
    
    # Тип записи: call (звонок) или chat (чат из открытых линий)
    record_type: Mapped[str] = mapped_column(String(20), default="call")  # 'call', 'chat'
    chat_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Текст переписки для чатов
    
    # Метаданные звонка
    call_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    direction: Mapped[str] = mapped_column(String(20), default="unknown")  # 'inbound', 'outbound', 'unknown'
    
    # Участники
    manager_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    manager_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    client_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    client_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    client_company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # URL и файл
    recording_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # URL в CRM
    local_file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Путь после скачивания
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Статусы
    sync_status: Mapped[str] = mapped_column(String(20), default="available")  # 'available', 'downloading', 'analyzing', 'completed', 'failed'
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Связь с анализом
    conversation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    training_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("analysis_training_plans.id"), nullable=True)
    
    # Результаты анализа
    analysis_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    
    # Дополнительные данные из CRM (JSON)
    crm_metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Связи с CRM-сущностями
    deal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("crm_deals.id"), nullable=True, index=True)
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("crm_leads.id"), nullable=True, index=True)
    contact_crm_id: Mapped[Optional[int]] = mapped_column(ForeignKey("crm_contacts.id"), nullable=True, index=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Связи
    integration = relationship("CRMIntegration", back_populates="recordings")
    user = relationship("User", back_populates="crm_recordings")
    conversation = relationship("Conversation", back_populates="crm_recording", uselist=False)
    training_plan = relationship("AnalysisTrainingPlan", foreign_keys=[training_plan_id])
    deal = relationship("CRMDeal", foreign_keys=[deal_id])
    lead = relationship("CRMLead", foreign_keys=[lead_id])
    contact_crm = relationship("CRMContact", foreign_keys=[contact_crm_id])


# Добавляем обратные связи в User
User.crm_integrations = relationship("CRMIntegration", back_populates="user", cascade="all, delete-orphan")
User.crm_recordings = relationship("CRMRecording", back_populates="user")

# Добавляем обратную связь в Conversation
Conversation.crm_recording = relationship("CRMRecording", back_populates="conversation", uselist=False)


class Team(Base):
    """Команда"""
    __tablename__ = "teams"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    manager_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связи
    manager = relationship("User", foreign_keys=[manager_id], back_populates="managed_teams")
    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    invitations = relationship("TeamInvitation", back_populates="team", cascade="all, delete-orphan")
    script = relationship("TeamScript", back_populates="team", uselist=False, cascade="all, delete-orphan")


class TeamMember(Base):
    """Участник команды"""
    __tablename__ = "team_members"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    role_in_team: Mapped[str] = mapped_column(String(50), default="member", nullable=False)  # "member", "assistant_manager", "manager"
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Уникальное ограничение: один пользователь не может быть дважды в одной команде
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)
    
    # Связи
    team = relationship("Team", back_populates="members")
    user = relationship("User", back_populates="team_memberships")


class TeamInvitation(Base):
    """Приглашение в команду"""
    __tablename__ = "team_invitations"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    invited_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)  # PENDING, ACCEPTED, CANCELED, EXPIRED
    invited_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    accepted_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Связи
    team = relationship("Team", back_populates="invitations")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
    accepted_by = relationship("User", foreign_keys=[accepted_user_id])


class TeamScript(Base):
    """Скрипт команды (аналог чеклиста)"""
    __tablename__ = "team_scripts"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    script_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON в формате чеклиста
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Оригинальный текст/Word контент
    uploaded_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    team = relationship("Team", back_populates="script")
    uploaded_by = relationship("User")


class TrainingConversionMetric(Base):
    """Метрики конверсии между этапами тренировок"""
    __tablename__ = "training_conversion_metrics"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    
    # Период метрики
    metric_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    period_type: Mapped[str] = mapped_column(String(20), default="daily")  # daily, weekly, monthly
    
    # Конверсии между этапами
    # Формат: {"stage_1_to_2": 0.85, "stage_2_to_3": 0.72, ...}
    conversion_rates_json: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Общие метрики
    total_plans: Mapped[int] = mapped_column(Integer, default=0)
    active_plans: Mapped[int] = mapped_column(Integer, default=0)
    completed_plans: Mapped[int] = mapped_column(Integer, default=0)
    total_trainings: Mapped[int] = mapped_column(Integer, default=0)
    completed_trainings: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User")
    team = relationship("Team")


class PasswordResetToken(Base):
    """Токены для восстановления пароля"""
    __tablename__ = "password_reset_tokens"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Связи
    user = relationship("User")


class TrainingErrorCorrection(Base):
    """Ошибки и коррекции из анализа звонков"""
    __tablename__ = "training_errors_corrections"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), index=True, nullable=True)
    
    # Связь с анализом
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True, nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True, nullable=False)
    
    # Данные об ошибке
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)  # "greeting", "objection_handling", etc.
    error_description: Mapped[str] = mapped_column(Text, nullable=False)
    error_severity: Mapped[str] = mapped_column(String(20), default="medium")  # low, medium, high, critical
    
    # Коррекция
    correction_text: Mapped[str] = mapped_column(Text, nullable=False)
    correction_applied: Mapped[bool] = mapped_column(Boolean, default=False)  # Применена ли коррекция
    correction_applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Связь с тренировкой (если создана)
    training_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("analysis_training_plans.id"), nullable=True)
    training_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trainings.id"), nullable=True)
    
    # Метаданные
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User")
    team = relationship("Team")
    conversation = relationship("Conversation")
    message = relationship("Message")
    training_plan = relationship("AnalysisTrainingPlan")
    training = relationship("Training")


# ─── Аналитика по параметрам (Слой 2) ───────────────────────

class ParameterDefinition(Base):
    """Справочник параметров анализа звонка (dictionary-driven)"""
    __tablename__ = "parameter_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)  # number, boolean, text
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=sa_func.now())

    values = relationship("ParameterValue", back_populates="parameter")


class ParameterValue(Base):
    """Значения параметров по конкретному звонку"""
    __tablename__ = "parameter_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True, nullable=False)
    parameter_id: Mapped[int] = mapped_column(ForeignKey("parameter_definitions.id"), index=True, nullable=False)

    value_number: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    value_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value_bool: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=80)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=sa_func.now())

    __table_args__ = (
        UniqueConstraint("conversation_id", "parameter_id", name="uq_conv_param"),
    )

    conversation = relationship("Conversation")
    parameter = relationship("ParameterDefinition", back_populates="values")


class CRMManagerMapping(Base):
    """Привязка менеджеров CRM к аккаунтам UpStat"""
    __tablename__ = "crm_manager_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("crm_integrations.id", ondelete="CASCADE"), index=True, nullable=False)
    crm_manager_name: Mapped[str] = mapped_column(String(255), nullable=False)
    crm_manager_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=sa_func.now())

    __table_args__ = (
        UniqueConstraint("integration_id", "crm_manager_name", name="uq_integ_crm_mgr"),
    )

    integration = relationship("CRMIntegration")
    user = relationship("User")


class AnalyticsMessage(Base):
    """Сообщения чата аналитики (отдельно от основного чата)"""
    __tablename__ = "analytics_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # user, bot
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=sa_func.now())

    user = relationship("User")


# ─── CRM-сущности из Битрикс24 ──────────────────────────────

class CRMDeal(Base):
    """Сделки из CRM"""
    __tablename__ = "crm_deals"
    __table_args__ = (UniqueConstraint("integration_id", "bitrix_id", name="uq_deal_integ_bx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("crm_integrations.id", ondelete="CASCADE"), index=True, nullable=False)
    bitrix_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    stage_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    stage_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    category_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    opportunity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency_id: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_won: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    probability: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assigned_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assigned_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    company_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    close_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    loss_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    crm_metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    integration = relationship("CRMIntegration")
    products = relationship("CRMDealProduct", back_populates="deal", cascade="all, delete-orphan")


class CRMLead(Base):
    """Лиды из CRM"""
    __tablename__ = "crm_leads"
    __table_args__ = (UniqueConstraint("integration_id", "bitrix_id", name="uq_lead_integ_bx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("crm_integrations.id", ondelete="CASCADE"), index=True, nullable=False)
    bitrix_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    opportunity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency_id: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    assigned_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assigned_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    converted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    converted_deal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    converted_contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    converted_company_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    crm_metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    integration = relationship("CRMIntegration")


class CRMContact(Base):
    """Контакты из CRM"""
    __tablename__ = "crm_contacts"
    __table_args__ = (UniqueConstraint("integration_id", "bitrix_id", name="uq_contact_integ_bx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("crm_integrations.id", ondelete="CASCADE"), index=True, nullable=False)
    bitrix_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    second_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    post: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    phone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    company_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assigned_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assigned_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    crm_metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    integration = relationship("CRMIntegration")


class CRMCompany(Base):
    """Компании из CRM"""
    __tablename__ = "crm_companies"
    __table_args__ = (UniqueConstraint("integration_id", "bitrix_id", name="uq_company_integ_bx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("crm_integrations.id", ondelete="CASCADE"), index=True, nullable=False)
    bitrix_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    phone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    web: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    revenue: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency_id: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    assigned_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assigned_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    crm_metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    integration = relationship("CRMIntegration")


class CRMDealProduct(Base):
    """Товары в сделке"""
    __tablename__ = "crm_deal_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("crm_deals.id", ondelete="CASCADE"), index=True, nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    product_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    discount_sum: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tax_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sum_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    deal = relationship("CRMDeal", back_populates="products")


class CRMActivity(Base):
    """Активности CRM (звонки, письма, встречи, задачи)"""
    __tablename__ = "crm_activities"
    __table_args__ = (UniqueConstraint("integration_id", "bitrix_id", name="uq_activity_integ_bx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("crm_integrations.id", ondelete="CASCADE"), index=True, nullable=False)
    bitrix_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    type_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner_type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    responsible_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    responsible_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    direction: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    crm_metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    integration = relationship("CRMIntegration")
