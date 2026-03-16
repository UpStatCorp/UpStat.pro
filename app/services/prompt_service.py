from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_
from models import Prompt, User


class PromptService:
    """Сервис для работы с промптами"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_active_prompt(self, name: str) -> Optional[Prompt]:
        """Получить активный промпт по имени"""
        return self.db.query(Prompt).filter(
            and_(Prompt.name == name, Prompt.is_active == True)
        ).first()
    
    def get_prompt_versions(self, name: str) -> List[Prompt]:
        """Получить все версии промпта по имени, отсортированные по версии (новые первые)"""
        return self.db.query(Prompt).filter(
            Prompt.name == name
        ).order_by(Prompt.version.desc()).all()
    
    def create_prompt_version(self, name: str, title: str, content: str, 
                            description: Optional[str], created_by: int) -> Prompt:
        """Создать новую версию промпта"""
        # Получаем максимальную версию для данного промпта
        max_version = self.db.query(Prompt.version).filter(
            Prompt.name == name
        ).order_by(Prompt.version.desc()).first()
        
        new_version = (max_version[0] + 1) if max_version else 1
        
        # Деактивируем все предыдущие версии
        self.db.query(Prompt).filter(Prompt.name == name).update({
            'is_active': False
        })
        
        # Создаем новую версию
        new_prompt = Prompt(
            name=name,
            title=title,
            content=content,
            description=description,
            version=new_version,
            is_active=True,
            created_by=created_by,
            created_at=datetime.utcnow()
        )
        
        self.db.add(new_prompt)
        self.db.commit()
        self.db.refresh(new_prompt)
        
        return new_prompt
    
    def activate_prompt_version(self, prompt_id: int) -> bool:
        """Активировать конкретную версию промпта"""
        prompt = self.db.query(Prompt).filter(Prompt.id == prompt_id).first()
        if not prompt:
            return False
        
        # Деактивируем все версии этого промпта
        self.db.query(Prompt).filter(Prompt.name == prompt.name).update({
            'is_active': False
        })
        
        # Активируем выбранную версию
        prompt.is_active = True
        prompt.updated_at = datetime.utcnow()
        
        self.db.commit()
        return True
    
    def get_all_prompts(self) -> List[Prompt]:
        """Получить все промпты с их активными версиями"""
        return self.db.query(Prompt).filter(Prompt.is_active == True).all()
    
    def get_prompt_by_id(self, prompt_id: int) -> Optional[Prompt]:
        """Получить промпт по ID"""
        return self.db.query(Prompt).filter(Prompt.id == prompt_id).first()
    
    def delete_prompt_version(self, prompt_id: int) -> bool:
        """Удалить версию промпта (только если она не активна)"""
        prompt = self.db.query(Prompt).filter(Prompt.id == prompt_id).first()
        if not prompt or prompt.is_active:
            return False
        
        self.db.delete(prompt)
        self.db.commit()
        return True
    
    def get_prompt_statistics(self) -> dict:
        """Получить статистику по промптам"""
        total_prompts = self.db.query(Prompt).count()
        active_prompts = self.db.query(Prompt).filter(Prompt.is_active == True).count()
        
        # Получаем уникальные имена промптов
        unique_names = self.db.query(Prompt.name).distinct().count()
        
        return {
            'total_versions': total_prompts,
            'active_prompts': active_prompts,
            'unique_prompts': unique_names
        }

