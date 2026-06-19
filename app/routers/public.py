# app/routers/public.py
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from typing import Optional
from models import User
import os

router = APIRouter(tags=["public"])


@router.get("/health", include_in_schema=False)
async def health():
    """Liveness — контейнер жив."""
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
async def ready(db: Session = Depends(get_db)):
    """Readiness — БД и Redis доступны."""
    errors: dict = {}

    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        errors["db"] = str(exc)

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis as _redis
            r = _redis.from_url(redis_url, socket_connect_timeout=2)
            r.ping()
        except Exception as exc:
            errors["redis"] = str(exc)

    if errors:
        return JSONResponse(status_code=503, content={"status": "not ready", "errors": errors})
    return {"status": "ready"}

def current_user(request: Request, db: Session) -> Optional[User]:
    uid = request.session.get("user_id")
    return db.get(User, uid) if uid else None

@router.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user:
        return RedirectResponse("/dashboard")
    return request.app.state.templates.TemplateResponse("landing.html", {"request": request})

@router.get("/career", response_class=HTMLResponse)
def career(request: Request):
    return request.app.state.templates.TemplateResponse("career.html", {"request": request})
