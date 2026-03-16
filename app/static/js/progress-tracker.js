/**
 * Компонент отслеживания прогресса операций
 */

class ProgressTracker {
    constructor() {
        this.container = null;
        this.activeOperations = new Map();
        this.pollInterval = 2000; // 2 секунды
        this.intervalId = null;
        this.init();
    }

    init() {
        // Создаем контейнер для прогресса
        this.container = document.createElement('div');
        this.container.className = 'progress-tracker-container';
        document.body.appendChild(this.container);

        // Запускаем периодический опрос
        this.startPolling();
    }

    startPolling() {
        if (this.intervalId) return;
        
        this.intervalId = setInterval(() => {
            this.fetchActiveOperations();
        }, this.pollInterval);
        
        // Первый запрос сразу
        this.fetchActiveOperations();
    }

    stopPolling() {
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
        }
    }

    async fetchActiveOperations() {
        try {
            const response = await fetch('/api/progress/active/list?limit=5');
            if (!response.ok) return;

            const data = await response.json();
            this.updateOperations(data.operations || []);
        } catch (error) {
            console.error('Failed to fetch active operations:', error);
        }
    }

    async fetchOperation(operationId) {
        try {
            const response = await fetch(`/api/progress/${operationId}`);
            if (!response.ok) return null;

            return await response.json();
        } catch (error) {
            console.error('Failed to fetch operation:', error);
            return null;
        }
    }

    updateOperations(operations) {
        // Удаляем завершенные операции
        for (const [id, element] of this.activeOperations.entries()) {
            const found = operations.find(op => op.operation_id === id);
            if (!found) {
                this.removeOperation(id);
            }
        }

        // Добавляем или обновляем активные операции
        operations.forEach(operation => {
            if (operation.status === 'in_progress' || operation.status === 'pending') {
                this.showProgress(operation);
            } else if (operation.status === 'completed' || operation.status === 'failed') {
                // Показываем завершенное состояние на 3 секунды, затем удаляем
                this.showProgress(operation);
                setTimeout(() => {
                    this.removeOperation(operation.operation_id);
                }, 3000);
            }
        });
    }

    showProgress(operation) {
        const id = operation.operation_id;
        let element = this.activeOperations.get(id);

        if (!element) {
            element = this.createProgressElement(operation);
            this.container.appendChild(element);
            this.activeOperations.set(id, element);
            
            // Анимация появления
            setTimeout(() => element.classList.add('show'), 10);
        } else {
            this.updateProgressElement(element, operation);
        }
    }

    createProgressElement(operation) {
        const element = document.createElement('div');
        element.className = `progress-tracker progress-tracker-${operation.status}`;
        element.dataset.id = operation.operation_id;

        element.innerHTML = `
            <div class="progress-tracker-header">
                <div class="progress-tracker-title">${this.escapeHtml(operation.title)}</div>
                <div class="progress-tracker-percentage">${operation.percentage}%</div>
            </div>
            <div class="progress-tracker-bar-container">
                <div class="progress-tracker-bar" style="width: ${operation.percentage}%"></div>
            </div>
            <div class="progress-tracker-details">
                <div class="progress-tracker-stage">${this.escapeHtml(operation.stage_message)}</div>
                ${operation.estimated_time_formatted ? 
                    `<div class="progress-tracker-time">Осталось: ${operation.estimated_time_formatted}</div>` : 
                    ''}
            </div>
            ${operation.error_message ? 
                `<div class="progress-tracker-error">${this.escapeHtml(operation.error_message)}</div>` : 
                ''}
        `;

        return element;
    }

    updateProgressElement(element, operation) {
        // Обновляем класс статуса
        element.className = `progress-tracker progress-tracker-${operation.status} show`;

        // Обновляем процент
        const percentageEl = element.querySelector('.progress-tracker-percentage');
        if (percentageEl) {
            percentageEl.textContent = `${operation.percentage}%`;
        }

        // Обновляем прогресс-бар
        const barEl = element.querySelector('.progress-tracker-bar');
        if (barEl) {
            barEl.style.width = `${operation.percentage}%`;
        }

        // Обновляем сообщение
        const stageEl = element.querySelector('.progress-tracker-stage');
        if (stageEl) {
            stageEl.textContent = operation.stage_message;
        }

        // Обновляем время
        const timeEl = element.querySelector('.progress-tracker-time');
        if (timeEl && operation.estimated_time_formatted) {
            timeEl.textContent = `Осталось: ${operation.estimated_time_formatted}`;
        } else if (timeEl && !operation.estimated_time_formatted) {
            timeEl.remove();
        }

        // Добавляем ошибку если есть
        const existingErrorEl = element.querySelector('.progress-tracker-error');
        if (operation.error_message && !existingErrorEl) {
            const errorEl = document.createElement('div');
            errorEl.className = 'progress-tracker-error';
            errorEl.textContent = operation.error_message;
            element.appendChild(errorEl);
        } else if (!operation.error_message && existingErrorEl) {
            existingErrorEl.remove();
        }
    }

    removeOperation(id) {
        const element = this.activeOperations.get(id);
        if (!element) return;

        // Анимация скрытия
        element.classList.remove('show');
        setTimeout(() => {
            element.remove();
            this.activeOperations.delete(id);
        }, 300);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    destroy() {
        this.stopPolling();
        this.activeOperations.forEach((element, id) => {
            element.remove();
        });
        this.activeOperations.clear();
        if (this.container) {
            this.container.remove();
        }
    }
}

// Глобальный экземпляр
window.progressTracker = new ProgressTracker();

