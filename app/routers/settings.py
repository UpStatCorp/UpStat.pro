import os
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request, status, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from deps import require_user
from models import User
from security import hash_password, verify_password

router = APIRouter(tags=["settings"])

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    return request.app.state.templates.TemplateResponse(
        "settings.html", 
        {"request": request, "user": user, "success": None, "error": None}
    )

@router.post("/settings/profile")
def update_profile(
    request: Request, 
    db: Session = Depends(get_db),
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None)
):
    user = require_user(request, db)
    
    # Проверяем, что email не занят другим пользователем
    if email.lower().strip() != user.email.lower():
        existing_user = db.query(User).filter(User.email == email.lower().strip()).first()
        if existing_user:
            return request.app.state.templates.TemplateResponse(
                "settings.html",
                {"request": request, "user": user, "success": None, "error": "Такой e-mail уже занят"},
                status_code=400
            )
    
    # Обновляем профиль
    user.name = name.strip()
    user.email = email.lower().strip()
    user.phone = phone.strip() if phone else None
    user.updated_at = datetime.now().isoformat()
    
    db.commit()
    
    return request.app.state.templates.TemplateResponse(
        "settings.html",
        {"request": request, "user": user, "success": "Профиль успешно обновлен", "error": None}
    )

@router.post("/settings/password")
def update_password(
    request: Request,
    db: Session = Depends(get_db),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    user = require_user(request, db)
    
    # Проверяем текущий пароль
    if not verify_password(current_password, user.password_hash):
        return request.app.state.templates.TemplateResponse(
            "settings.html",
            {"request": request, "user": user, "success": None, "error": "Неверный текущий пароль"},
            status_code=400
        )
    
    # Проверяем новый пароль
    if len(new_password) < 6:
        return request.app.state.templates.TemplateResponse(
            "settings.html",
            {"request": request, "user": user, "success": None, "error": "Новый пароль должен быть не менее 6 символов"},
            status_code=400
        )
    
    if new_password != confirm_password:
        return request.app.state.templates.TemplateResponse(
            "settings.html",
            {"request": request, "user": user, "success": None, "error": "Пароли не совпадают"},
            status_code=400
        )
    
    # Обновляем пароль
    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.now().isoformat()
    db.commit()
    
    return request.app.state.templates.TemplateResponse(
        "settings.html",
        {"request": request, "user": user, "success": "Пароль успешно изменен", "error": None}
    )

@router.post("/settings/avatar")
def update_avatar(
    request: Request,
    db: Session = Depends(get_db),
    avatar: UploadFile = File(...)
):
    user = require_user(request, db)
    
    # Проверяем тип файла
    if not avatar.content_type.startswith('image/'):
        return request.app.state.templates.TemplateResponse(
            "settings.html",
            {"request": request, "user": user, "success": None, "error": "Загрузите изображение"},
            status_code=400
        )
    
    # Проверяем размер файла (максимум 5MB)
    if avatar.size > 5 * 1024 * 1024:
        return request.app.state.templates.TemplateResponse(
            "settings.html",
            {"request": request, "user": user, "success": None, "error": "Размер файла не должен превышать 5MB"},
            status_code=400
        )
    
    # Создаем директорию для аватаров если её нет
    avatar_dir = "app/static/avatars"
    os.makedirs(avatar_dir, exist_ok=True)
    
    # Генерируем уникальное имя файла
    file_extension = avatar.filename.split('.')[-1] if '.' in avatar.filename else 'jpg'
    avatar_filename = f"{uuid.uuid4()}.{file_extension}"
    avatar_path = os.path.join(avatar_dir, avatar_filename)
    
    # Сохраняем файл
    with open(avatar_path, "wb") as f:
        content = avatar.file.read()
        f.write(content)
    
    # Удаляем старый аватар если есть
    if user.avatar and user.avatar.startswith('/static/avatars/'):
        old_avatar_path = os.path.join("app", user.avatar.lstrip('/'))
        if os.path.exists(old_avatar_path):
            try:
                os.remove(old_avatar_path)
            except:
                pass
    
    # Обновляем URL аватара в базе
    user.avatar = f"/static/avatars/{avatar_filename}"
    user.updated_at = datetime.now().isoformat()
    db.commit()
    
    return request.app.state.templates.TemplateResponse(
        "settings.html",
        {"request": request, "user": user, "success": "Аватар успешно обновлен", "error": None}
    )
