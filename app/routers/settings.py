import os
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from deps import require_user
from models import User
from security import hash_password, verify_password
from services.capability_service import has_capability

router = APIRouter(tags=["settings"])


def _settings_template(user: User) -> str:
    if has_capability(user, "call_analysis"):
        return "settings.html"
    return "train/settings.html"


def _settings_response(
    request: Request,
    user: User,
    *,
    success: Optional[str] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    return request.app.state.templates.TemplateResponse(
        _settings_template(user),
        {
            "request": request,
            "user": user,
            "success": success,
            "error": error,
            "show_integrations": has_capability(user, "call_analysis"),
        },
        status_code=status_code,
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    return _settings_response(request, user)


@router.post("/settings/profile")
def update_profile(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
):
    user = require_user(request, db)

    if email.lower().strip() != user.email.lower():
        existing_user = db.query(User).filter(User.email == email.lower().strip()).first()
        if existing_user:
            return _settings_response(
                request, user, error="Такой e-mail уже занят", status_code=400
            )

    user.name = name.strip()
    user.email = email.lower().strip()
    user.phone = phone.strip() if phone else None
    user.updated_at = datetime.now().isoformat()

    db.commit()

    return _settings_response(request, user, success="Профиль успешно обновлен")


@router.post("/settings/password")
def update_password(
    request: Request,
    db: Session = Depends(get_db),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = require_user(request, db)

    if not verify_password(current_password, user.password_hash):
        return _settings_response(
            request, user, error="Неверный текущий пароль", status_code=400
        )

    if len(new_password) < 6:
        return _settings_response(
            request,
            user,
            error="Новый пароль должен быть не менее 6 символов",
            status_code=400,
        )

    if new_password != confirm_password:
        return _settings_response(
            request, user, error="Пароли не совпадают", status_code=400
        )

    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.now().isoformat()
    db.commit()

    return _settings_response(request, user, success="Пароль успешно изменен")


@router.post("/settings/avatar")
def update_avatar(
    request: Request,
    db: Session = Depends(get_db),
    avatar: UploadFile = File(...),
):
    user = require_user(request, db)

    if not avatar.content_type.startswith("image/"):
        return _settings_response(
            request, user, error="Загрузите изображение", status_code=400
        )

    if avatar.size > 5 * 1024 * 1024:
        return _settings_response(
            request,
            user,
            error="Размер файла не должен превышать 5MB",
            status_code=400,
        )

    avatar_dir = "app/static/avatars"
    os.makedirs(avatar_dir, exist_ok=True)

    file_extension = avatar.filename.split(".")[-1] if "." in avatar.filename else "jpg"
    avatar_filename = f"{uuid.uuid4()}.{file_extension}"
    avatar_path = os.path.join(avatar_dir, avatar_filename)

    with open(avatar_path, "wb") as f:
        content = avatar.file.read()
        f.write(content)

    if user.avatar and user.avatar.startswith("/static/avatars/"):
        old_avatar_path = os.path.join("app", user.avatar.lstrip("/"))
        if os.path.exists(old_avatar_path):
            try:
                os.remove(old_avatar_path)
            except OSError:
                pass

    user.avatar = f"/static/avatars/{avatar_filename}"
    user.updated_at = datetime.now().isoformat()
    db.commit()

    return _settings_response(request, user, success="Аватар успешно обновлен")
