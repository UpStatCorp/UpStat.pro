"""Роутер экрана владельца (Owner Command Center)"""

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

from database import get_db
from deps import require_user
from models import Team, TeamMember, User
from services.owner_analytics_service import OwnerAnalyticsService

router = APIRouter(tags=["owner_dashboard"])


def _get_owner_teams(db: Session, user_id: int):
    """Возвращает команды, где пользователь — manager или owner"""
    memberships = (
        db.query(TeamMember)
        .filter(
            TeamMember.user_id == user_id,
            TeamMember.role_in_team.in_(["manager", "owner"]),
        )
        .all()
    )
    team_ids_from_membership = [m.team_id for m in memberships]

    managed = (
        db.query(Team)
        .filter(Team.manager_id == user_id)
        .all()
    )
    managed_ids = [t.id for t in managed]

    all_team_ids = list(set(team_ids_from_membership + managed_ids))
    if not all_team_ids:
        return []

    return db.query(Team).filter(Team.id.in_(all_team_ids)).all()


@router.get("/owner", response_class=HTMLResponse)
def owner_dashboard_redirect(request: Request, db: Session = Depends(get_db)):
    """Редирект на первую команду владельца"""
    user = require_user(request, db)
    teams = _get_owner_teams(db, user.id)

    if not teams:
        raise HTTPException(
            status_code=403,
            detail="У вас нет команд для просмотра экрана владельца"
        )

    from starlette.responses import RedirectResponse
    return RedirectResponse(url=f"/owner/{teams[0].id}", status_code=302)


@router.get("/owner/{team_id}", response_class=HTMLResponse)
def owner_dashboard(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    user = (
        db.query(User)
        .options(joinedload(User.team_memberships))
        .filter(User.id == user.id)
        .first()
    )

    teams = _get_owner_teams(db, user.id)
    team_ids = [t.id for t in teams]

    if team_id not in team_ids:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Нет доступа к этой команде")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")

    days = int(request.query_params.get("days", 30))
    if days not in (1, 7, 30):
        days = 30

    dashboard_data = OwnerAnalyticsService.get_full_dashboard(db, team_id, days=days)

    return request.app.state.templates.TemplateResponse(
        "owner_dashboard.html",
        {
            "request": request,
            "user": user,
            "team": team,
            "teams": teams,
            "days": days,
            "data": dashboard_data,
            "money_leaks": dashboard_data["money_leaks"],
            "conversion": dashboard_data["conversion"],
            "risk": dashboard_data["risk"],
            "speed": dashboard_data["speed"],
            "team_ranking": dashboard_data["team"],
            "patterns": dashboard_data["patterns"],
            "ai_insights": dashboard_data["ai_insights"],
        },
    )


@router.get("/api/owner/{team_id}/data")
def owner_dashboard_api(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """API-эндпоинт для динамического обновления данных"""
    user = require_user(request, db)
    teams = _get_owner_teams(db, user.id)

    if team_id not in [t.id for t in teams] and user.role != "admin":
        raise HTTPException(status_code=403, detail="Нет доступа")

    days = int(request.query_params.get("days", 30))
    dashboard_data = OwnerAnalyticsService.get_full_dashboard(db, team_id, days=days)

    def _serialize_members(members_list):
        result = []
        for m in members_list:
            result.append({
                "user_name": m["user"].name,
                "user_email": m["user"].email,
                "role_in_team": m["role_in_team"],
                "overall_score": m["overall_score"],
                "contact_score": m["contact_score"],
                "needs_score": m["needs_score"],
                "presentation_score": m["presentation_score"],
                "objections_score": m["objections_score"],
                "closing_score": m["closing_score"],
                "total_calls": m["total_calls"],
            })
        return result

    def _serialize_patterns(patterns_list):
        result = []
        for p in patterns_list:
            result.append({
                "pattern_text": p.pattern_text,
                "stage": p.stage,
                "outcome": p.outcome,
                "percentage": p.percentage,
                "occurrence_count": p.occurrence_count,
            })
        return result

    return {
        "money_leaks": dashboard_data["money_leaks"],
        "conversion": dashboard_data["conversion"],
        "risk": dashboard_data["risk"],
        "speed": dashboard_data["speed"],
        "team": {
            "members": _serialize_members(dashboard_data["team"]["members"]),
            "team_stats": dashboard_data["team"]["team_stats"],
        },
        "patterns": {
            "positive": _serialize_patterns(dashboard_data["patterns"]["positive"]),
            "negative": _serialize_patterns(dashboard_data["patterns"]["negative"]),
        },
        "ai_insights": dashboard_data["ai_insights"],
    }
