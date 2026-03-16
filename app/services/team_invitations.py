"""Сервис для работы с приглашениями в команды"""
import secrets
import logging
from datetime import datetime, timedelta
from typing import List
from fastapi import HTTPException
from sqlalchemy.orm import Session
from models import User, Team, TeamMember, TeamInvitation

logger = logging.getLogger(__name__)

# Статусы приглашений
class TeamInvitationStatus:
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


def _generate_token() -> str:
    """Генерирует крипто-стойкий токен 48 байт → ~64 символа URL-safe"""
    return secrets.token_urlsafe(48)


def create_invitations(
    db: Session,
    manager: User,
    team: Team,
    emails: List[str]
) -> List[TeamInvitation]:
    """
    Создаёт приглашения для списка email адресов.
    
    Args:
        db: Сессия базы данных
        manager: Пользователь, который создаёт приглашения
        team: Команда, в которую приглашаем
        emails: Список email адресов
    
    Returns:
        Список созданных приглашений
    """
    # Нормализуем email (lowercase, убираем пробелы)
    normalized_emails = {e.strip().lower() for e in emails if e.strip()}
    
    if not normalized_emails:
        raise HTTPException(status_code=400, detail="Список email пуст")
    
    invitations = []
    expires_at = datetime.utcnow() + timedelta(days=14)
    
    for email in normalized_emails:
        # Проверяем, нет ли уже активного приглашения для этого email
        existing = db.query(TeamInvitation).filter(
            TeamInvitation.team_id == team.id,
            TeamInvitation.invited_email == email,
            TeamInvitation.status == TeamInvitationStatus.PENDING
        ).first()
        
        if existing:
            # Если есть активное приглашение, пропускаем
            logger.info(f"Пропущено дублирующее приглашение для {email} в команду {team.id}")
            continue
        
        # Генерируем уникальный токен
        token = _generate_token()
        
        # Проверяем уникальность токена (крайне маловероятно, но на всякий случай)
        while db.query(TeamInvitation).filter(TeamInvitation.token == token).first():
            token = _generate_token()
        
        # Создаём запись TeamInvitation со статусом PENDING
        invitation = TeamInvitation(
            team_id=team.id,
            invited_email=email,
            token=token,
            status=TeamInvitationStatus.PENDING,
            invited_by_user_id=manager.id,
            expires_at=expires_at
        )
        db.add(invitation)
        invitations.append(invitation)
    
    # Сохраняем в БД
    db.commit()
    
    # Обновляем объекты из БД
    for inv in invitations:
        db.refresh(inv)
    
    # Логируем действие
    logger.info(f"Создано {len(invitations)} приглашений для команды {team.id} менеджером {manager.id}")
    
    return invitations


def get_invitation_by_token(db: Session, token: str) -> TeamInvitation:
    """
    Получает приглашение по токену с проверками.
    
    Args:
        db: Сессия базы данных
        token: Токен приглашения
    
    Returns:
        Объект TeamInvitation
    
    Raises:
        HTTPException: Если приглашение не найдено, уже использовано или истёк срок
    """
    # Ищем приглашение по токену
    inv = db.query(TeamInvitation).filter(TeamInvitation.token == token).first()
    
    # Проверяем, что оно существует
    if not inv:
        raise HTTPException(status_code=404, detail="Приглашение не найдено")
    
    # Проверяем статус (должно быть PENDING)
    if inv.status != TeamInvitationStatus.PENDING:
        raise HTTPException(status_code=400, detail="Приглашение уже использовано")
    
    # Проверяем срок действия
    if inv.expires_at < datetime.utcnow():
        inv.status = TeamInvitationStatus.EXPIRED
        db.commit()
        raise HTTPException(status_code=400, detail="Срок действия приглашения истёк")
    
    return inv


def accept_invitation(db: Session, token: str, user: User) -> TeamMember:
    """
    Принимает приглашение и добавляет пользователя в команду.
    
    Args:
        db: Сессия базы данных
        token: Токен приглашения
        user: Пользователь, который принимает приглашение
    
    Returns:
        Объект TeamMember (участник команды)
    
    Raises:
        HTTPException: Если приглашение не найдено, уже использовано или истёк срок
    """
    # Получаем приглашение (с проверками)
    inv = get_invitation_by_token(db, token)
    
    # Проверяем, что email пользователя совпадает с приглашённым email
    if user.email.lower() != inv.invited_email.lower():
        raise HTTPException(
            status_code=400,
            detail=f"Приглашение предназначено для другого email ({inv.invited_email})"
        )
    
    # Проверяем, не состоит ли пользователь уже в команде
    existing = db.query(TeamMember).filter(
        TeamMember.team_id == inv.team_id,
        TeamMember.user_id == user.id
    ).first()
    
    if existing:
        # Если уже в команде — просто помечаем приглашение как ACCEPTED
        inv.status = TeamInvitationStatus.ACCEPTED
        inv.accepted_user_id = user.id
        inv.accepted_at = datetime.utcnow()
        db.commit()
        logger.info(f"Приглашение {inv.id} помечено как принятое (пользователь уже в команде)")
        return existing
    
    # Если не в команде — добавляем как участника
    member = TeamMember(
        team_id=inv.team_id,
        user_id=user.id,
        role_in_team="member"
    )
    db.add(member)
    
    # Помечаем приглашение как ACCEPTED
    inv.status = TeamInvitationStatus.ACCEPTED
    inv.accepted_user_id = user.id
    inv.accepted_at = datetime.utcnow()
    
    db.commit()
    db.refresh(member)
    
    # Логируем
    logger.info(f"Пользователь {user.id} принял приглашение {inv.id} и добавлен в команду {inv.team_id}")
    
    return member

