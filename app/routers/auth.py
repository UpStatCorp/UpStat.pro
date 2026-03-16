from fastapi import APIRouter, Depends, Form, Request, status, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from models import User, Conversation, PasswordResetToken
from security import hash_password, verify_password
from services.google_oauth import google_oauth_service
from services.team_invitations import accept_invitation
from services.email import send_password_reset_email
from datetime import datetime, timedelta
import secrets
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def _attach_invitation_if_present(request: Request, db: Session, user: User):
    """Если в сессии есть invite_token — автоматически добавляет пользователя в команду"""
    invite_token = request.session.get("invite_token")
    if not invite_token:
        return
    
    try:
        # Пытаемся принять приглашение
        accept_invitation(db, invite_token, user)
        logger.info(f"Пользователь {user.id} автоматически добавлен в команду по приглашению")
    except Exception as e:
        # Если ошибка — не блокируем логин/регистрацию
        logger.warning(f"Не удалось принять приглашение для пользователя {user.id}: {e}")
    
    # Очищаем токен из сессии
    request.session.pop("invite_token", None)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db), error: str = None, success: str = None, invite_token: str = Query(None)):
    # Если передан invite_token в URL — сохраняем в сессию
    if invite_token:
        request.session["invite_token"] = invite_token
    
    # Если уже залогинен — редирект в зависимости от роли
    uid = request.session.get("user_id")
    if uid:
        logged_user = db.get(User, uid)
        if logged_user and logged_user.role == "sale_manager":
            return RedirectResponse(url="/sales/", status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    
    # Получаем информацию о приглашении, если есть токен
    invited_email = None
    team_name = None
    token_to_check = invite_token or request.session.get("invite_token")
    
    if token_to_check:
        try:
            from services.team_invitations import get_invitation_by_token
            invitation = get_invitation_by_token(db, token_to_check)
            invited_email = invitation.invited_email
            team_name = invitation.team.name
            logger.info(f"Приглашение найдено для email: {invited_email}, команда: {team_name}")
        except Exception as e:
            logger.warning(f"Не удалось получить информацию о приглашении: {e}")
    
    return request.app.state.templates.TemplateResponse(
        "login.html", 
        {
            "request": request, 
            "error": error,
            "success": success,
            "invited_email": invited_email,
            "team_name": team_name,
            "has_invitation": invited_email is not None
        }
    )

@router.post("/login")
def login(request: Request, db: Session = Depends(get_db),
          email: str = Form(...), password: str = Form(...)):
    email_norm = email.lower().strip()
    user = db.query(User).filter(User.email == email_norm).first()
    
    # Проверяем, есть ли приглашение и совпадает ли email
    invite_token = request.session.get("invite_token")
    invited_email = None
    team_name = None
    
    if invite_token:
        try:
            from services.team_invitations import get_invitation_by_token
            invitation = get_invitation_by_token(db, invite_token)
            invited_email = invitation.invited_email
            team_name = invitation.team.name
            
            # Проверяем, что email совпадает с приглашенным
            if email_norm != invited_email.lower():
                return request.app.state.templates.TemplateResponse(
                    "login.html",
                    {
                        "request": request,
                        "error": f"Это приглашение предназначено для другого email ({invited_email})",
                        "invited_email": invited_email,
                        "team_name": team_name,
                        "has_invitation": True
                    },
                    status_code=400
                )
        except Exception as e:
            logger.warning(f"Ошибка проверки приглашения при логине: {e}")
    
    if not user or not verify_password(password, user.password_hash):
        return request.app.state.templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Неверный e-mail или пароль",
                "invited_email": invited_email,
                "team_name": team_name,
                "has_invitation": invited_email is not None
            },
            status_code=400
        )
    
    request.session["user_id"] = user.id
    
    # Обновляем время последнего входа
    from datetime import datetime
    user.last_login_at = datetime.utcnow()
    db.commit()
    
    # Проверяем и привязываем инвайт, если есть
    _attach_invitation_if_present(request, db, user)
    
    # Редирект в зависимости от роли
    if user.role == "sale_manager":
        return RedirectResponse(url="/sales/", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db), invite_token: str = Query(None)):
    # Сохраняем токен в сессию, если передан в URL
    if invite_token:
        request.session["invite_token"] = invite_token
    
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    
    # Получаем токен из сессии или из URL
    token_to_check = invite_token or request.session.get("invite_token")
    
    # Получаем email из приглашения, если есть токен
    invited_email = None
    error_message = None
    if token_to_check:
        try:
            from services.team_invitations import get_invitation_by_token
            invitation = get_invitation_by_token(db, token_to_check)
            invited_email = invitation.invited_email
            logger.info(f"Найдено приглашение для email: {invited_email}")
            
            # Проверяем, существует ли пользователь с таким email
            existing_user = db.query(User).filter(User.email == invited_email.lower()).first()
            if existing_user:
                # Пользователь уже существует - перенаправляем на страницу логина
                logger.info(f"Пользователь с email {invited_email} уже существует, перенаправляем на логин")
                # Сохраняем токен в сессию для логина
                request.session["invite_token"] = token_to_check
                return RedirectResponse(
                    url=f"/login?invite_token={token_to_check}",
                    status_code=status.HTTP_302_FOUND
                )
        except HTTPException as e:
            # Если приглашение не найдено или истекло, показываем ошибку
            error_message = e.detail
            logger.warning(f"Ошибка получения приглашения: {e.detail}")
            # Очищаем невалидный токен из сессии
            request.session.pop("invite_token", None)
        except Exception as e:
            # Другие ошибки
            logger.error(f"Неожиданная ошибка при получении приглашения: {e}", exc_info=True)
            request.session.pop("invite_token", None)
    else:
        logger.debug("Токен приглашения не найден ни в URL, ни в сессии")
    
    logger.debug(f"Передаем в шаблон: invited_email={invited_email}, has_invitation={invited_email is not None}, error={error_message}")
    
    return request.app.state.templates.TemplateResponse(
        "register.html", 
        {
            "request": request, 
            "error": error_message,
            "invited_email": invited_email,
            "has_invitation": invited_email is not None
        }
    )

@router.post("/register")
def register(request: Request, db: Session = Depends(get_db),
             name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    email_norm = email.lower().strip()
    
    # Проверяем, есть ли приглашение и совпадает ли email
    invite_token = request.session.get("invite_token")
    if invite_token:
        try:
            from services.team_invitations import get_invitation_by_token
            invitation = get_invitation_by_token(db, invite_token)
            if email_norm != invitation.invited_email.lower():
                return request.app.state.templates.TemplateResponse(
                    "register.html",
                    {
                        "request": request, 
                        "error": f"Это приглашение предназначено для другого email ({invitation.invited_email})",
                        "invited_email": invitation.invited_email,
                        "has_invitation": True
                    },
                    status_code=400
                )
        except Exception as e:
            logger.warning(f"Ошибка проверки приглашения: {e}")
    
    # Получаем invited_email для отображения в форме при ошибках
    invited_email_for_form = None
    if invite_token:
        try:
            from services.team_invitations import get_invitation_by_token
            invitation = get_invitation_by_token(db, invite_token)
            invited_email_for_form = invitation.invited_email
        except Exception:
            pass
    
    if db.query(User).filter(User.email == email_norm).first():
        return request.app.state.templates.TemplateResponse(
            "register.html",
            {
                "request": request, 
                "error": "Такой e-mail уже зарегистрирован",
                "invited_email": invited_email_for_form,
                "has_invitation": invite_token is not None
            },
            status_code=400
        )
    if len(password) < 6:
        return request.app.state.templates.TemplateResponse(
            "register.html",
            {
                "request": request, 
                "error": "Пароль должен быть ≥ 6 символов",
                "invited_email": invited_email_for_form,
                "has_invitation": invite_token is not None
            },
            status_code=400
        )

    user = User(email=email_norm, name=name.strip(), password_hash=hash_password(password), role="user")
    db.add(user); db.flush()
    conv = Conversation(user_id=user.id, title="Мой первый диалог")
    db.add(conv); db.commit()

    request.session["user_id"] = user.id
    request.session["show_welcome"] = True  # Показать приветственное окно новому пользователю
    
    # Привязываем инвайт, если есть
    _attach_invitation_if_present(request, db, user)
    
    # → кабинет
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


# Google OAuth маршруты
@router.get("/auth/google")
def google_login(request: Request, invite_token: str = None):
    """Инициация Google OAuth авторизации"""
    # Сохраняем invite_token в сессию, если передан
    if invite_token:
        request.session["invite_token"] = invite_token
    
    # Генерируем случайный state для защиты от CSRF
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    
    auth_url = google_oauth_service.get_authorization_url(state)
    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(None),
    db: Session = Depends(get_db)
):
    """Обработка callback от Google OAuth"""
    # Проверяем state для защиты от CSRF
    session_state = request.session.get("oauth_state")
    if not session_state or session_state != state:
        raise HTTPException(
            status_code=400,
            detail="Invalid state parameter"
        )
    
    try:
        # Обмениваем код на токен
        token_data = google_oauth_service.exchange_code_for_token(code)
        access_token = token_data.get("access_token")
        
        if not access_token:
            raise HTTPException(
                status_code=400,
                detail="No access token received"
            )
        
        # Получаем информацию о пользователе
        user_info = google_oauth_service.get_user_info(access_token)
        
        # Получаем или создаем пользователя
        user, is_new_user = google_oauth_service.get_or_create_user(db, user_info)
        
        # Проверяем, есть ли приглашение и совпадает ли email
        invite_token = request.session.get("invite_token")
        if invite_token:
            try:
                from services.team_invitations import get_invitation_by_token
                invitation = get_invitation_by_token(db, invite_token)
                if user.email.lower() != invitation.invited_email.lower():
                    # Email не совпадает - удаляем invite_token и показываем ошибку
                    request.session.pop("invite_token", None)
                    return RedirectResponse(
                        url=f"/login?error=Email+в+Google+аккаунте+({user.email})+не+совпадает+с+приглашенным+email+({invitation.invited_email})",
                        status_code=status.HTTP_302_FOUND
                    )
            except Exception as e:
                logger.warning(f"Ошибка проверки приглашения при Google OAuth: {e}")
        
        # Устанавливаем сессию
        request.session["user_id"] = user.id
        request.session.pop("oauth_state", None)  # Удаляем state из сессии
        
        # Показать приветственное окно новому пользователю
        if is_new_user:
            request.session["show_welcome"] = True
        
        # Обновляем время последнего входа
        from datetime import datetime
        user.last_login_at = datetime.utcnow()
        db.commit()
        
        # Привязываем инвайт, если есть
        _attach_invitation_if_present(request, db, user)
        
        # Перенаправляем в зависимости от роли
        if user.role == "sale_manager":
            return RedirectResponse(url="/sales/", status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
        
    except Exception as e:
        # В случае ошибки перенаправляем на страницу входа с сообщением об ошибке
        return RedirectResponse(
            url=f"/login?error={str(e)}",
            status_code=status.HTTP_302_FOUND
        )


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request, error: str = None, success: str = None):
    """Страница запроса восстановления пароля"""
    # Если уже залогинен — редирект в кабинет
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    
    return request.app.state.templates.TemplateResponse(
        "forgot-password.html",
        {
            "request": request,
            "error": error,
            "success": success
        }
    )


@router.post("/forgot-password")
def forgot_password(request: Request, db: Session = Depends(get_db), email: str = Form(...)):
    """Обработка запроса на восстановление пароля"""
    email_norm = email.lower().strip()
    
    # Ищем пользователя
    user = db.query(User).filter(User.email == email_norm).first()
    
    # Всегда показываем успешное сообщение (для безопасности не раскрываем, существует ли email)
    success_message = "Если аккаунт с таким email существует, на него было отправлено письмо с инструкциями по восстановлению пароля."
    
    if user:
        # Проверяем, что у пользователя есть пароль (не OAuth пользователь)
        if not user.password_hash or user.is_oauth_user:
            # OAuth пользователи не могут восстановить пароль через email
            return request.app.state.templates.TemplateResponse(
                "forgot-password.html",
                {
                    "request": request,
                    "error": "Этот аккаунт использует вход через Google. Пожалуйста, войдите через Google.",
                    "success": None
                },
                status_code=400
            )
        
        # Генерируем токен
        token = secrets.token_urlsafe(48)
        expires_at = datetime.utcnow() + timedelta(hours=1)
        
        # Создаем запись токена
        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=expires_at
        )
        db.add(reset_token)
        db.commit()
        
        # Формируем ссылку для сброса пароля
        public_app_url = os.getenv("PUBLIC_APP_URL", "").rstrip("/")
        if not public_app_url:
            base_url = f"{request.url.scheme}://{request.url.netloc}"
        else:
            base_url = public_app_url
        
        reset_link = f"{base_url}/reset-password?token={token}"
        
        # Отправляем email
        try:
            send_password_reset_email(user.email, reset_link, user.name)
            logger.info(f"Password reset email sent to {user.email}")
        except Exception as e:
            logger.error(f"Error sending password reset email: {e}", exc_info=True)
            # Не показываем ошибку пользователю, чтобы не раскрывать информацию
    
    return request.app.state.templates.TemplateResponse(
        "forgot-password.html",
        {
            "request": request,
            "error": None,
            "success": success_message
        }
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, db: Session = Depends(get_db), token: str = Query(None), error: str = None):
    """Страница сброса пароля"""
    # Если уже залогинен — редирект в кабинет
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    
    if not token:
        return request.app.state.templates.TemplateResponse(
            "reset-password.html",
            {
                "request": request,
                "error": "Токен не указан",
                "token": None,
                "valid": False
            }
        )
    
    # Проверяем токен
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token,
        PasswordResetToken.used == False,
        PasswordResetToken.expires_at > datetime.utcnow()
    ).first()
    
    if not reset_token:
        return request.app.state.templates.TemplateResponse(
            "reset-password.html",
            {
                "request": request,
                "error": "Токен недействителен или истек. Запросите новую ссылку для восстановления пароля.",
                "token": None,
                "valid": False
            }
        )
    
    return request.app.state.templates.TemplateResponse(
        "reset-password.html",
        {
            "request": request,
            "error": error,
            "token": token,
            "valid": True
        }
    )


@router.post("/reset-password")
def reset_password(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...)
):
    """Обработка сброса пароля"""
    # Проверяем, что пароли совпадают
    if password != password_confirm:
        return request.app.state.templates.TemplateResponse(
            "reset-password.html",
            {
                "request": request,
                "error": "Пароли не совпадают",
                "token": token,
                "valid": True
            },
            status_code=400
        )
    
    # Проверяем длину пароля
    if len(password) < 6:
        return request.app.state.templates.TemplateResponse(
            "reset-password.html",
            {
                "request": request,
                "error": "Пароль должен быть не менее 6 символов",
                "token": token,
                "valid": True
            },
            status_code=400
        )
    
    # Проверяем токен
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token,
        PasswordResetToken.used == False,
        PasswordResetToken.expires_at > datetime.utcnow()
    ).first()
    
    if not reset_token:
        return request.app.state.templates.TemplateResponse(
            "reset-password.html",
            {
                "request": request,
                "error": "Токен недействителен или истек. Запросите новую ссылку для восстановления пароля.",
                "token": None,
                "valid": False
            },
            status_code=400
        )
    
    # Обновляем пароль пользователя
    user = db.query(User).filter(User.id == reset_token.user_id).first()
    if not user:
        return request.app.state.templates.TemplateResponse(
            "reset-password.html",
            {
                "request": request,
                "error": "Пользователь не найден",
                "token": None,
                "valid": False
            },
            status_code=400
        )
    
    # Устанавливаем новый пароль
    user.password_hash = hash_password(password)
    
    # Помечаем токен как использованный
    reset_token.used = True
    reset_token.used_at = datetime.utcnow()
    
    db.commit()
    
    logger.info(f"Password reset successful for user {user.id}")
    
    # Перенаправляем на страницу входа с сообщением об успехе
    return RedirectResponse(
        url="/login?success=Пароль успешно изменен. Войдите с новым паролем.",
        status_code=status.HTTP_302_FOUND
    )
