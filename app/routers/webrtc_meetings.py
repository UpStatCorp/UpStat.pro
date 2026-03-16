import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from deps import require_user, get_db
from models import User, CustomMeeting, MeetingParticipant, CustomMeetingTranscript
from schemas import (
    CreateCustomMeetingRequest, 
    CustomMeetingResponse, 
    CustomMeetingListResponse,
    JoinMeetingRequest,
    CustomMeetingWithTranscript,
    MeetingParticipantResponse,
    CustomMeetingTranscriptResponse
)
from services.webrtc_meeting_service import webrtc_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webrtc", tags=["webrtc-meetings"])

# Создаем отдельный роутер для HTML страниц
html_router = APIRouter(prefix="/webrtc", tags=["webrtc-html"])
templates = Jinja2Templates(directory="templates")


# Удален дублирующий роут - используется только get_user_meetings ниже


@router.post("/meetings/create", response_model=dict)
async def create_meeting(
    meeting_data: CreateCustomMeetingRequest,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Создает новую WebRTC встречу"""
    try:
        result = await webrtc_service.create_meeting(
            meeting_data=meeting_data,
            creator_id=current_user.id,
            db=db
        )
        
        logger.info(f"Created WebRTC meeting {result['meeting_id']} for user {current_user.id}")
        
        return {
            "message": "Meeting created successfully",
            "meeting_id": result["meeting_id"],
            "join_url": result["join_url"],
            "status": result["status"]
        }
        
    except Exception as e:
        logger.error(f"Error creating meeting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/meetings", response_model=CustomMeetingListResponse)
async def get_user_meetings(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 20
):
    """Получает список встреч пользователя"""
    try:
        # Получаем встречи пользователя
        meetings = db.query(CustomMeeting).filter(
            CustomMeeting.creator_id == current_user.id
        ).order_by(desc(CustomMeeting.created_at)).offset(skip).limit(limit).all()
        
        # Подсчитываем общее количество
        total = db.query(CustomMeeting).filter(
            CustomMeeting.creator_id == current_user.id
        ).count()
        
        # Преобразуем в response модели
        meeting_responses = []
        for meeting in meetings:
            # Подсчитываем количество участников
            participants_count = db.query(MeetingParticipant).filter(
                MeetingParticipant.meeting_id == meeting.id
            ).count()
            
            meeting_response = CustomMeetingResponse(
                id=meeting.id,
                meeting_id=meeting.meeting_id,
                topic=meeting.topic,
                creator_id=meeting.creator_id,
                status=meeting.status,
                max_participants=meeting.max_participants,
                duration_minutes=meeting.duration_minutes,
                password=meeting.password,
                ai_agent_enabled=meeting.ai_agent_enabled,
                created_at=meeting.created_at,
                started_at=meeting.started_at,
                ended_at=meeting.ended_at,
                participants_count=participants_count
            )
            meeting_responses.append(meeting_response)
        
        return CustomMeetingListResponse(
            meetings=meeting_responses,
            total=total
        )
        
    except Exception as e:
        logger.error(f"Error getting user meetings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/meetings/{meeting_id}", response_model=CustomMeetingWithTranscript)
async def get_meeting_details(
    meeting_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получает детальную информацию о встрече"""
    try:
        # Получаем встречу
        meeting = db.query(CustomMeeting).filter(
            CustomMeeting.meeting_id == meeting_id
        ).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        # Проверяем права доступа
        if meeting.creator_id != current_user.id:
            # Проверяем, является ли пользователь участником
            participant = db.query(MeetingParticipant).filter(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.user_id == current_user.id
            ).first()
            
            if not participant:
                raise HTTPException(status_code=403, detail="Access denied")
        
        # Получаем участников
        participants = db.query(MeetingParticipant, User).join(
            User, MeetingParticipant.user_id == User.id
        ).filter(
            MeetingParticipant.meeting_id == meeting.id
        ).all()
        
        participant_responses = []
        for participant, user in participants:
            participant_response = MeetingParticipantResponse(
                id=participant.id,
                user_id=participant.user_id,
                user_name=user.name,
                joined_at=participant.joined_at,
                left_at=participant.left_at,
                role=participant.role
            )
            participant_responses.append(participant_response)
        
        # Получаем транскрипт
        transcript = db.query(CustomMeetingTranscript).filter(
            CustomMeetingTranscript.meeting_id == meeting.id
        ).first()
        
        transcript_response = None
        if transcript:
            transcript_response = CustomMeetingTranscriptResponse(
                id=transcript.id,
                meeting_id=transcript.meeting_id,
                content=transcript.content,
                summary=transcript.summary,
                created_at=transcript.created_at
            )
        
        # Создаем response
        meeting_response = CustomMeetingResponse(
            id=meeting.id,
            meeting_id=meeting.meeting_id,
            topic=meeting.topic,
            creator_id=meeting.creator_id,
            status=meeting.status,
            max_participants=meeting.max_participants,
            duration_minutes=meeting.duration_minutes,
            password=meeting.password,
            ai_agent_enabled=meeting.ai_agent_enabled,
            created_at=meeting.created_at,
            started_at=meeting.started_at,
            ended_at=meeting.ended_at,
            participants_count=len(participant_responses)
        )
        
        return CustomMeetingWithTranscript(
            meeting=meeting_response,
            participants=participant_responses,
            transcript=transcript_response
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting meeting details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/meetings/{meeting_id}/join")
async def join_meeting(
    meeting_id: str,
    join_request: JoinMeetingRequest,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Подключает пользователя к встрече"""
    try:
        # Проверяем существование встречи
        meeting = db.query(CustomMeeting).filter(
            CustomMeeting.meeting_id == meeting_id
        ).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        # Проверяем пароль
        if meeting.password and meeting.password != join_request.password:
            raise HTTPException(status_code=403, detail="Invalid password")
        
        # Проверяем лимит участников
        participants_count = db.query(MeetingParticipant).filter(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.left_at.is_(None)
        ).count()
        
        if participants_count >= meeting.max_participants:
            raise HTTPException(status_code=400, detail="Meeting is full")
        
        # Проверяем, не подключен ли уже пользователь
        existing_participant = db.query(MeetingParticipant).filter(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.user_id == current_user.id,
            MeetingParticipant.left_at.is_(None)
        ).first()
        
        if existing_participant:
            raise HTTPException(status_code=400, detail="User already in meeting")
        
        return {
            "message": "Ready to join meeting",
            "meeting_id": meeting_id,
            "join_url": f"/meeting/{meeting_id}",
            "websocket_url": f"ws://localhost:8000/api/webrtc/meetings/{meeting_id}/join"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error joining meeting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/meetings/{meeting_id}/join")
async def join_meeting_websocket(
    websocket: WebSocket,
    meeting_id: str,
    user_id: int = Query(..., description="User ID"),
    password: Optional[str] = Query(None, description="Meeting password")
):
    """WebSocket endpoint для подключения к встрече"""
    await websocket.accept()
    
    try:
        # Подключаемся к встрече
        result = await webrtc_service.join_meeting(
            meeting_id=meeting_id,
            user_id=user_id,
            websocket=websocket,
            password=password
        )
        
        # Отправляем подтверждение
        await websocket.send_text(json.dumps({
            "type": "connection_established",
            "meeting_id": meeting_id,
            "status": "joined",
            "participants_count": result["participants_count"]
        }))
        
        logger.info(f"User {user_id} connected to meeting {meeting_id} via WebSocket")
        
        # Слушаем сообщения от клиента
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                # Обрабатываем разные типы сообщений
                if message["type"] == "audio_data":
                    # Получаем timestamp из сообщения или используем текущее время
                    timestamp = message.get("timestamp")
                    if timestamp is None:
                        import time
                        timestamp = time.time()
                    await webrtc_service.handle_audio_data(
                        meeting_id, 
                        message["audio_data"], 
                        user_id,
                        timestamp=timestamp
                    )
                elif message["type"] == "video_data":
                    # Обработка видео данных (пока не реализовано)
                    pass
                elif message["type"] == "chat_message":
                    await webrtc_service.handle_chat_message(
                        meeting_id, 
                        message["message"], 
                        user_id
                    )
                elif message["type"] == "voice_message":
                    await webrtc_service.handle_voice_message(
                        meeting_id, 
                        message["audio_data"], 
                        user_id
                    )
                elif message["type"] == "ping":
                    # Отвечаем на ping
                    await websocket.send_text(json.dumps({
                        "type": "pong",
                        "timestamp": message.get("timestamp")
                    }))
                    
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON format"
                }))
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Internal server error"
                }))
                
    except WebSocketDisconnect:
        logger.info(f"User {user_id} disconnected from meeting {meeting_id}")
    except Exception as e:
        logger.error(f"WebSocket error for meeting {meeting_id}: {e}")
        try:
            await websocket.close()
        except:
            pass
    finally:
        # Удаляем пользователя из встречи
        try:
            await webrtc_service.leave_meeting(meeting_id, user_id)
        except Exception as e:
            logger.error(f"Error leaving meeting: {e}")


@router.post("/meetings/{meeting_id}/start-ai-agent")
async def start_ai_agent(
    meeting_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Запускает AI агента для встречи"""
    try:
        # Проверяем права доступа
        meeting = db.query(CustomMeeting).filter(
            CustomMeeting.meeting_id == meeting_id
        ).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        if meeting.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="Only meeting creator can start AI agent")
        
        if not meeting.ai_agent_enabled:
            raise HTTPException(status_code=400, detail="AI agent not enabled for this meeting")
        
        # Запускаем AI агента
        result = await webrtc_service.start_ai_agent(meeting_id)
        
        return {
            "message": "AI Agent started successfully",
            "meeting_id": meeting_id,
            "status": result["status"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting AI agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/meetings/{meeting_id}/end")
async def end_meeting(
    meeting_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Завершает встречу"""
    try:
        # Проверяем права доступа
        meeting = db.query(CustomMeeting).filter(
            CustomMeeting.meeting_id == meeting_id
        ).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        if meeting.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="Only meeting creator can end meeting")
        
        # Завершаем встречу
        await webrtc_service._end_meeting(meeting_id, db)
        
        return {
            "message": "Meeting ended successfully",
            "meeting_id": meeting_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ending meeting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/meetings/{meeting_id}/info")
async def get_meeting_info(
    meeting_id: str,
    current_user: User = Depends(require_user)
):
    """Получает информацию о встрече из Redis"""
    try:
        meeting_info = await webrtc_service.get_meeting_info(meeting_id)
        
        if not meeting_info:
            raise HTTPException(status_code=404, detail="Meeting not found")
        
        return {
            "meeting_id": meeting_id,
            "info": meeting_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting meeting info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# HTML страница для встречи
@html_router.get("/meetings/{meeting_id}/room", response_class=HTMLResponse)
async def meeting_room_page(
    meeting_id: str,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """HTML страница для участия во встрече"""
    try:
        # Получаем информацию о встрече
        meeting = db.query(CustomMeeting).filter(
            CustomMeeting.meeting_id == meeting_id
        ).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Встреча не найдена")
        
        # Проверяем права доступа
        if meeting.creator_id != current_user.id:
            # Проверяем, является ли пользователь участником
            participant = db.query(MeetingParticipant).filter(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.user_id == current_user.id
            ).first()
            
            if not participant:
                raise HTTPException(status_code=403, detail="Нет доступа к встрече")
        
        return templates.TemplateResponse("webrtc_meeting.html", {
            "request": request,
            "meeting": meeting,
            "user": current_user
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка загрузки страницы встречи: {e}")
        raise HTTPException(status_code=500, detail="Ошибка загрузки страницы встречи")


@router.delete("/meetings/{meeting_id}")
async def delete_meeting(
    meeting_id: str,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Удаляет WebRTC встречу"""
    try:
        # Проверяем права доступа
        meeting = db.query(CustomMeeting).filter(
            CustomMeeting.meeting_id == meeting_id
        ).first()
        
        if not meeting:
            raise HTTPException(status_code=404, detail="Встреча не найдена")
        
        if meeting.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="Только создатель может удалить встречу")
        
        # Удаляем связанные записи
        db.query(MeetingParticipant).filter(
            MeetingParticipant.meeting_id == meeting.id
        ).delete()
        
        db.query(CustomMeetingTranscript).filter(
            CustomMeetingTranscript.meeting_id == meeting.id
        ).delete()
        
        # Удаляем встречу
        db.delete(meeting)
        db.commit()
        
        return {"success": True, "message": "Встреча удалена"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления встречи: {e}")
        raise HTTPException(status_code=500, detail=str(e))
