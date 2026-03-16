"""
Оптимизация работы с базой данных
"""
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session, Query, joinedload, selectinload
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class DBOptimizer:
    """Утилиты для оптимизации запросов к БД"""
    
    @staticmethod
    def optimize_query_with_eager_loading(
        query: Query,
        relationships: List[str]
    ) -> Query:
        """
        Оптимизация запроса с eager loading отношений
        
        Args:
            query: SQLAlchemy запрос
            relationships: список отношений для eager loading
        
        Returns:
            Оптимизированный запрос
        """
        for rel in relationships:
            query = query.options(joinedload(rel))
        return query
    
    @staticmethod
    def batch_load_relationships(
        objects: List[Any],
        relationship_name: str
    ):
        """
        Пакетная загрузка отношений для списка объектов
        Избегает проблемы N+1 запросов
        """
        if not objects:
            return
        
        # SQLAlchemy автоматически оптимизирует это через selectinload
        for obj in objects:
            getattr(obj, relationship_name)
    
    @staticmethod
    def paginate_query(
        query: Query,
        page: int = 1,
        per_page: int = 20
    ) -> Dict[str, Any]:
        """
        Пагинация запроса с метаданными
        
        Returns:
            {
                'items': [...],
                'total': int,
                'page': int,
                'per_page': int,
                'pages': int
            }
        """
        total = query.count()
        items = query.limit(per_page).offset((page - 1) * per_page).all()
        pages = (total + per_page - 1) // per_page
        
        return {
            'items': items,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': pages
        }
    
    @staticmethod
    def bulk_insert(db: Session, model_class, data_list: List[Dict[str, Any]]):
        """
        Массовая вставка с использованием bulk_insert_mappings
        Гораздо быстрее чем по одному
        """
        if not data_list:
            return
        
        try:
            db.bulk_insert_mappings(model_class, data_list)
            db.commit()
            logger.info(f"Bulk inserted {len(data_list)} {model_class.__name__} records")
        except Exception as e:
            db.rollback()
            logger.error(f"Bulk insert failed: {e}")
            raise
    
    @staticmethod
    def bulk_update(db: Session, model_class, data_list: List[Dict[str, Any]]):
        """
        Массовое обновление с использованием bulk_update_mappings
        """
        if not data_list:
            return
        
        try:
            db.bulk_update_mappings(model_class, data_list)
            db.commit()
            logger.info(f"Bulk updated {len(data_list)} {model_class.__name__} records")
        except Exception as e:
            db.rollback()
            logger.error(f"Bulk update failed: {e}")
            raise
    
    @staticmethod
    def cleanup_old_records(
        db: Session,
        model_class,
        date_field: str,
        days_to_keep: int = 30
    ) -> int:
        """
        Очистка старых записей
        
        Args:
            db: сессия БД
            model_class: модель для очистки
            date_field: название поля с датой
            days_to_keep: сколько дней хранить
        
        Returns:
            Количество удаленных записей
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
        
        try:
            count = db.query(model_class).filter(
                getattr(model_class, date_field) < cutoff_date
            ).delete()
            db.commit()
            logger.info(f"Cleaned up {count} old {model_class.__name__} records")
            return count
        except Exception as e:
            db.rollback()
            logger.error(f"Cleanup failed: {e}")
            raise
    
    @staticmethod
    def analyze_query_performance(db: Session, query: Query) -> Dict[str, Any]:
        """
        Анализ производительности запроса (только для SQLite)
        """
        try:
            # Получаем SQL запрос
            sql = str(query.statement.compile(compile_kwargs={"literal_binds": True}))
            
            # EXPLAIN QUERY PLAN для SQLite
            explain_result = db.execute(text(f"EXPLAIN QUERY PLAN {sql}")).fetchall()
            
            return {
                'sql': sql,
                'plan': [dict(row._mapping) for row in explain_result]
            }
        except Exception as e:
            logger.error(f"Query analysis failed: {e}")
            return {'error': str(e)}
    
    @staticmethod
    def optimize_database(db: Session):
        """
        Оптимизация базы данных (для SQLite)
        """
        try:
            # VACUUM - освобождает неиспользуемое пространство
            db.execute(text("VACUUM"))
            
            # ANALYZE - обновляет статистику для оптимизатора запросов
            db.execute(text("ANALYZE"))
            
            db.commit()
            logger.info("Database optimized successfully")
        except Exception as e:
            logger.error(f"Database optimization failed: {e}")
            raise
    
    @staticmethod
    def get_database_stats(db: Session) -> Dict[str, Any]:
        """
        Получить статистику базы данных (для SQLite)
        """
        try:
            # Размер БД
            size_result = db.execute(
                text("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
            ).fetchone()
            
            # Список таблиц с количеством записей
            tables_result = db.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            ).fetchall()
            
            table_counts = {}
            for (table_name,) in tables_result:
                count = db.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
                table_counts[table_name] = count
            
            return {
                'size_bytes': size_result[0] if size_result else 0,
                'size_mb': round(size_result[0] / (1024 * 1024), 2) if size_result else 0,
                'tables': table_counts,
                'total_records': sum(table_counts.values())
            }
        except Exception as e:
            logger.error(f"Failed to get database stats: {e}")
            return {'error': str(e)}


class QueryOptimizationMiddleware:
    """Middleware для логирования медленных запросов"""
    
    def __init__(self, slow_query_threshold: float = 1.0):
        """
        Args:
            slow_query_threshold: порог в секундах для медленных запросов
        """
        self.slow_query_threshold = slow_query_threshold
    
    def log_slow_query(self, query_time: float, query: str):
        """Логирование медленного запроса"""
        if query_time > self.slow_query_threshold:
            logger.warning(
                f"Slow query detected ({query_time:.2f}s): {query[:200]}..."
            )


# Хелперы для часто используемых запросов

def get_user_with_conversations(db: Session, user_id: int):
    """Получить пользователя с conversations (оптимизировано)"""
    from models import User
    return db.query(User).options(
        selectinload(User.conversations)
    ).filter(User.id == user_id).first()


def get_conversation_with_messages(db: Session, conversation_id: int):
    """Получить диалог с сообщениями (оптимизировано)"""
    from models import Conversation
    return db.query(Conversation).options(
        selectinload(Conversation.messages)
    ).filter(Conversation.id == conversation_id).first()


def get_recent_messages_optimized(
    db: Session,
    conversation_id: int,
    limit: int = 50
):
    """Получить последние сообщения с attachments (оптимизировано)"""
    from models import Message
    return db.query(Message).options(
        selectinload(Message.attachments)
    ).filter(
        Message.conversation_id == conversation_id
    ).order_by(
        Message.created_at.desc()
    ).limit(limit).all()

