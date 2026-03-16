"""Сервис для отправки email приглашений"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def _get_smtp_config():
    """Получает конфигурацию SMTP из переменных окружения"""
    password = os.getenv("SMTP_PASSWORD", "")
    # Убираем кавычки, если они есть
    if password.startswith('"') and password.endswith('"'):
        password = password[1:-1]
    elif password.startswith("'") and password.endswith("'"):
        password = password[1:-1]
    
    return {
        "host": os.getenv("SMTP_HOST"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER"),
        "password": password,
        "from_email": os.getenv("SMTP_FROM_EMAIL", os.getenv("SMTP_USER", "")),
        "from_name": os.getenv("SMTP_FROM_NAME", "UpStat"),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    }


def send_invitation_email(invited_email: str, invite_link: str, team_name: str, manager_name: str):
    """
    Отправляет email с приглашением в команду.
    
    Args:
        invited_email: Email приглашённого пользователя
        invite_link: Ссылка для принятия приглашения
        team_name: Название команды
        manager_name: Имя менеджера, который отправил приглашение
    """
    config = _get_smtp_config()
    
    # Если SMTP не настроен — только логируем
    if not config["host"] or not config["user"] or not config["password"]:
        logger.info(f"Team invitation email -> {invited_email} | link={invite_link[:50]}...")
        logger.warning("SMTP не настроен, письмо не отправлено")
        return
    
    try:
        # Формируем письмо (HTML + текстовая версия)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Приглашение в команду {team_name}"
        msg["From"] = f"{config['from_name']} <{config['from_email']}>"
        msg["To"] = invited_email
        
        # HTML версия с красивым дизайном
        html_body = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 30px; border-radius: 12px; text-align: center; margin-bottom: 30px;">
                <h1 style="color: white; margin: 0; font-size: 28px;">Приглашение в команду</h1>
            </div>
            
            <div style="background: #f8fafc; padding: 30px; border-radius: 12px; margin-bottom: 30px;">
                <p style="font-size: 16px; margin: 0 0 15px 0;">
                    <strong>{manager_name}</strong> приглашает вас присоединиться к команде <strong>{team_name}</strong>.
                </p>
                <p style="font-size: 14px; color: #64748b; margin: 0;">
                    Нажмите на кнопку ниже, чтобы принять приглашение и начать работу в команде.
                </p>
            </div>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{invite_link}" 
                   style="display: inline-block; background: linear-gradient(135deg, #2563eb, #1d4ed8); 
                          color: white; padding: 14px 32px; text-decoration: none; 
                          border-radius: 8px; font-weight: 600; font-size: 16px;
                          box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);">
                    Принять приглашение
                </a>
            </div>
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb; text-align: center;">
                <p style="font-size: 12px; color: #94a3b8; margin: 0;">
                    Если кнопка не работает, скопируйте и вставьте эту ссылку в браузер:<br>
                    <a href="{invite_link}" style="color: #2563eb; word-break: break-all;">{invite_link}</a>
                </p>
                <p style="font-size: 12px; color: #94a3b8; margin: 10px 0 0 0;">
                    Срок действия приглашения: 14 дней
                </p>
            </div>
        </body>
        </html>
        """
        
        # Текстовая версия (fallback)
        text_body = f"""Приглашение в команду {team_name}

{manager_name} приглашает вас присоединиться к команде {team_name}.

Принять приглашение: {invite_link}

Срок действия приглашения: 14 дней

---
UpStat
        """
        
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        
        # Отправляем через SMTP
        with smtplib.SMTP(config["host"], config["port"]) as server:
            if config["use_tls"]:
                server.starttls()
            server.login(config["user"], config["password"])
            server.send_message(msg)
        
        logger.info(f"✅ Email отправлен: {config['from_email']} -> {invited_email}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки email: {str(e)}", exc_info=True)


def send_password_reset_email(user_email: str, reset_link: str, user_name: str = None):
    """
    Отправляет email с ссылкой для восстановления пароля.
    
    Args:
        user_email: Email пользователя
        reset_link: Ссылка для сброса пароля
        user_name: Имя пользователя (опционально)
    """
    config = _get_smtp_config()
    
    # Если SMTP не настроен — только логируем
    if not config["host"] or not config["user"] or not config["password"]:
        logger.info(f"Password reset email -> {user_email} | link={reset_link[:50]}...")
        logger.warning("SMTP не настроен, письмо не отправлено")
        return
    
    try:
        # Формируем письмо (HTML + текстовая версия)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Восстановление пароля"
        msg["From"] = f"{config['from_name']} <{config['from_email']}>"
        msg["To"] = user_email
        
        # HTML версия с красивым дизайном
        greeting = f"Здравствуйте, {user_name}!" if user_name else "Здравствуйте!"
        
        html_body = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 30px; border-radius: 12px; text-align: center; margin-bottom: 30px;">
                <h1 style="color: white; margin: 0; font-size: 28px;">Восстановление пароля</h1>
            </div>
            
            <div style="background: #f8fafc; padding: 30px; border-radius: 12px; margin-bottom: 30px;">
                <p style="font-size: 16px; margin: 0 0 15px 0;">
                    {greeting}
                </p>
                <p style="font-size: 14px; color: #64748b; margin: 0 0 15px 0;">
                    Вы запросили восстановление пароля для вашего аккаунта. Нажмите на кнопку ниже, чтобы установить новый пароль.
                </p>
                <p style="font-size: 14px; color: #64748b; margin: 0;">
                    Если вы не запрашивали восстановление пароля, просто проигнорируйте это письмо.
                </p>
            </div>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{reset_link}" 
                   style="display: inline-block; background: linear-gradient(135deg, #2563eb, #1d4ed8); 
                          color: white; padding: 14px 32px; text-decoration: none; 
                          border-radius: 8px; font-weight: 600; font-size: 16px;
                          box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);">
                    Восстановить пароль
                </a>
            </div>
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb; text-align: center;">
                <p style="font-size: 12px; color: #94a3b8; margin: 0;">
                    Если кнопка не работает, скопируйте и вставьте эту ссылку в браузер:<br>
                    <a href="{reset_link}" style="color: #2563eb; word-break: break-all;">{reset_link}</a>
                </p>
                <p style="font-size: 12px; color: #94a3b8; margin: 10px 0 0 0;">
                    Срок действия ссылки: 1 час
                </p>
            </div>
        </body>
        </html>
        """
        
        # Текстовая версия (fallback)
        text_body = f"""Восстановление пароля

{greeting}

Вы запросили восстановление пароля для вашего аккаунта. Перейдите по ссылке ниже, чтобы установить новый пароль.

{reset_link}

Если вы не запрашивали восстановление пароля, просто проигнорируйте это письмо.

Срок действия ссылки: 1 час

---
{config['from_name']}
        """
        
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        
        # Отправляем через SMTP
        with smtplib.SMTP(config["host"], config["port"]) as server:
            if config["use_tls"]:
                server.starttls()
            server.login(config["user"], config["password"])
            server.send_message(msg)
        
        logger.info(f"✅ Password reset email отправлен: {config['from_email']} -> {user_email}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки email восстановления пароля: {str(e)}", exc_info=True)

