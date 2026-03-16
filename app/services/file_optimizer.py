"""
Оптимизация работы с файлами
"""
import os
import shutil
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timedelta
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class FileOptimizer:
    """Утилиты для оптимизации файловых операций"""
    
    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
    
    async def compress_file_async(
        self,
        source_path: Path,
        compression_level: int = 6
    ) -> Optional[Path]:
        """
        Асинхронное сжатие файла
        
        Args:
            source_path: путь к исходному файлу
            compression_level: уровень сжатия (1-9)
        
        Returns:
            Путь к сжатому файлу или None при ошибке
        """
        import gzip
        
        def compress():
            try:
                compressed_path = source_path.with_suffix(source_path.suffix + '.gz')
                with open(source_path, 'rb') as f_in:
                    with gzip.open(compressed_path, 'wb', compresslevel=compression_level) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                logger.info(f"Compressed {source_path} to {compressed_path}")
                return compressed_path
            except Exception as e:
                logger.error(f"Failed to compress {source_path}: {e}")
                return None
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, compress)
    
    async def cleanup_old_files(
        self,
        directory: Path,
        days_to_keep: int = 7,
        pattern: str = "*"
    ) -> int:
        """
        Очистка старых файлов
        
        Args:
            directory: директория для очистки
            days_to_keep: сколько дней хранить файлы
            pattern: шаблон имен файлов (glob)
        
        Returns:
            Количество удаленных файлов
        """
        def cleanup():
            count = 0
            cutoff_time = datetime.now().timestamp() - (days_to_keep * 86400)
            
            for file_path in directory.glob(pattern):
                if file_path.is_file():
                    try:
                        if file_path.stat().st_mtime < cutoff_time:
                            file_path.unlink()
                            count += 1
                            logger.debug(f"Deleted old file: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to delete {file_path}: {e}")
            
            logger.info(f"Cleaned up {count} old files from {directory}")
            return count
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, cleanup)
    
    async def cleanup_empty_directories(self, root_directory: Path) -> int:
        """
        Удаление пустых директорий
        
        Returns:
            Количество удаленных директорий
        """
        def cleanup():
            count = 0
            # Идем от вложенных к корневым
            for dirpath, dirnames, filenames in os.walk(root_directory, topdown=False):
                current_dir = Path(dirpath)
                if current_dir != root_directory:
                    try:
                        if not any(current_dir.iterdir()):
                            current_dir.rmdir()
                            count += 1
                            logger.debug(f"Deleted empty directory: {current_dir}")
                    except Exception as e:
                        logger.error(f"Failed to delete {current_dir}: {e}")
            
            logger.info(f"Cleaned up {count} empty directories")
            return count
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, cleanup)
    
    def get_directory_size(self, directory: Path) -> int:
        """
        Получить размер директории в байтах
        """
        total_size = 0
        for file_path in directory.rglob('*'):
            if file_path.is_file():
                try:
                    total_size += file_path.stat().st_size
                except Exception as e:
                    logger.error(f"Failed to get size of {file_path}: {e}")
        return total_size
    
    def get_directory_stats(self, directory: Path) -> dict:
        """
        Получить статистику директории
        """
        stats = {
            'total_files': 0,
            'total_size': 0,
            'file_types': {},
            'largest_files': []
        }
        
        file_sizes = []
        
        for file_path in directory.rglob('*'):
            if file_path.is_file():
                try:
                    size = file_path.stat().st_size
                    stats['total_files'] += 1
                    stats['total_size'] += size
                    
                    # Подсчет по типам
                    ext = file_path.suffix.lower() or 'no_extension'
                    stats['file_types'][ext] = stats['file_types'].get(ext, 0) + 1
                    
                    # Для топ-10 самых больших файлов
                    file_sizes.append((file_path, size))
                except Exception as e:
                    logger.error(f"Failed to process {file_path}: {e}")
        
        # Топ-10 самых больших файлов
        file_sizes.sort(key=lambda x: x[1], reverse=True)
        stats['largest_files'] = [
            {
                'path': str(path),
                'size': size,
                'size_mb': round(size / (1024 * 1024), 2)
            }
            for path, size in file_sizes[:10]
        ]
        
        stats['total_size_mb'] = round(stats['total_size'] / (1024 * 1024), 2)
        
        return stats
    
    async def optimize_storage(
        self,
        directory: Path,
        days_to_keep: int = 7,
        compress_old_files: bool = True
    ) -> dict:
        """
        Комплексная оптимизация хранилища
        
        Returns:
            Словарь со статистикой оптимизации
        """
        logger.info(f"Starting storage optimization for {directory}")
        
        stats = {
            'deleted_files': 0,
            'deleted_directories': 0,
            'compressed_files': 0,
            'freed_space': 0
        }
        
        # 1. Удаление старых файлов
        deleted_files = await self.cleanup_old_files(directory, days_to_keep)
        stats['deleted_files'] = deleted_files
        
        # 2. Сжатие старых файлов (если включено)
        if compress_old_files:
            cutoff_time = datetime.now().timestamp() - ((days_to_keep - 1) * 86400)
            for file_path in directory.rglob('*'):
                if file_path.is_file() and not file_path.suffix == '.gz':
                    try:
                        if file_path.stat().st_mtime < cutoff_time:
                            compressed = await self.compress_file_async(file_path)
                            if compressed:
                                original_size = file_path.stat().st_size
                                file_path.unlink()
                                compressed_size = compressed.stat().st_size
                                stats['compressed_files'] += 1
                                stats['freed_space'] += (original_size - compressed_size)
                    except Exception as e:
                        logger.error(f"Failed to compress {file_path}: {e}")
        
        # 3. Удаление пустых директорий
        deleted_dirs = await self.cleanup_empty_directories(directory)
        stats['deleted_directories'] = deleted_dirs
        
        stats['freed_space_mb'] = round(stats['freed_space'] / (1024 * 1024), 2)
        
        logger.info(f"Storage optimization complete: {stats}")
        return stats


class FileCache:
    """Простой кеш для часто читаемых небольших файлов"""
    
    def __init__(self, max_size: int = 100):
        self.cache = {}
        self.max_size = max_size
        self.access_times = {}
    
    def get(self, file_path: Path) -> Optional[bytes]:
        """Получить содержимое файла из кеша"""
        key = str(file_path)
        if key in self.cache:
            self.access_times[key] = datetime.now()
            logger.debug(f"File cache hit: {file_path}")
            return self.cache[key]
        return None
    
    def set(self, file_path: Path, content: bytes):
        """Добавить файл в кеш"""
        if len(self.cache) >= self.max_size:
            # Удаляем самый старый по времени доступа
            oldest_key = min(self.access_times, key=self.access_times.get)
            del self.cache[oldest_key]
            del self.access_times[oldest_key]
        
        key = str(file_path)
        self.cache[key] = content
        self.access_times[key] = datetime.now()
        logger.debug(f"File cached: {file_path}")
    
    def clear(self):
        """Очистить кеш"""
        self.cache.clear()
        self.access_times.clear()


# Глобальные экземпляры
_file_optimizer: Optional[FileOptimizer] = None
_file_cache: Optional[FileCache] = None


def get_file_optimizer() -> FileOptimizer:
    """Получить глобальный экземпляр FileOptimizer"""
    global _file_optimizer
    if _file_optimizer is None:
        _file_optimizer = FileOptimizer()
    return _file_optimizer


def get_file_cache() -> FileCache:
    """Получить глобальный экземпляр FileCache"""
    global _file_cache
    if _file_cache is None:
        _file_cache = FileCache()
    return _file_cache

