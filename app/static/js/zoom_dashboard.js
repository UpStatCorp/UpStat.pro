// Zoom Dashboard JavaScript
// Управление Zoom встречами с ИИ-агентом

class ZoomDashboard {
    constructor() {
        this.meetings = [];
        this.currentMeeting = null;
        this.init();
    }

    init() {
        this.loadMeetings();
        this.setupEventListeners();
        this.setDefaultDateTime();
    }

    setupEventListeners() {
        // Форма создания встречи
        const form = document.getElementById('create-meeting-form');
        if (form) {
            form.addEventListener('submit', (e) => this.handleCreateMeeting(e));
        }

        // Обновление времени каждую минуту
        setInterval(() => this.updateMeetingTimes(), 60000);
    }

    setDefaultDateTime() {
        // Устанавливаем время начала по умолчанию (через 15 минут)
        const now = new Date();
        now.setMinutes(now.getMinutes() + 15);
        now.setSeconds(0);
        now.setMilliseconds(0);
        
        // Инициализируем селекты даты и времени
        this.initDateSelects(now);
        this.initTimeSelects(now);
    }

    initDateSelects(defaultDate) {
        const daySelect = document.getElementById('meeting-day');
        const monthSelect = document.getElementById('meeting-month');
        const yearSelect = document.getElementById('meeting-year');
        
        if (!daySelect || !monthSelect || !yearSelect) return;

        const now = new Date();
        const currentYear = now.getFullYear();
        
        // Заполняем годы (текущий + 2 следующих)
        for (let year = currentYear; year <= currentYear + 2; year++) {
            const option = document.createElement('option');
            option.value = year;
            option.textContent = year;
            if (year === defaultDate.getFullYear()) option.selected = true;
            yearSelect.appendChild(option);
        }

        // Заполняем месяцы
        const months = [
            'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
            'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
        ];
        
        months.forEach((month, index) => {
            const option = document.createElement('option');
            option.value = index;
            option.textContent = month;
            if (index === defaultDate.getMonth()) option.selected = true;
            monthSelect.appendChild(option);
        });

        // Заполняем дни
        this.updateDays(monthSelect.value, yearSelect.value, defaultDate.getDate());
        
        // Обработчики изменений
        monthSelect.addEventListener('change', () => this.updateDays(monthSelect.value, yearSelect.value));
        yearSelect.addEventListener('change', () => this.updateDays(monthSelect.value, yearSelect.value));
    }

    initTimeSelects(defaultDate) {
        const hourSelect = document.getElementById('meeting-hour');
        const minuteSelect = document.getElementById('meeting-minute');
        
        if (!hourSelect || !minuteSelect) return;

        const now = new Date();
        const minHour = now.getHours();
        const minMinute = now.getMinutes() + 5; // Минимум через 5 минут
        
        // Заполняем часы (с текущего часа + 1 до 23)
        for (let hour = minHour + 1; hour <= 23; hour++) {
            const option = document.createElement('option');
            option.value = hour;
            option.textContent = hour.toString().padStart(2, '0');
            if (hour === defaultDate.getHours()) option.selected = true;
            hourSelect.appendChild(option);
        }

        // Заполняем минуты (каждые 15 минут)
        for (let minute = 0; minute < 60; minute += 15) {
            const option = document.createElement('option');
            option.value = minute;
            option.textContent = minute.toString().padStart(2, '0');
            if (minute === defaultDate.getMinutes()) option.selected = true;
            minuteSelect.appendChild(option);
        }

        // Если текущее время + 15 минут попадает в прошлое, устанавливаем следующий час
        if (defaultDate <= now) {
            const nextHour = new Date(now.getTime() + 15 * 60 * 1000);
            hourSelect.value = nextHour.getHours();
            minuteSelect.value = Math.floor(nextHour.getMinutes() / 15) * 15;
        }
    }

    updateDays(month, year, selectedDay = 1) {
        const daySelect = document.getElementById('meeting-day');
        if (!daySelect) return;

        // Очищаем текущие дни
        daySelect.innerHTML = '';

        // Получаем количество дней в месяце
        const daysInMonth = new Date(year, parseInt(month) + 1, 0).getDate();
        
        // Заполняем дни
        for (let day = 1; day <= daysInMonth; day++) {
            const option = document.createElement('option');
            option.value = day;
            option.textContent = day;
            if (day === selectedDay) option.selected = true;
            daySelect.appendChild(option);
        }
    }

    getSelectedDateTime() {
        const day = document.getElementById('meeting-day')?.value;
        const month = document.getElementById('meeting-month')?.value;
        const year = document.getElementById('meeting-year')?.value;
        const hour = document.getElementById('meeting-hour')?.value;
        const minute = document.getElementById('meeting-minute')?.value;
        
        if (!day || !month || !year || !hour || !minute) return null;
        
        const date = new Date(parseInt(year), parseInt(month), parseInt(day), parseInt(hour), parseInt(minute));
        return date;
    }



    validateSelectedDateTime(selectedDateTime) {
        const now = new Date();
        const minTime = new Date(now.getTime() + 5 * 60 * 1000); // +5 минут
        
        if (selectedDateTime < minTime) {
            this.showError('Время начала должно быть минимум через 5 минут от текущего времени');
            return false;
        }
        
        return true;
    }

    async loadMeetings() {
        try {
            const response = await fetch('/api/zoom/meetings');
            if (response.ok) {
                const data = await response.json();
                this.meetings = data.meetings || [];
                this.renderMeetings();
            } else {
                console.error('Failed to load meetings:', response.statusText);
                this.showError('Ошибка загрузки встреч');
            }
        } catch (error) {
            console.error('Error loading meetings:', error);
            this.showError('Ошибка загрузки встреч');
        }
    }

    renderMeetings() {
        const container = document.getElementById('zoom-meetings-list');
        if (!container) return;

        if (this.meetings.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">
                        <svg viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/>
                        </svg>
                    </div>
                    <h3>Пока нет встреч</h3>
                    <p>Создайте свою первую Zoom встречу с ИИ-агентом</p>
                    <button class="btn btn-primary" onclick="openCreateMeetingModal()">
                        Создать встречу
                    </button>
                </div>
            `;
            return;
        }

        const meetingsHtml = this.meetings.map(meeting => this.renderMeetingItem(meeting)).join('');
        container.innerHTML = meetingsHtml;
    }

    renderMeetingItem(meeting) {
        const statusClass = this.getStatusClass(meeting.status);
        const statusText = this.getStatusText(meeting.status);
        const startTime = new Date(meeting.start_time);
        const formattedTime = startTime.toLocaleString('ru-RU', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });

        return `
            <div class="meeting-item ${statusClass}">
                <div class="meeting-header">
                    <div class="meeting-info">
                        <h4 class="meeting-topic">${this.escapeHtml(meeting.topic)}</h4>
                        <div class="meeting-meta">
                            <span class="meeting-time">
                                <svg class="icon-sm" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clip-rule="evenodd"/>
                                </svg>
                                ${formattedTime}
                            </span>
                            <span class="meeting-duration">
                                <svg class="icon-sm" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clip-rule="evenodd"/>
                                </svg>
                                ${meeting.duration_minutes} мин
                            </span>
                            <span class="meeting-status ${statusClass}">
                                ${statusText}
                            </span>
                        </div>
                    </div>
                    
                    <div class="meeting-actions">
                        ${this.renderMeetingActions(meeting)}
                    </div>
                </div>
                
                ${meeting.ai_agent_enabled ? `
                    <div class="ai-agent-badge">
                        <svg class="icon-sm" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M3 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clip-rule="evenodd"/>
                        </svg>
                        ИИ-агент активен
                    </div>
                ` : ''}
            </div>
        `;
    }

    renderMeetingActions(meeting) {
        const actions = [];
        
        if (meeting.status === 'scheduled') {
            actions.push(`
                <button class="btn btn-sm btn-primary" onclick="startMeeting(${meeting.id})">
                    <svg class="btn-icon-sm" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" clip-rule="evenodd"/>
                    </svg>
                    Запустить
                </button>
                <button class="btn btn-sm btn-success" onclick="joinMeeting(${meeting.id})">
                    <svg class="btn-icon-sm" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM3 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clip-rule="evenodd"/>
                    </svg>
                    Войти
                </button>
            `);
        }
        
        if (meeting.status === 'active') {
            actions.push(`
                <button class="btn btn-sm btn-success" onclick="joinMeeting(${meeting.id})">
                    <svg class="btn-icon-sm" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM3 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clip-rule="evenodd"/>
                    </svg>
                    Войти
                </button>
                <button class="btn btn-sm btn-outline" onclick="endMeeting(${meeting.id})">
                    <svg class="btn-icon-sm" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8 7a1 1 0 00-1 1v4a1 1 0 001 1h4a1 1 0 001-1V8a1 1 0 00-1-1H8z" clip-rule="evenodd"/>
                    </svg>
                    Завершить
                </button>
            `);
        }
        
        actions.push(`
            <button class="btn btn-sm btn-outline" onclick="viewMeetingDetails(${meeting.id})">
                <svg class="btn-icon-sm" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-11a1 1 0 10-2 0v2H7a1 1 0 100 2h2v2a1 1 0 102 0v-2h2a1 1 0 100-2h-2V7z" clip-rule="evenodd"/>
                </svg>
                Детали
            </button>
        `);
        
        if (meeting.status === 'scheduled') {
            actions.push(`
                <button class="btn btn-sm btn-outline btn-danger" onclick="deleteMeeting(${meeting.id})">
                    <svg class="btn-icon-sm" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/>
                    </svg>
                    Удалить
                </button>
            `);
        }
        
        return actions.join('');
    }

    getStatusClass(status) {
        switch (status) {
            case 'scheduled': return 'status-scheduled';
            case 'active': return 'status-active';
            case 'completed': return 'status-completed';
            default: return 'status-unknown';
        }
    }

    getStatusText(status) {
        switch (status) {
            case 'scheduled': return 'Запланирована';
            case 'active': return 'Активна';
            case 'completed': return 'Завершена';
            default: return 'Неизвестно';
        }
    }

    async handleCreateMeeting(event) {
        event.preventDefault();
        
        const formData = new FormData(event.target);
        
        // Получаем выбранную дату и время из селектов
        const selectedDateTime = this.getSelectedDateTime();
        if (!selectedDateTime) {
            this.showError('Пожалуйста, выберите дату и время');
            return;
        }
        
        // Валидация времени
        if (!this.validateSelectedDateTime(selectedDateTime)) {
            return;
        }
        
        const meetingData = {
            topic: formData.get('topic'),
            start_time: selectedDateTime.toISOString(),
            duration_minutes: parseInt(formData.get('duration_minutes')),
            password: formData.get('password') || null,
            ai_agent_enabled: formData.get('ai_agent_enabled') === 'on'
        };
        
        try {
            const response = await fetch('/api/zoom/meetings/create', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(meetingData)
            });
            
            if (response.ok) {
                const meeting = await response.json();
                this.meetings.unshift(meeting);
                this.renderMeetings();
                this.closeCreateMeetingModal();
                this.showSuccess('Встреча успешно создана!');
                
                // Очищаем форму и переустанавливаем время по умолчанию
                event.target.reset();
                this.setDefaultDateTime();
            } else {
                const error = await response.json();
                this.showError(`Ошибка создания встречи: ${error.detail}`);
            }
        } catch (error) {
            console.error('Error creating meeting:', error);
            this.showError('Ошибка создания встречи');
        }
    }

    async startMeeting(meetingId) {
        try {
            const response = await fetch(`/api/zoom/meetings/${meetingId}/start`, {
                method: 'POST'
            });
            
            if (response.ok) {
                const result = await response.json();
                this.showSuccess('Встреча запущена с ИИ-агентом!');
                this.loadMeetings(); // Обновляем список
            } else {
                const error = await response.json();
                this.showError(`Ошибка запуска встречи: ${error.detail}`);
            }
        } catch (error) {
            console.error('Error starting meeting:', error);
            this.showError('Ошибка запуска встречи');
        }
    }

    async endMeeting(meetingId) {
        if (!confirm('Вы уверены, что хотите завершить встречу? Это создаст отчет.')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/zoom/meetings/${meetingId}/end`, {
                method: 'POST'
            });
            
            if (response.ok) {
                const result = await response.json();
                this.showSuccess('Встреча завершена! Отчет будет готов через несколько минут.');
                this.loadMeetings(); // Обновляем список
            } else {
                const error = await response.json();
                this.showError(`Ошибка завершения встречи: ${error.detail}`);
            }
        } catch (error) {
            console.error('Error ending meeting:', error);
            this.showError('Ошибка завершения встречи');
        }
    }

    async joinMeeting(meetingId) {
        try {
            // Находим встречу в списке
            const meeting = this.meetings.find(m => m.id === meetingId);
            if (!meeting) {
                this.showError('Встреча не найдена');
                this.showError('Встреча не найдена');
                return;
            }

            // Сначала уведомляем бэкенд о входе пользователя
            const response = await fetch(`/api/zoom/meetings/${meetingId}/join`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                const error = await response.json();
                this.showError(`Ошибка входа в встречу: ${error.detail}`);
                return;
            }

            const result = await response.json();
            this.showSuccess(result.message);

            // Если встреча еще не запущена, сначала запускаем её
            if (meeting.status === 'scheduled') {
                await this.startMeeting(meetingId);
                // Обновляем список встреч
                await this.loadMeetings();
            }

            // Открываем ссылку для входа в Zoom
            if (meeting.join_url) {
                window.open(meeting.join_url, '_blank');
                this.showSuccess('Открываю ссылку для входа в Zoom встречу! ИИ-агент подключится через 3 секунды.');
            } else {
                this.showError('Ссылка для входа не найдена');
            }
        } catch (error) {
            console.error('Error joining meeting:', error);
            this.showError('Ошибка входа в встречу');
        }
    }

    async deleteMeeting(meetingId) {
        if (!confirm('Вы уверены, что хотите удалить встречу? Это действие нельзя отменить.')) {
            return;
        }
        
        try {
            const meeting = this.meetings.find(m => m.id === meetingId);
            const response = await fetch(`/api/zoom/meetings/${meetingId}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                this.meetings = this.meetings.filter(m => m.id !== meetingId);
                this.renderMeetings();
                this.showSuccess('Встреча удалена!');
            } else {
                const error = await response.json();
                this.showError(`Ошибка удаления встречи: ${error.detail}`);
            }
        } catch (error) {
            console.error('Error deleting meeting:', error);
            this.showError('Ошибка удаления встречи');
        }
    }

    async viewMeetingDetails(meetingId) {
        try {
            const response = await fetch(`/api/zoom/meetings/${meetingId}`);
            
            if (response.ok) {
                const meetingData = await response.json();
                this.showMeetingDetails(meetingData);
            } else {
                const error = await response.json();
                this.showError(`Ошибка загрузки деталей: ${error.detail}`);
            }
        } catch (error) {
            console.error('Error loading meeting details:', error);
            this.showError('Ошибка загрузки деталей встречи');
        }
    }

    showMeetingDetails(meetingData) {
        const modal = document.getElementById('meeting-details-modal');
        const title = document.getElementById('meeting-details-title');
        const content = document.getElementById('meeting-details-content');
        
        if (!modal || !title || !content) return;
        
        const meeting = meetingData.meeting;
        const transcript = meetingData.transcript;
        
        title.textContent = meeting.topic;
        
        const startTime = new Date(meeting.start_time);
        const formattedTime = startTime.toLocaleString('ru-RU');
        
        let transcriptHtml = '';
        if (transcript) {
            transcriptHtml = `
                <div class="transcript-section">
                    <h4>Транскрипт встречи</h4>
                    <div class="transcript-content">
                        <p><strong>Краткое резюме:</strong></p>
                        <p>${this.escapeHtml(transcript.summary)}</p>
                        
                        <p><strong>Полный транскрипт:</strong></p>
                        <div class="transcript-text">
                            ${this.escapeHtml(transcript.full_transcript)}
                        </div>
                        
                        <div class="transcript-meta">
                            <span>Участников: ${transcript.participants_count}</span>
                            <span>Длительность: ${Math.round(transcript.duration_seconds / 60)} мин</span>
                            <span>Создан: ${new Date(transcript.created_at).toLocaleString('ru-RU')}</span>
                        </div>
                    </div>
                </div>
            `;
        } else {
            transcriptHtml = `
                <div class="transcript-section">
                    <p class="text-muted">Транскрипт будет доступен после завершения встречи</p>
                </div>
            `;
        }
        
        content.innerHTML = `
            <div class="meeting-details">
                <div class="meeting-info-section">
                    <h4>Информация о встрече</h4>
                    <div class="info-grid">
                        <div class="info-item">
                            <label>Тема:</label>
                            <span>${this.escapeHtml(meeting.topic)}</span>
                        </div>
                        <div class="info-item">
                            <label>Время начала:</label>
                            <span>${formattedTime}</span>
                        </div>
                        <div class="info-item">
                            <label>Длительность:</label>
                            <span>${meeting.duration_minutes} минут</span>
                        </div>
                        <div class="info-item">
                            <label>Статус:</label>
                            <span class="status-badge ${this.getStatusClass(meeting.status)}">
                                ${this.getStatusText(meeting.status)}
                            </span>
                        </div>
                        <div class="info-item">
                            <label>ИИ-агент:</label>
                            <span>${meeting.ai_agent_enabled ? 'Включен' : 'Отключен'}</span>
                        </div>
                    </div>
                    
                    ${meeting.status === 'scheduled' ? `
                        <div class="meeting-actions-section">
                            <a href="${meeting.join_url}" target="_blank" class="btn btn-primary">
                                <svg class="btn-icon-sm" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clip-rule="evenodd"/>
                                </svg>
                                Присоединиться к встрече
                            </a>
                        </div>
                    ` : ''}
                </div>
                
                ${transcriptHtml}
            </div>
        `;
        
        modal.style.display = 'block';
    }

    updateMeetingTimes() {
        // Обновляем отображение времени для активных встреч
        this.meetings.forEach(meeting => {
            if (meeting.status === 'active') {
                // Можно добавить логику обновления времени
            }
        });
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    showSuccess(message) {
        // Показываем уведомление об успехе
        this.showNotification(message, 'success');
    }

    showError(message) {
        // Показываем уведомление об ошибке
        this.showNotification(message, 'error');
    }

    showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.innerHTML = `
            <div class="notification-content">
                <span class="notification-message">${message}</span>
                <button class="notification-close" onclick="this.parentElement.parentElement.remove()">&times;</button>
            </div>
        `;
        
        document.body.appendChild(notification);
        
        // Автоматически скрываем через 5 секунд
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 5000);
    }

    closeCreateMeetingModal() {
        const modal = document.getElementById('create-meeting-modal');
        if (modal) {
            modal.style.display = 'none';
            // Восстанавливаем скролл на body
            document.body.style.overflow = 'auto';
        }
    }

    closeMeetingDetailsModal() {
        const modal = document.getElementById('meeting-details-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    }
}

// Глобальные функции для модальных окон
function openCreateMeetingModal() {
    const modal = document.getElementById('create-meeting-modal');
    if (modal) {
        modal.style.display = 'block';
        // Блокируем скролл на body
        document.body.style.overflow = 'hidden';
    }
}

function closeCreateMeetingModal() {
    if (window.zoomDashboard) {
        window.zoomDashboard.closeCreateMeetingModal();
    }
}

function closeMeetingDetailsModal() {
    if (window.zoomDashboard) {
        window.zoomDashboard.closeMeetingDetailsModal();
    } else {
        const modal = document.getElementById('meeting-details-modal');
        if (modal) {
            modal.style.display = 'none';
            // Восстанавливаем скролл на body
            document.body.style.overflow = 'auto';
        }
    }
}

// Закрытие модальных окон при клике вне их
window.addEventListener('click', function(event) {
    const createModal = document.getElementById('create-meeting-modal');
    const detailsModal = document.getElementById('meeting-details-modal');
    
    if (event.target === createModal) {
        closeCreateMeetingModal();
    }
    
    if (event.target === detailsModal) {
        closeMeetingDetailsModal();
    }
});

// Блокируем скролл на body при открытии модального окна
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        const createModal = document.getElementById('create-meeting-modal');
        const detailsModal = document.getElementById('meeting-details-modal');
        
        if (createModal && createModal.style.display === 'block') {
            closeCreateMeetingModal();
        }
        
        if (detailsModal && detailsModal.style.display === 'block') {
            closeMeetingDetailsModal();
        }
    }
});

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    window.zoomDashboard = new ZoomDashboard();
});

// Глобальные функции для вызова из HTML
window.startMeeting = function(meetingId) {
    if (window.zoomDashboard) {
        window.zoomDashboard.startMeeting(meetingId);
    }
};

window.endMeeting = function(meetingId) {
    if (window.zoomDashboard) {
        window.zoomDashboard.endMeeting(meetingId);
    }
};

window.deleteMeeting = function(meetingId) {
    if (window.zoomDashboard) {
        window.zoomDashboard.deleteMeeting(meetingId);
    }
};

window.viewMeetingDetails = function(meetingId) {
    if (window.zoomDashboard) {
        window.zoomDashboard.viewMeetingDetails(meetingId);
    }
};

window.joinMeeting = function(meetingId) {
    if (window.zoomDashboard) {
        window.zoomDashboard.joinMeeting(meetingId);
    }
};
