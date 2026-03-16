# app/routers/public.py
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from typing import Optional
from models import User

router = APIRouter(tags=["public"])

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
