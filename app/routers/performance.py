"""
API для мониторинга производительности
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pathlib import Path
from typing import Dict, Any
import os

from database import get_db
from deps import require_user
from models import User
from services.caching_service import get_cache_service
from services.db_optimizer import DBOptimizer
from services.file_optimizer import get_file_optimizer, get_file_cache

router = APIRouter(prefix="/api/performance", tags=["performance"])


@router.get("/cache/stats")
async def get_cache_stats(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить статистику кеша"""
    # Только для админов
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    cache = get_cache_service()
    return cache.get_stats()


@router.post("/cache/clear")
async def clear_cache(
    pattern: str = "*",
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Очистить кеш (только для админов)"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    cache = get_cache_service()
    count = cache.delete_pattern(pattern)
    
    return {
        "success": True,
        "message": f"Очищено {count} записей кеша",
        "count": count
    }


@router.get("/database/stats")
async def get_db_stats(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить статистику базы данных"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    optimizer = DBOptimizer()
    stats = optimizer.get_database_stats(db)
    return stats


@router.post("/database/optimize")
async def optimize_database(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Оптимизировать базу данных (VACUUM + ANALYZE)"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    try:
        optimizer = DBOptimizer()
        optimizer.optimize_database(db)
        return {
            "success": True,
            "message": "База данных оптимизирована"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка оптимизации: {str(e)}")


@router.post("/database/cleanup")
async def cleanup_database(
    days_to_keep: int = 30,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Очистить старые записи из базы данных"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    try:
        from models import Message, Attachment
        optimizer = DBOptimizer()
        
        # Очищаем старые сообщения
        deleted_messages = optimizer.cleanup_old_records(
            db, Message, 'created_at', days_to_keep
        )
        
        # Очищаем старые вложения
        deleted_attachments = optimizer.cleanup_old_records(
            db, Attachment, 'created_at', days_to_keep
        )
        
        return {
            "success": True,
            "deleted_messages": deleted_messages,
            "deleted_attachments": deleted_attachments,
            "total_deleted": deleted_messages + deleted_attachments
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка очистки: {str(e)}")


@router.get("/storage/stats")
async def get_storage_stats(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить статистику хранилища файлов"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    upload_dir = Path(os.getenv("UPLOAD_DIR", "uploads"))
    
    if not upload_dir.exists():
        return {"error": "Директория загрузок не найдена"}
    
    optimizer = get_file_optimizer()
    stats = optimizer.get_directory_stats(upload_dir)
    
    return stats


@router.post("/storage/cleanup")
async def cleanup_storage(
    days_to_keep: int = 7,
    compress_old: bool = False,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Очистить старые файлы из хранилища"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    upload_dir = Path(os.getenv("UPLOAD_DIR", "uploads"))
    
    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail="Директория загрузок не найдена")
    
    try:
        optimizer = get_file_optimizer()
        stats = await optimizer.optimize_storage(
            upload_dir,
            days_to_keep=days_to_keep,
            compress_old_files=compress_old
        )
        
        return {
            "success": True,
            **stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка очистки: {str(e)}")


@router.post("/storage/clear-cache")
async def clear_file_cache(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Очистить кеш файлов"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    cache = get_file_cache()
    cache.clear()
    
    return {
        "success": True,
        "message": "Кеш файлов очищен"
    }


@router.get("/system/info")
async def get_system_info(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить информацию о системе"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    import psutil
    import platform
    
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "platform": platform.system(),
            "platform_version": platform.version(),
            "python_version": platform.python_version(),
            "cpu": {
                "percent": cpu_percent,
                "count": psutil.cpu_count()
            },
            "memory": {
                "total_mb": round(memory.total / (1024 * 1024), 2),
                "used_mb": round(memory.used / (1024 * 1024), 2),
                "percent": memory.percent
            },
            "disk": {
                "total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
                "used_gb": round(disk.used / (1024 * 1024 * 1024), 2),
                "free_gb": round(disk.free / (1024 * 1024 * 1024), 2),
                "percent": disk.percent
            }
        }
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="psutil не установлен. Установите: pip install psutil"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения данных: {str(e)}")


@router.get("/overview")
async def get_performance_overview(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить общий обзор производительности"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    # Собираем данные из разных источников
    cache_stats = get_cache_service().get_stats()
    optimizer = DBOptimizer()
    db_stats = optimizer.get_database_stats(db)
    
    upload_dir = Path(os.getenv("UPLOAD_DIR", "uploads"))
    storage_stats = {}
    if upload_dir.exists():
        optimizer = get_file_optimizer()
        storage_stats = {
            "total_files": len(list(upload_dir.rglob('*'))),
            "total_size_mb": round(
                optimizer.get_directory_size(upload_dir) / (1024 * 1024), 2
            )
        }
    
    return {
        "cache": cache_stats,
        "database": {
            "size_mb": db_stats.get('size_mb', 0),
            "total_records": db_stats.get('total_records', 0)
        },
        "storage": storage_stats
    }

