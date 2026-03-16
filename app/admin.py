from functools import wraps
from fastapi import HTTPException, status, Request, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import User


def admin_required(f):
    """Декоратор для проверки прав администратора"""
    @wraps(f)
    def decorated_function(request: Request, db: Session = Depends(get_db), *args, **kwargs):
        user_id = request.session.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Необходима авторизация"
            )
        
        user = db.query(User).filter(User.id == user_id).first()
        if not user or user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для выполнения этого действия"
            )
        
        return f(request, db, *args, **kwargs)
    return decorated_function


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Получение текущего пользователя из сессии"""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Необходима авторизация"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден"
        )
    
    return user


def is_admin(user: User) -> bool:
    """Проверка, является ли пользователь администратором"""
    return user.role == "admin"


def is_sale_manager(user: User) -> bool:
    """Проверка, является ли пользователь менеджером продаж"""
    return user.role == "sale_manager"


def require_sale_manager(request: Request, db: Session) -> User:
    """Получение текущего пользователя и проверка роли sale_manager"""
    user = get_current_user(request, db)
    if not is_sale_manager(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к панели продаж"
        )
    return user

