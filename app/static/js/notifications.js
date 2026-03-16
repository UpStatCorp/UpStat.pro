/**
 * Система уведомлений
 */

class NotificationManager {
    constructor() {
        this.container = null;
        this.notifications = new Map();
        this.maxNotifications = 5;
        this.init();
    }

    init() {
        // Создаем контейнер для уведомлений
        this.container = document.createElement('div');
        this.container.className = 'notification-container';
        document.body.appendChild(this.container);

        // Подписываемся на события от сервера
        this.setupServerNotifications();
    }

    /**
     * Показать уведомление
     */
    show(options) {
        const {
            id = this.generateId(),
            type = 'info',
            title,
            message,
            duration = 5000,
            priority = 2,
            dismissible = true,
            actionLabel = null,
            actionUrl = null,
            actionCallback = null
        } = options;

        // Проверяем лимит уведомлений
        if (this.notifications.size >= this.maxNotifications) {
            this.removeOldest();
        }

        // Создаем элемент уведомления
        const notification = this.createNotificationElement({
            id, type, title, message, priority, dismissible, actionLabel, actionUrl, actionCallback
        });

        // Добавляем в контейнер
        this.container.appendChild(notification);
        this.notifications.set(id, { element: notification, timer: null });

        // Анимация появления
        setTimeout(() => notification.classList.add('show'), 10);

        // Автоматическое скрытие
        if (duration > 0) {
            const timer = setTimeout(() => this.dismiss(id), duration);
            this.notifications.get(id).timer = timer;
        }

        return id;
    }

    createNotificationElement(options) {
        const { id, type, title, message, dismissible, actionLabel, actionUrl, actionCallback } = options;

        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.dataset.id = id;

        // Иконка
        const icon = this.getIcon(type);

        // HTML структура
        notification.innerHTML = `
            <div class="notification-icon">${icon}</div>
            <div class="notification-content">
                ${title ? `<div class="notification-title">${this.escapeHtml(title)}</div>` : ''}
                <div class="notification-message">${this.escapeHtml(message)}</div>
                ${actionLabel ? `<div class="notification-action"><button class="notification-action-btn">${this.escapeHtml(actionLabel)}</button></div>` : ''}
            </div>
            ${dismissible ? '<button class="notification-close" aria-label="Закрыть">&times;</button>' : ''}
        `;

        // Обработчик закрытия
        if (dismissible) {
            const closeBtn = notification.querySelector('.notification-close');
            closeBtn.addEventListener('click', () => this.dismiss(id));
        }

        // Обработчик действия
        if (actionLabel) {
            const actionBtn = notification.querySelector('.notification-action-btn');
            actionBtn.addEventListener('click', () => {
                if (actionCallback) {
                    actionCallback();
                } else if (actionUrl) {
                    window.location.href = actionUrl;
                }
                this.dismiss(id);
            });
        }

        return notification;
    }

    getIcon(type) {
        const icons = {
            success: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>',
            error: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>',
            warning: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>',
            info: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>',
            progress: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83"></path></svg>'
        };
        return icons[type] || icons.info;
    }

    dismiss(id) {
        const notification = this.notifications.get(id);
        if (!notification) return;

        // Очищаем таймер
        if (notification.timer) {
            clearTimeout(notification.timer);
        }

        // Анимация скрытия
        notification.element.classList.remove('show');
        setTimeout(() => {
            notification.element.remove();
            this.notifications.delete(id);
        }, 300);
    }

    dismissAll() {
        this.notifications.forEach((_, id) => this.dismiss(id));
    }

    removeOldest() {
        const firstKey = this.notifications.keys().next().value;
        if (firstKey) {
            this.dismiss(firstKey);
        }
    }

    generateId() {
        return `notif_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Хелперы для быстрого создания уведомлений
    success(message, title = 'Успешно', duration = 5000) {
        return this.show({ type: 'success', title, message, duration });
    }

    error(message, title = 'Ошибка', duration = 0) {
        return this.show({ type: 'error', title, message, duration });
    }

    warning(message, title = 'Внимание', duration = 8000) {
        return this.show({ type: 'warning', title, message, duration });
    }

    info(message, title = null, duration = 5000) {
        return this.show({ type: 'info', title, message, duration });
    }

    progress(message, title = 'Обработка...', duration = 0) {
        return this.show({ type: 'progress', title, message, duration });
    }

    /**
     * Настройка получения уведомлений с сервера
     */
    setupServerNotifications() {
        // Проверяем новые уведомления каждые 30 секунд
        this.pollInterval = setInterval(() => {
            this.fetchNotifications();
        }, 30000);

        // Первая загрузка
        this.fetchNotifications();
    }

    async fetchNotifications() {
        try {
            const response = await fetch('/api/notifications/unread');
            if (!response.ok) return;

            const data = await response.json();
            if (data.notifications && data.notifications.length > 0) {
                // Показываем только новые уведомления
                data.notifications.forEach(notif => {
                    if (!this.notifications.has(notif.id)) {
                        this.show({
                            id: notif.id,
                            type: notif.type,
                            title: notif.title,
                            message: notif.message,
                            duration: notif.duration,
                            dismissible: notif.dismissible,
                            actionLabel: notif.action_label,
                            actionUrl: notif.action_url
                        });
                    }
                });
            }
        } catch (error) {
            console.error('Failed to fetch notifications:', error);
        }
    }

    async markAsRead(notificationId) {
        try {
            await fetch(`/api/notifications/${notificationId}/read`, {
                method: 'POST'
            });
        } catch (error) {
            console.error('Failed to mark notification as read:', error);
        }
    }

    destroy() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
        }
        this.dismissAll();
        if (this.container) {
            this.container.remove();
        }
    }
}

// Глобальный экземпляр
window.notifications = new NotificationManager();

// API для совместимости
window.showNotification = (message, type = 'info', duration = 5000) => {
    return window.notifications.show({ type, message, duration });
};

