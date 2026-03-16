from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from database import get_db
from deps import require_user
from models import User, ZoomMeeting, MeetingTranscript
from schemas import (
    CreateMeetingRequest, 
    MeetingResponse, 
    MeetingListResponse,
    StartMeetingRequest,
    MeetingTranscriptResponse,
    ZoomMeetingWithTranscript
)
from services.zoom_service import ZoomService
from services.signature_service import signature_service

router = APIRouter(prefix="/api/zoom", tags=["zoom-meetings"])
zoom_service = ZoomService()


@router.post("/meetings/create", response_model=MeetingResponse)
async def create_meeting(
    meeting_data: CreateMeetingRequest,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Создает новую Zoom встречу с ИИ-агентом"""
    try:
        meeting = await zoom_service.create_meeting(db, current_user, meeting_data)
        return MeetingResponse.model_validate(meeting)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка создания встречи: {str(e)}"
        )


@router.get("/meetings", response_model=MeetingListResponse)
async def get_user_meetings(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100
):
    """Получает список встреч пользователя"""
    try:
        meetings = db.query(ZoomMeeting).filter(
            ZoomMeeting.user_id == current_user.id
        ).offset(skip).limit(limit).all()
        
        total = db.query(ZoomMeeting).filter(
            ZoomMeeting.user_id == current_user.id
        ).count()
        
        return MeetingListResponse(
            meetings=[MeetingResponse.model_validate(m) for m in meetings],
            total=total
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения списка встреч: {str(e)}"
        )


@router.get("/meetings/{meeting_id}", response_model=ZoomMeetingWithTranscript)
async def get_meeting_details(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получает детали встречи с транскриптом"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        transcript = db.query(MeetingTranscript).filter(
            MeetingTranscript.meeting_id == meeting_id
        ).first()
        
        return ZoomMeetingWithTranscript(
            meeting=MeetingResponse.model_validate(meeting),
            transcript=MeetingTranscriptResponse.model_validate(transcript) if transcript else None
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения деталей встречи: {str(e)}"
        )


@router.post("/meetings/{meeting_id}/start")
async def start_meeting_with_agent(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Запускает встречу с ИИ-агентом"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        if meeting.status != "scheduled":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Встреча уже запущена или завершена"
            )
        
        # Обновляем статус встречи
        await zoom_service.update_meeting_status(db, meeting_id, "active")
        
        # TODO: Здесь будет вызов AI Agent Service для подключения к встрече
        # await ai_agent_service.connect_to_meeting(meeting.meeting_id)
        
        return {
            "message": "Встреча запущена с ИИ-агентом",
            "meeting_id": meeting_id,
            "join_url": meeting.join_url
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка запуска встречи: {str(e)}"
        )


@router.get("/meetings/{meeting_id}/transcript", response_model=MeetingTranscriptResponse)
async def get_meeting_transcript(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получает транскрипт встречи"""
    try:
        # Проверяем, что встреча принадлежит пользователю
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        transcript = db.query(MeetingTranscript).filter(
            MeetingTranscript.meeting_id == meeting_id
        ).first()
        
        if not transcript:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Транскрипт не найден"
            )
        
        return MeetingTranscriptResponse.model_validate(transcript)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения транскрипта: {str(e)}"
        )


@router.delete("/meetings/{meeting_id}")
async def delete_meeting(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Удаляет встречу"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        success = await zoom_service.delete_meeting(db, meeting_id, meeting.meeting_id)
        
        if success:
            return {"message": "Встреча успешно удалена"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Ошибка удаления встречи"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка удаления встречи: {str(e)}"
        )


@router.post("/meetings/{meeting_id}/join")
async def join_meeting(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Пользователь присоединяется к встрече"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        if meeting.status == "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Встреча уже завершена"
            )
        
        # Если встреча еще не активна, запускаем её
        if meeting.status == "scheduled":
            await zoom_service.update_meeting_status(db, meeting_id, "active")
        
        # Запускаем ИИ-агента через 3 секунды
        import asyncio
        asyncio.create_task(start_ai_agent_delayed(meeting, current_user.name))
        
        return {
            "message": "Добро пожаловать в встречу! ИИ-агент подключится через несколько секунд.",
            "meeting_id": meeting_id,
            "status": "active"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка входа в встречу: {str(e)}"
        )


async def start_ai_agent_delayed(meeting: ZoomMeeting, user_name: str):
    """Запускает ИИ-агента с задержкой в 3 секунды через SDK Runner"""
    import asyncio
    print(f"🚀 start_ai_agent_delayed вызвана для встречи {meeting.meeting_id}")
    await asyncio.sleep(3)
    print(f"⏰ Задержка завершена, начинаем запуск агента для встречи {meeting.meeting_id}")
    
    try:
        # Если агент уже активен, не запускаем повторно
        if meeting.agent_active:
            print(f"Агент уже активен для встречи {meeting.meeting_id}")
            return
        
        print(f"🔑 Генерируем JWT подпись для встречи {meeting.meeting_id}")
        # Генерируем подпись для SDK
        signature = signature_service.generate_zoom_signature(
            meeting_number=meeting.meeting_id,
            role=0,  # attendee
            user_identity="ai_assistant"
        )
        print(f"✅ JWT подпись сгенерирована для встречи {meeting.meeting_id}")
        
        import httpx
        async with httpx.AsyncClient() as client:
            print(f"🤖 Подключаемся к SDK Runner для встречи {meeting.meeting_id}")
            # 1. Запускаем агента через SDK Runner
            sdk_response = await client.post(
                "http://sdk-runner:3001/join",
                json={
                    "meetingNumber": meeting.meeting_id,
                    "signature": signature,
                    "userName": "ИИ-Агент",
                    "sdkKey": signature_service.sdk_key
                },
                timeout=30.0
            )
            
            print(f"📡 SDK Runner ответ: {sdk_response.status_code}")
            if sdk_response.status_code != 200:
                print(f"❌ SDK Runner error: {sdk_response.status_code}")
                print(f"📄 SDK Runner response: {sdk_response.text}")
                return
            
            print(f"🎯 Запускаем AI Agent Service для встречи {meeting.meeting_id}")
            # 2. Запускаем AI Agent Service
            ai_response = await client.post(
                "http://ai_agent_service:8001/agent/start",
                json={
                    "meeting_id": meeting.meeting_id,
                    "user_name": user_name,
                    "duration_minutes": meeting.duration_minutes
                },
                timeout=30.0
            )
            
            print(f"🧠 AI Agent Service ответ: {ai_response.status_code}")
            if ai_response.status_code == 200:
                # Обновляем статус в БД через zoom_service
                await zoom_service.update_meeting_agent_status(meeting.meeting_id, True)
                print(f"✅ ИИ-агент успешно запущен для встречи {meeting.meeting_id}")
            else:
                print(f"❌ AI Agent Service error: {ai_response.status_code}")
                print(f"📄 AI Agent Service response: {ai_response.text}")
                
    except Exception as e:
        print(f"💥 Ошибка запуска ИИ-агента: {str(e)}")
        import traceback
        print(f"📋 Stack trace: {traceback.format_exc()}")


@router.post("/meetings/{meeting_id}/end")
async def end_meeting(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Завершает встречу и генерирует отчет"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        if meeting.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Встреча не активна"
            )
        
        # Обновляем статус встречи
        await zoom_service.update_meeting_status(db, meeting_id, "completed")
        
        # TODO: Здесь будет вызов AI Agent Service для завершения встречи
        # и генерации отчета
        # await ai_agent_service.end_meeting_and_generate_report(meeting.meeting_id)
        
        return {
            "message": "Встреча завершена",
            "meeting_id": meeting_id,
            "status": "completed"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка завершения встречи: {str(e)}"
        )


@router.post("/sdk-signature")
async def generate_sdk_signature(
    meeting_number: str,
    role: int = 0,
    user_identity: str = "ai_assistant",
    current_user: User = Depends(require_user)
):
    """Генерирует JWT подпись для Zoom Meeting SDK"""
    try:
        signature = signature_service.generate_zoom_signature(
            meeting_number=meeting_number,
            role=role,
            user_identity=user_identity
        )
        
        return {
            "signature": signature,
            "sdk_key": signature_service.sdk_key,
            "meeting_number": meeting_number,
            "role": role,
            "user_identity": user_identity
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка генерации подписи: {str(e)}"
        )


@router.post("/meetings/{meeting_id}/start-agent")
async def start_agent(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Запускает ИИ-агента для встречи"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        if meeting.agent_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Агент уже активен"
            )
        
        # Генерируем подпись для SDK
        signature = signature_service.generate_zoom_signature(
            meeting_number=meeting.meeting_id,
            role=0,  # attendee
            user_identity="ai_assistant"
        )
        
        # Запускаем агента через SDK Runner
        import httpx
        async with httpx.AsyncClient() as client:
            sdk_response = await client.post(
                "http://sdk-runner:3001/join",
                json={
                    "meetingNumber": meeting.meeting_id,
                    "signature": signature,
                    "userName": "ИИ-Агент",
                    "sdkKey": signature_service.sdk_key
                },
                timeout=30.0
            )
            
            if sdk_response.status_code != 200:
                raise Exception(f"SDK Runner error: {sdk_response.status_code}")
        
        # Запускаем AI Agent Service
        ai_response = await client.post(
            "http://ai_agent_service:8001/agent/start",
            json={
                "meeting_id": meeting.meeting_id,
                "user_name": current_user.name,
                "duration_minutes": meeting.duration_minutes
            },
            timeout=30.0
        )
        
        if ai_response.status_code != 200:
            raise Exception(f"AI Agent Service error: {ai_response.status_code}")
        
        # Обновляем статус в БД
        meeting.agent_active = True
        meeting.status = "active"
        db.commit()
        
        return {
            "message": "ИИ-агент успешно запущен",
            "meeting_id": meeting_id,
            "agent_active": True
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка запуска агента: {str(e)}"
        )


@router.post("/meetings/{meeting_id}/stop-agent")
async def stop_agent(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Останавливает ИИ-агента"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        if not meeting.agent_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Агент не активен"
            )
        
        # Останавливаем агента через SDK Runner
        import httpx
        async with httpx.AsyncClient() as client:
            sdk_response = await client.post(
                "http://sdk-runner:3001/leave",
                json={"meetingNumber": meeting.meeting_id},
                timeout=30.0
            )
            
            # Останавливаем AI Agent Service
            ai_response = await client.post(
                "http://ai_agent_service:8001/agent/stop",
                json={"meeting_id": meeting.meeting_id},
                timeout=30.0
            )
        
        # Обновляем статус в БД
        meeting.agent_active = False
        db.commit()
        
        return {
            "message": "ИИ-агент остановлен",
            "meeting_id": meeting_id,
            "agent_active": False
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка остановки агента: {str(e)}"
        )


@router.get("/meetings/{meeting_id}/agent-status")
async def get_agent_status(
    meeting_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получает статус ИИ-агента"""
    try:
        meeting = db.query(ZoomMeeting).filter(
            ZoomMeeting.id == meeting_id,
            ZoomMeeting.user_id == current_user.id
        ).first()
        
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Встреча не найдена"
            )
        
        # Получаем статус от SDK Runner
        import httpx
        async with httpx.AsyncClient() as client:
            try:
                sdk_response = await client.get(
                    f"http://sdk-runner:3001/status/{meeting.meeting_id}",
                    timeout=10.0
                )
                sdk_status = sdk_response.json() if sdk_response.status_code == 200 else None
            except:
                sdk_status = None
        
        return {
            "meeting_id": meeting_id,
            "agent_active": meeting.agent_active,
            "meeting_status": meeting.status,
            "sdk_status": sdk_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения статуса агента: {str(e)}"
        )
