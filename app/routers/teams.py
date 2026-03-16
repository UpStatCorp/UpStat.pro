"""Роутер для управления командами и приглашениями"""
import os
import json
import tempfile
from pathlib import Path
from fastapi import APIRouter, Depends, Request, HTTPException, Form, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from deps import require_user
from models import User, Team, TeamMember, TeamInvitation, TeamScript
from services.team_access import (
    is_admin,
    is_manager,
    assert_can_manage_team,
    get_manager_teams
)
from services.team_invitations import create_invitations
from services.email import send_invitation_email
from services.team_script_service import convert_to_checklist_format

router = APIRouter(tags=["teams"])


@router.get("/teams/my", response_class=HTMLResponse)
def my_teams_page(request: Request, db: Session = Depends(get_db)):
    """Страница 'Моя команда'"""
    current_user = require_user(request, db)
    
    # Получаем команды, где пользователь менеджер
    manager_teams = get_manager_teams(db, current_user)
    
    # Получаем команды, где пользователь участник
    member_teams = db.query(Team).join(TeamMember).filter(
        TeamMember.user_id == current_user.id
    ).all()
    
    # Рендерим шаблон
    return request.app.state.templates.TemplateResponse(
        "team_manage.html",
        {
            "request": request,
            "user": current_user,
            "manager_teams": manager_teams,
            "member_teams": member_teams,
            "is_manager": is_manager(current_user) or is_admin(current_user)
        }
    )


@router.post("/teams")
def create_team(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db)
):
    """Создание новой команды"""
    current_user = require_user(request, db)
    
    # Проверяем права (только manager или admin)
    if not (is_admin(current_user) or is_manager(current_user)):
        raise HTTPException(status_code=403, detail="Нет прав для создания команды")
    
    # Валидация названия
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название команды не может быть пустым")
    
    # Создаём команду
    team = Team(name=name, manager_id=current_user.id)
    db.add(team)
    db.flush()  # Получаем ID команды
    
    # Автоматически добавляем менеджера как участника
    member = TeamMember(
        team_id=team.id,
        user_id=current_user.id,
        role_in_team="manager"
    )
    db.add(member)
    db.commit()
    
    return RedirectResponse(url="/teams/my", status_code=302)


@router.get("/teams/{team_id}/members", response_class=HTMLResponse)
def team_members_page(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Страница участников команды"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа
    team = assert_can_manage_team(db, current_user, team_id)
    
    # Получаем участников
    members = db.query(TeamMember).filter(TeamMember.team_id == team_id).all()
    
    # Получаем приглашения
    invitations = db.query(TeamInvitation).filter(
        TeamInvitation.team_id == team_id
    ).order_by(TeamInvitation.created_at.desc()).all()
    
    return request.app.state.templates.TemplateResponse(
        "team_members.html",
        {
            "request": request,
            "user": current_user,
            "team": team,
            "members": members,
            "invitations": invitations
        }
    )


@router.post("/teams/{team_id}/invitations")
def create_team_invitations(
    team_id: int,
    request: Request,
    emails: str = Form(...),
    db: Session = Depends(get_db)
):
    """Создание приглашений в команду"""
    current_user = require_user(request, db)
    
    # Проверяем права
    team = assert_can_manage_team(db, current_user, team_id)
    
    # Парсим список email (поддерживает запятые и переносы строк)
    email_list = [
        e.strip() for e in emails.replace(",", "\n").splitlines()
        if e.strip()
    ]
    
    if not email_list:
        raise HTTPException(status_code=400, detail="Список email пуст")
    
    # Создаём приглашения через сервис
    try:
        invitations = create_invitations(db, current_user, team, email_list)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка создания приглашений: {str(e)}")
    
    # Формируем ссылки и отправляем email
    public_app_url = os.getenv("PUBLIC_APP_URL", "").rstrip("/")
    if not public_app_url:
        base_link = f"{request.url.scheme}://{request.url.netloc}/register?invite_token="
    else:
        base_link = f"{public_app_url}/register?invite_token="
    
    for inv in invitations:
        link = f"{base_link}{inv.token}"
        try:
            send_invitation_email(
                inv.invited_email,
                link,
                team.name,
                current_user.name
            )
        except Exception as e:
            # Логируем ошибку, но не прерываем процесс
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка отправки email для {inv.invited_email}: {e}")
    
    return RedirectResponse(url=f"/teams/{team_id}/members", status_code=302)


@router.get("/teams/{team_id}/script", response_class=HTMLResponse)
def team_script_page(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Страница скрипта команды"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа (менеджер команды или участник)
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    
    is_team_manager = team.manager_id == current_user.id
    is_member = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == current_user.id
    ).first() is not None
    
    if not (is_team_manager or is_member):
        raise HTTPException(status_code=403, detail="Нет доступа к этой команде")
    
    # Получаем скрипт команды
    script = db.query(TeamScript).filter(TeamScript.team_id == team_id).first()
    
    # Парсим JSON скрипта для отображения
    script_data = None
    if script:
        try:
            script_data = json.loads(script.script_json)
        except Exception:
            script_data = None
    
    return request.app.state.templates.TemplateResponse(
        "team_script.html",
        {
            "request": request,
            "user": current_user,
            "team": team,
            "script": script,
            "script_data": script_data,
            "can_edit": is_team_manager
        }
    )


@router.post("/teams/{team_id}/script")
def upload_team_script(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db),
    script_text: str = Form(None),
    script_file: UploadFile = File(None)
):
    """Загрузка скрипта команды (текст или Word файл)"""
    current_user = require_user(request, db)
    
    # Проверяем права (только менеджер команды)
    team = assert_can_manage_team(db, current_user, team_id)
    
    # Проверяем, что передан либо текст, либо файл
    # Проверяем, что файл действительно есть (не пустой)
    has_file = script_file and script_file.filename and script_file.filename.strip()
    has_text = script_text and script_text.strip()
    
    if not has_text and not has_file:
        raise HTTPException(status_code=400, detail="Необходимо указать текст скрипта или загрузить файл")
    
    text_content = None
    is_word_file = False
    word_file_path = None
    
    # Обрабатываем файл (только если файл действительно загружен)
    if has_file:
        # Проверяем тип файла
        if script_file.content_type and script_file.content_type not in [
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
            "text/plain"
        ]:
            raise HTTPException(status_code=400, detail="Поддерживаются только файлы Word (.docx, .doc) или текстовые файлы")
        
        is_word_file = script_file.content_type and (
            script_file.content_type.startswith("application/vnd.openxmlformats") or 
            script_file.content_type == "application/msword"
        )
        
        # Сохраняем файл во временную директорию
        if is_word_file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx" if script_file.content_type.endswith("document") else ".doc") as tmp_file:
                content = script_file.file.read()
                if not content:
                    raise HTTPException(status_code=400, detail="Файл пуст")
                tmp_file.write(content)
                word_file_path = Path(tmp_file.name)
        else:
            # Текстовый файл
            content = script_file.file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Файл пуст")
            text_content = content.decode("utf-8")
    
    # Обрабатываем текст (если передан текст напрямую)
    if has_text:
        text_content = script_text.strip()
    
    if not text_content and not word_file_path:
        raise HTTPException(status_code=400, detail="Не удалось извлечь текст из файла")
    
    try:
        # Конвертируем в формат чеклиста
        checklist_data = convert_to_checklist_format(
            text=text_content or "",
            is_word_file=is_word_file,
            word_file_path=word_file_path
        )
        
        # Сохраняем или обновляем скрипт
        existing_script = db.query(TeamScript).filter(TeamScript.team_id == team_id).first()
        
        if existing_script:
            # Обновляем существующий
            existing_script.title = checklist_data.get("title", "Скрипт команды")
            existing_script.description = checklist_data.get("description", "")
            existing_script.script_json = json.dumps(checklist_data, ensure_ascii=False)
            existing_script.original_text = text_content
            existing_script.uploaded_by_user_id = current_user.id
            from datetime import datetime
            existing_script.updated_at = datetime.utcnow()
        else:
            # Создаём новый
            script = TeamScript(
                team_id=team_id,
                title=checklist_data.get("title", "Скрипт команды"),
                description=checklist_data.get("description", ""),
                script_json=json.dumps(checklist_data, ensure_ascii=False),
                original_text=text_content,
                uploaded_by_user_id=current_user.id
            )
            db.add(script)
        
        db.commit()
        
        # Удаляем временный файл, если был
        if word_file_path and word_file_path.exists():
            try:
                word_file_path.unlink()
            except Exception:
                pass
        
        return RedirectResponse(url=f"/teams/{team_id}/script", status_code=302)
        
    except Exception as e:
        db.rollback()
        # Удаляем временный файл в случае ошибки
        if word_file_path and word_file_path.exists():
            try:
                word_file_path.unlink()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Ошибка обработки скрипта: {str(e)}")

