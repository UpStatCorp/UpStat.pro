from fastapi import APIRouter, Depends, Request, status, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Prompt
from admin import get_current_user, is_admin
from services.prompt_service import PromptService
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/prompts", tags=["admin-prompts"])


@router.get("/", response_class=HTMLResponse)
def admin_prompts(request: Request, db: Session = Depends(get_db)):
    """Главная страница управления промптами"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к управлению промптами"
        )
    
    prompt_service = PromptService(db)
    # Получаем все версии промптов для обоих типов
    audit_prompts = prompt_service.get_prompt_versions("sales_audit_summary")
    trainer_prompts = prompt_service.get_prompt_versions("sales_trainer")
    stats = prompt_service.get_prompt_statistics()
    
    return request.app.state.templates.TemplateResponse(
        "admin/prompts.html",
        {
            "request": request, 
            "current_user": current_user, 
            "audit_prompts": audit_prompts,
            "trainer_prompts": trainer_prompts,
            "stats": stats
        }
    )


@router.get("/create", response_class=HTMLResponse)
def create_prompt_page(request: Request, db: Session = Depends(get_db)):
    """Страница создания нового промпта (редактирование текущего)"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для создания промптов"
        )
    
    prompt_service = PromptService(db)
    # Получаем текущий активный промпт sales_audit_summary для редактирования
    current_prompt = prompt_service.get_active_prompt("sales_audit_summary")
    
    return request.app.state.templates.TemplateResponse(
        "admin/prompt_create.html",
        {
            "request": request, 
            "current_user": current_user,
            "current_prompt": current_prompt
        }
    )


@router.get("/trainer", response_class=HTMLResponse)
def trainer_prompt_page(request: Request, db: Session = Depends(get_db)):
    """Страница редактирования промпта чата тренера"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования промптов тренера"
        )
    
    prompt_service = PromptService(db)
    # Получаем текущий активный промпт sales_trainer для редактирования
    current_prompt = prompt_service.get_active_prompt("sales_trainer")
    
    return request.app.state.templates.TemplateResponse(
        "admin/prompt_trainer.html",
        {
            "request": request, 
            "current_user": current_user,
            "current_prompt": current_prompt
        }
    )


@router.post("/trainer")
def update_trainer_prompt(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    content: str = Form(...)
):
    """Обновление промпта чата тренера"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для обновления промптов тренера"
        )
    
    prompt_service = PromptService(db)
    
    try:
        prompt_service.create_prompt_version(
            name="sales_trainer",
            title=title,
            content=content,
            description=description if description else None,
            created_by=current_user.id
        )
        return RedirectResponse(url="/admin/prompts/trainer", status_code=status.HTTP_302_FOUND)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка при обновлении промпта тренера: {str(e)}"
        )


@router.post("/create")
def create_prompt(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    content: str = Form(...)
):
    """Создание новой версии промпта sales_audit_summary"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для создания промптов"
        )
    
    prompt_service = PromptService(db)
    
    try:
        prompt_service.create_prompt_version(
            name="sales_audit_summary",  # Всегда sales_audit_summary
            title=title,
            content=content,
            description=description if description else None,
            created_by=current_user.id
        )
        return RedirectResponse(url="/admin/prompts", status_code=status.HTTP_302_FOUND)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка при создании промпта: {str(e)}"
        )


@router.get("/{prompt_name}/versions", response_class=HTMLResponse)
def prompt_versions(request: Request, prompt_name: str, db: Session = Depends(get_db)):
    """Страница версий промпта"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для просмотра версий промптов"
        )
    
    prompt_service = PromptService(db)
    versions = prompt_service.get_prompt_versions(prompt_name)
    
    if not versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Промпт не найден"
        )
    
    return request.app.state.templates.TemplateResponse(
        "admin/prompt_versions.html",
        {
            "request": request, 
            "current_user": current_user, 
            "prompt_name": prompt_name,
            "versions": versions
        }
    )


@router.get("/{prompt_name}/edit", response_class=HTMLResponse)
def edit_prompt_page(request: Request, prompt_name: str, db: Session = Depends(get_db)):
    """Страница редактирования промпта"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования промптов"
        )
    
    prompt_service = PromptService(db)
    active_prompt = prompt_service.get_active_prompt(prompt_name)
    
    if not active_prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Промпт не найден"
        )
    
    return request.app.state.templates.TemplateResponse(
        "admin/prompt_edit.html",
        {
            "request": request, 
            "current_user": current_user, 
            "prompt": active_prompt
        }
    )


@router.post("/{prompt_name}/edit")
def edit_prompt(
    request: Request,
    prompt_name: str,
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    content: str = Form(...)
):
    """Редактирование промпта (создание новой версии)"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования промптов"
        )
    
    prompt_service = PromptService(db)
    
    try:
        prompt_service.create_prompt_version(
            name=prompt_name,
            title=title,
            content=content,
            description=description if description else None,
            created_by=current_user.id
        )
        return RedirectResponse(url="/admin/prompts", status_code=status.HTTP_302_FOUND)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка при редактировании промпта: {str(e)}"
        )


@router.post("/{prompt_id}/activate")
def activate_prompt_version(prompt_id: int, request: Request, db: Session = Depends(get_db)):
    """Активация конкретной версии промпта"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для активации промптов"
        )
    
    prompt_service = PromptService(db)
    
    if prompt_service.activate_prompt_version(prompt_id):
        return RedirectResponse(url="/admin/prompts", status_code=status.HTTP_302_FOUND)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось активировать версию промпта"
        )


@router.post("/{prompt_id}/delete")
def delete_prompt_version(prompt_id: int, request: Request, db: Session = Depends(get_db)):
    """Удаление версии промпта"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления промптов"
        )
    
    prompt_service = PromptService(db)
    
    if prompt_service.delete_prompt_version(prompt_id):
        return RedirectResponse(url="/admin/prompts", status_code=status.HTTP_302_FOUND)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось удалить версию промпта (возможно, она активна)"
        )
