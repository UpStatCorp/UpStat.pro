"""Сервис для проверки прав доступа к командам"""
import json
import logging
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from models import User, Team, TeamMember

logger = logging.getLogger(__name__)

# Константы ролей
ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_USER = "user"


def is_admin(user: User) -> bool:
    """Проверка, является ли пользователь админом"""
    return user.role == ROLE_ADMIN


def is_manager(user: User) -> bool:
    """Проверка, является ли пользователь менеджером"""
    return user.role == ROLE_MANAGER


def assert_can_manage_team(db: Session, current_user: User, team_id: int) -> Team:
    """
    Проверяет, может ли пользователь управлять командой (админ или менеджер этой команды).
    Выбрасывает HTTPException 403, если нет прав.
    Возвращает объект Team, если права есть.
    """
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    
    # Админ может управлять любой командой
    if is_admin(current_user):
        return team
    
    # Менеджер может управлять только своими командами
    if is_manager(current_user) and team.manager_id == current_user.id:
        return team
    
    raise HTTPException(status_code=403, detail="Нет прав для управления этой командой")


def get_manager_teams(db: Session, current_user: User):
    """
    Возвращает все команды, где пользователь менеджер.
    Если пользователь админ — возвращает все команды.
    """
    if is_admin(current_user):
        return db.query(Team).all()
    
    if is_manager(current_user):
        return db.query(Team).filter(Team.manager_id == current_user.id).all()
    
    return []


def get_accessible_user_ids_for_manager(db: Session, current_user: User) -> list[int]:
    """
    Возвращает список user_id, по которым менеджер может смотреть прогресс.
    - Админ видит всех пользователей (возвращает None, что означает "все")
    - Менеджер видит только участников своих команд
    - Обычный пользователь видит только себя
    """
    if is_admin(current_user):
        # Админ видит всех — возвращаем None, что означает "не фильтровать"
        return None
    
    if is_manager(current_user):
        # Получаем все команды менеджера
        teams = db.query(Team).filter(Team.manager_id == current_user.id).all()
        team_ids = [t.id for t in teams]
        
        if not team_ids:
            # Если нет команд, возвращаем пустой список
            return []
        
        # Получаем всех участников этих команд
        members = db.query(TeamMember).filter(TeamMember.team_id.in_(team_ids)).all()
        user_ids = [m.user_id for m in members]
        
        # Добавляем самого менеджера
        user_ids.append(current_user.id)
        
        # Убираем дубликаты
        return list(set(user_ids))
    
    # Обычный пользователь видит только себя
    return [current_user.id]


def get_user_teams(db: Session, user: User) -> list[Team]:
    """
    Возвращает все команды, в которых пользователь является участником (включая менеджера).
    """
    # Команды, где пользователь менеджер
    manager_teams = db.query(Team).filter(Team.manager_id == user.id).all()
    
    # Команды, где пользователь участник
    member_teams = db.query(Team).join(TeamMember).filter(
        TeamMember.user_id == user.id
    ).all()
    
    # Объединяем и убираем дубликаты
    all_teams = {team.id: team for team in manager_teams + member_teams}
    return list(all_teams.values())


def get_team_script_for_user(db: Session, target_user: User, conversation_user_id: int) -> Optional[dict]:
    """
    Получает скрипт команды для пользователя, для которого создается анализ.
    Ищет команду, где conversation_user_id является участником.
    
    Args:
        db: Сессия БД
        target_user: Пользователь, для которого создается анализ (может быть участником команды)
        conversation_user_id: ID пользователя, для которого создается диалог (тот же что target_user.id обычно)
    
    Returns:
        Dict в формате чеклиста или None
    """
    from models import TeamScript, TeamMember
    
    # Получаем команды пользователя, для которого создается анализ
    user_teams = get_user_teams(db, target_user)
    
    if not user_teams:
        return None
    
    # Ищем команду, где conversation_user_id является участником
    target_team = None
    for team in user_teams:
        # Проверяем, является ли conversation_user_id участником этой команды
        member = db.query(TeamMember).filter(
            TeamMember.team_id == team.id,
            TeamMember.user_id == conversation_user_id
        ).first()
        if member:
            target_team = team
            break
    
    # Если не нашли, берем первую команду пользователя (если он менеджер)
    if not target_team and user_teams:
        # Проверяем, является ли пользователь менеджером первой команды
        first_team = user_teams[0]
        if first_team.manager_id == conversation_user_id:
            target_team = first_team
    
    if not target_team:
        return None
    
    # Получаем скрипт команды
    script = db.query(TeamScript).filter(TeamScript.team_id == target_team.id).first()
    if not script:
        return None
    
    try:
        return json.loads(script.script_json)
    except Exception as e:
        logger.error(f"Ошибка парсинга JSON скрипта команды {target_team.id}: {e}")
        return None

