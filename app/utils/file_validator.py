"""
Утилиты для валидации файлов
"""
import os
from pathlib import Path
from typing import Tuple, Optional, List
from services.error_handler import ValidationError, FileProcessingError

# Попытка импорта python-magic (опционально)
try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False


# Разрешенные MIME типы с их магическими байтами
ALLOWED_MIME_TYPES = {
    # Изображения
    "image/png": [b"\x89PNG"],
    "image/jpeg": [b"\xFF\xD8\xFF"],
    "image/webp": [b"RIFF"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    
    # Документы
    "application/pdf": [b"%PDF"],
    "text/plain": [],  # текст может начинаться с любых символов
    
    # MS Office
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [b"PK\x03\x04"],  # .docx
    "application/msword": [b"\xD0\xCF\x11\xE0"],  # .doc
    "application/zip": [b"PK\x03\x04"],
    
    # Аудио - расширенный список форматов
    "audio/mpeg": [b"\xFF\xFB", b"\xFF\xF3", b"\xFF\xF2", b"ID3"],  # MP3
    "audio/mp3": [b"\xFF\xFB", b"\xFF\xF3", b"\xFF\xF2", b"ID3"],
    "audio/wav": [b"RIFF"],  # WAV (может конфликтовать с WebP, но проверяем по расширению)
    "audio/x-wav": [b"RIFF"],
    "audio/wave": [b"RIFF"],
    "audio/m4a": [b"\x00\x00\x00\x18ftypM4A", b"\x00\x00\x00\x1cftypM4A"],
    "audio/x-m4a": [b"\x00\x00\x00\x18ftypM4A", b"\x00\x00\x00\x1cftypM4A"],
    "audio/mp4": [b"\x00\x00\x00\x18ftypM4A", b"\x00\x00\x00\x1cftypM4A", b"\x00\x00\x00\x20ftyp"],
    "audio/aac": [b"\xFF\xF1", b"\xFF\xF9"],
    "audio/opus": [b"OggS"],
    "audio/ogg": [b"OggS"],
    "audio/vorbis": [b"OggS"],
    "audio/flac": [b"fLaC"],  # FLAC
    "audio/x-flac": [b"fLaC"],
    "audio/x-ms-wma": [],  # WMA - сложно определить по magic bytes
    "audio/wma": [],
    "audio/aiff": [b"FORM"],  # AIFF
    "audio/x-aiff": [b"FORM"],
    "audio/amr": [],  # AMR
    "audio/3gpp": [],  # 3GP
    "audio/3gp": [],
    "audio/x-matroska": [],  # MKA
    "audio/webm": [b"\x1a\x45\xdf\xa3"],  # WebM audio
    "audio/x-m4b": [b"\x00\x00\x00\x18ftypM4B"],  # M4B (audiobook)
    "audio/m4b": [b"\x00\x00\x00\x18ftypM4B"],
    "audio/x-aac": [b"\xFF\xF1", b"\xFF\xF9"],
    "audio/basic": [],  # AU
    "audio/x-au": [],
    "audio/midi": [b"MThd"],  # MIDI
    "audio/mid": [b"MThd"],
    "audio/x-midi": [b"MThd"],
}

# Расширения файлов для каждого MIME типа
MIME_TO_EXTENSIONS = {
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "image/webp": [".webp"],
    "image/gif": [".gif"],
    "application/pdf": [".pdf"],
    "text/plain": [".txt"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    "application/msword": [".doc"],
    "application/zip": [".zip"],
    "audio/mpeg": [".mp3"],
    "audio/mp3": [".mp3"],
    "audio/wav": [".wav"],
    "audio/x-wav": [".wav"],
    "audio/wave": [".wav"],
    "audio/m4a": [".m4a"],
    "audio/x-m4a": [".m4a"],
    "audio/mp4": [".m4a", ".mp4"],
    "audio/aac": [".aac"],
    "audio/x-aac": [".aac"],
    "audio/opus": [".opus"],
    "audio/ogg": [".ogg", ".oga"],
    "audio/vorbis": [".ogg"],
    "audio/flac": [".flac"],
    "audio/x-flac": [".flac"],
    "audio/x-ms-wma": [".wma"],
    "audio/wma": [".wma"],
    "audio/aiff": [".aiff", ".aif"],
    "audio/x-aiff": [".aiff", ".aif"],
    "audio/amr": [".amr"],
    "audio/3gpp": [".3gp", ".3gpp"],
    "audio/3gp": [".3gp"],
    "audio/x-matroska": [".mka"],
    "audio/webm": [".weba"],
    "audio/x-m4b": [".m4b"],
    "audio/m4b": [".m4b"],
    "audio/basic": [".au"],
    "audio/x-au": [".au"],
    "audio/midi": [".mid", ".midi"],
    "audio/mid": [".mid"],
    "audio/x-midi": [".mid", ".midi"],
}

# Максимальные размеры файлов по типу (в байтах)
MAX_FILE_SIZES = {
    "image": 10 * 1024 * 1024,  # 10 MB
    "document": 25 * 1024 * 1024,  # 25 MB
    "audio": 100 * 1024 * 1024,  # 100 MB
    "default": 25 * 1024 * 1024,  # 25 MB
}


class FileValidator:
    """Валидатор файлов с проверкой magic bytes"""
    
    @staticmethod
    def validate_file_size(file_size: int, mime_type: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка размера файла
        
        Returns:
            Tuple[bool, Optional[str]]: (валиден ли размер, сообщение об ошибке)
        """
        # Определяем тип файла
        if mime_type.startswith("image/"):
            max_size = MAX_FILE_SIZES["image"]
            type_name = "изображения"
        elif mime_type.startswith("audio/"):
            max_size = MAX_FILE_SIZES["audio"]
            type_name = "аудио"
        elif mime_type in ["application/pdf", "text/plain", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"]:
            max_size = MAX_FILE_SIZES["document"]
            type_name = "документа"
        else:
            max_size = MAX_FILE_SIZES["default"]
            type_name = "файла"
        
        if file_size > max_size:
            max_mb = max_size / (1024 * 1024)
            current_mb = file_size / (1024 * 1024)
            return False, f"Размер {type_name} ({current_mb:.1f} МБ) превышает максимально допустимый ({max_mb:.0f} МБ)"
        
        if file_size == 0:
            return False, f"Файл пустой (0 байт)"
        
        return True, None
    
    @staticmethod
    def check_magic_bytes(file_data: bytes, declared_mime: str) -> bool:
        """
        Проверка magic bytes файла
        
        Args:
            file_data: Первые байты файла
            declared_mime: Заявленный MIME тип
            
        Returns:
            bool: Соответствуют ли magic bytes заявленному типу
        """
        if declared_mime not in ALLOWED_MIME_TYPES:
            return False
        
        magic_bytes = ALLOWED_MIME_TYPES[declared_mime]
        
        # Для текстовых файлов пропускаем проверку magic bytes
        if declared_mime == "text/plain":
            return True
        
        # Проверяем, начинается ли файл с одного из возможных magic bytes
        for magic in magic_bytes:
            if file_data.startswith(magic):
                return True
        
        return False
    
    @staticmethod
    def validate_file_extension(filename: str, mime_type: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка соответствия расширения файла MIME типу
        
        Returns:
            Tuple[bool, Optional[str]]: (валидно ли расширение, сообщение об ошибке)
        """
        ext = Path(filename).suffix.lower()
        
        if mime_type not in MIME_TO_EXTENSIONS:
            return False, f"Неподдерживаемый тип файла: {mime_type}"
        
        allowed_extensions = MIME_TO_EXTENSIONS[mime_type]
        
        if ext not in allowed_extensions:
            return False, f"Расширение файла ({ext}) не соответствует типу ({', '.join(allowed_extensions)})"
        
        return True, None
    
    @staticmethod
    def detect_mime_type(file_data: bytes, filename: str) -> Optional[str]:
        """
        Определение MIME типа по содержимому файла
        
        Args:
            file_data: Данные файла
            filename: Имя файла
            
        Returns:
            Optional[str]: Определенный MIME тип или None
        """
        # Если python-magic установлен, используем его
        if HAS_MAGIC:
            try:
                mime = magic.from_buffer(file_data, mime=True)
                return mime
            except Exception:
                pass
        
        # Fallback - проверяем по magic bytes вручную
        for mime_type, magic_bytes_list in ALLOWED_MIME_TYPES.items():
            for magic_bytes in magic_bytes_list:
                if file_data.startswith(magic_bytes):
                    return mime_type
        
        # Последний fallback - по расширению
        ext = Path(filename).suffix.lower()
        for mime_type, extensions in MIME_TO_EXTENSIONS.items():
            if ext in extensions:
                return mime_type
        
        return None
    
    @staticmethod
    def validate_file(
        file_data: bytes,
        filename: str,
        declared_mime: str,
        strict_mode: bool = True
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Полная валидация файла
        
        Args:
            file_data: Данные файла
            filename: Имя файла
            declared_mime: Заявленный MIME тип из загрузки
            strict_mode: Строгий режим (проверка magic bytes)
            
        Returns:
            Tuple[bool, Optional[str], Optional[str]]: 
                (валиден ли файл, сообщение об ошибке, реальный MIME тип)
        """
        # 1. Проверяем размер
        size_valid, size_error = FileValidator.validate_file_size(len(file_data), declared_mime)
        if not size_valid:
            return False, size_error, None
        
        # 2. Проверяем расширение
        ext_valid, ext_error = FileValidator.validate_file_extension(filename, declared_mime)
        if not ext_valid:
            return False, ext_error, None
        
        # 3. Определяем реальный MIME тип
        real_mime = FileValidator.detect_mime_type(file_data, filename)
        
        # 4. В строгом режиме проверяем magic bytes
        if strict_mode and real_mime:
            # Проверяем, что реальный MIME соответствует заявленному
            # или относится к той же категории (например, audio/mpeg и audio/mp3)
            declared_category = declared_mime.split('/')[0]
            real_category = real_mime.split('/')[0]
            
            if declared_category != real_category:
                return False, f"Тип файла ({real_mime}) не соответствует заявленному ({declared_mime})", real_mime
        
        # 5. Проверяем, что тип файла разрешен
        if declared_mime not in ALLOWED_MIME_TYPES:
            return False, f"Неподдерживаемый тип файла: {declared_mime}", real_mime
        
        return True, None, real_mime
    
    @staticmethod
    def get_file_type_display_name(mime_type: str) -> str:
        """Получить понятное название типа файла"""
        display_names = {
            "image/png": "PNG изображение",
            "image/jpeg": "JPEG изображение",
            "image/webp": "WebP изображение",
            "image/gif": "GIF изображение",
            "application/pdf": "PDF документ",
            "text/plain": "Текстовый файл",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word документ (.docx)",
            "application/msword": "Word документ (.doc)",
            "application/zip": "ZIP архив",
            "audio/mpeg": "MP3 аудио",
            "audio/mp3": "MP3 аудио",
            "audio/wav": "WAV аудио",
            "audio/x-wav": "WAV аудио",
            "audio/wave": "WAV аудио",
            "audio/m4a": "M4A аудио",
            "audio/x-m4a": "M4A аудио",
            "audio/mp4": "MP4 аудио",
            "audio/aac": "AAC аудио",
            "audio/x-aac": "AAC аудио",
            "audio/opus": "Opus аудио",
            "audio/ogg": "OGG аудио",
            "audio/vorbis": "Vorbis аудио",
            "audio/flac": "FLAC аудио",
            "audio/x-flac": "FLAC аудио",
            "audio/x-ms-wma": "WMA аудио",
            "audio/wma": "WMA аудио",
            "audio/aiff": "AIFF аудио",
            "audio/x-aiff": "AIFF аудио",
            "audio/amr": "AMR аудио",
            "audio/3gpp": "3GP аудио",
            "audio/3gp": "3GP аудио",
            "audio/x-matroska": "MKA аудио",
            "audio/webm": "WebM аудио",
            "audio/x-m4b": "M4B аудио",
            "audio/m4b": "M4B аудио",
            "audio/basic": "AU аудио",
            "audio/x-au": "AU аудио",
            "audio/midi": "MIDI",
            "audio/mid": "MIDI",
            "audio/x-midi": "MIDI",
        }
        return display_names.get(mime_type, mime_type)
    
    @staticmethod
    def validate_audio_file(file_data: bytes, filename: str, declared_mime: str) -> Tuple[bool, Optional[str]]:
        """
        Специализированная валидация для аудио файлов
        Более гибкая валидация - принимает файлы с audio/* MIME и аудио расширениями,
        даже если magic bytes определяют их как что-то другое (например, WAV может определяться как WebP)
        
        Returns:
            Tuple[bool, Optional[str]]: (валиден ли файл, сообщение об ошибке)
        """
        # Проверяем, что это аудио файл
        if not declared_mime.startswith("audio/"):
            return False, "Файл не является аудио файлом"
        
        # 1. Проверяем размер файла
        size_valid, size_error = FileValidator.validate_file_size(len(file_data), declared_mime)
        if not size_valid:
            return False, size_error
        
        # 2. Проверяем расширение файла
        ext = Path(filename).suffix.lower()
        audio_extensions = set()
        for mime_type, extensions in MIME_TO_EXTENSIONS.items():
            if mime_type.startswith("audio/"):
                audio_extensions.update(extensions)
        
        if ext not in audio_extensions:
            return False, f"Расширение файла ({ext}) не соответствует аудио формату"
        
        # 3. Проверяем, что заявленный MIME тип поддерживается
        if declared_mime not in ALLOWED_MIME_TYPES:
            return False, f"Неподдерживаемый тип файла: {declared_mime}"
        
        # 4. Для аудио файлов делаем более гибкую проверку:
        # Если файл заявлен как audio/* и имеет аудио расширение, принимаем его
        # даже если magic bytes определяют его как что-то другое
        # Это особенно важно для WAV файлов, которые могут определяться как image/webp
        # из-за общего формата контейнера RIFF
        
        # Дополнительные проверки для аудио
        # Минимальный размер аудио файла (например, 1 KB)
        if len(file_data) < 1024:
            return False, "Аудио файл слишком маленький. Возможно, файл поврежден"
        
        return True, None
    
    @staticmethod
    def get_supported_formats_list() -> List[str]:
        """Получить список поддерживаемых форматов"""
        formats = []
        seen = set()
        
        for mime_type, extensions in MIME_TO_EXTENSIONS.items():
            display_name = FileValidator.get_file_type_display_name(mime_type)
            ext_str = ", ".join(extensions)
            format_str = f"{display_name} ({ext_str})"
            
            if format_str not in seen:
                formats.append(format_str)
                seen.add(format_str)
        
        return sorted(formats)


def validate_uploaded_file(
    file_data: bytes,
    filename: str,
    declared_mime: str,
    is_audio: bool = False
) -> None:
    """
    Валидация загруженного файла с выбросом исключений
    
    Args:
        file_data: Данные файла
        filename: Имя файла
        declared_mime: Заявленный MIME тип
        is_audio: Флаг, что это должен быть аудио файл
        
    Raises:
        ValidationError: Если файл не прошел валидацию
    """
    if is_audio:
        valid, error = FileValidator.validate_audio_file(file_data, filename, declared_mime)
        if not valid:
            raise FileProcessingError(
                message=f"Ошибка валидации аудио файла: {error}",
                filename=filename,
                user_message=f"Не удалось обработать аудио файл '{filename}'. {error}",
                details={"filename": filename, "mime_type": declared_mime, "size": len(file_data)}
            )
    else:
        valid, error, real_mime = FileValidator.validate_file(file_data, filename, declared_mime)
        if not valid:
            raise ValidationError(
                message=f"Ошибка валидации файла: {error}",
                user_message=f"Не удалось обработать файл '{filename}'. {error}",
                details={"filename": filename, "declared_mime": declared_mime, "real_mime": real_mime, "size": len(file_data)}
            )

