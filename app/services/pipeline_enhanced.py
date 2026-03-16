"""
Расширенная версия pipeline с прогрессом и уведомлениями
"""
import logging
from services.pipeline import run_pipeline as original_run_pipeline, run_pipeline_from_text as original_run_pipeline_from_text
from services.progress_tracker import get_progress_tracker
from services.notification_service import get_notification_service

logger = logging.getLogger(__name__)


async def run_pipeline_with_progress(user_id: int, conversation_id: int, audio_attachment_id: int):
    """
    Обертка над run_pipeline с поддержкой прогресса и уведомлений
    """
    tracker = get_progress_tracker()
    notif_service = get_notification_service()
    
    # Создаем операцию прогресса
    operation_id = f"audio_analysis_{conversation_id}_{audio_attachment_id}"
    progress = tracker.create_operation(
        operation_id=operation_id,
        total_stages=4,
        title="Анализ аудиозаписи",
        can_cancel=False
    )
    progress.metadata = {"user_id": user_id, "conversation_id": conversation_id}
    
    try:
        # Запускаем оригинальный pipeline
        await original_run_pipeline(user_id, conversation_id, audio_attachment_id)
        
        # Завершаем операцию
        tracker.complete_operation(operation_id, "Анализ завершен успешно!")
        notif_service.success(
            user_id,
            "Анализ завершен",
            "Ваш аудиофайл успешно проанализирован. Отчет готов!",
            action_label="Посмотреть",
            action_url=f"/chat/{conversation_id}"
        )
        
    except Exception as e:
        # Обрабатываем ошибки
        logger.error(f"Error in pipeline: {e}", exc_info=True)
        tracker.fail_operation(operation_id, f"Ошибка: {str(e)}")
        notif_service.error(
            user_id,
            "Ошибка анализа",
            "Произошла ошибка при анализе файла. Попробуйте еще раз."
        )
        raise


async def run_pipeline_from_text_with_progress(user_id: int, conversation_id: int, text_attachment_id: int):
    """
    Обертка над run_pipeline_from_text с поддержкой прогресса и уведомлений
    """
    tracker = get_progress_tracker()
    notif_service = get_notification_service()
    
    # Создаем операцию прогресса
    operation_id = f"text_analysis_{conversation_id}_{text_attachment_id}"
    progress = tracker.create_operation(
        operation_id=operation_id,
        total_stages=3,
        title="Анализ текстового файла",
        can_cancel=False
    )
    progress.metadata = {"user_id": user_id, "conversation_id": conversation_id}
    
    try:
        # Запускаем оригинальный pipeline
        await original_run_pipeline_from_text(user_id, conversation_id, text_attachment_id)
        
        # Завершаем операцию
        tracker.complete_operation(operation_id, "Анализ завершен успешно!")
        notif_service.success(
            user_id,
            "Анализ завершен",
            "Ваш текстовый файл успешно проанализирован. Отчет готов!",
            action_label="Посмотреть",
            action_url=f"/chat/{conversation_id}"
        )
        
    except Exception as e:
        # Обрабатываем ошибки
        logger.error(f"Error in text pipeline: {e}", exc_info=True)
        tracker.fail_operation(operation_id, f"Ошибка: {str(e)}")
        notif_service.error(
            user_id,
            "Ошибка анализа",
            "Произошла ошибка при анализе файла. Попробуйте еще раз."
        )
        raise

