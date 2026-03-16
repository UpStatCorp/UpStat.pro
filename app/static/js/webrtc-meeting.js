/**
 * WebRTC Meeting Client
 * Обеспечивает подключение к WebRTC встречам с поддержкой AI агента
 */

class WebRTCMeetingClient {
    constructor(meetingId, userId, userName) {
        this.meetingId = meetingId;
        this.userId = userId;
        this.userName = userName;
        
        // WebRTC соединения
        this.websocket = null;
        this.peerConnection = null;
        this.localStream = null;
        this.remoteStreams = new Map();
        
        // Состояние
        this.isConnected = false;
        this.isMuted = false;
        this.isVideoEnabled = true;
        this.aiAgentActive = false;
        this.isListening = false; // Флаг непрерывного прослушивания
        this.audioStream = null; // Поток аудио для прослушивания
        this.mediaRecorder = null; // MediaRecorder для захвата аудио
        // MediaSource для непрерывного стрима (audio/mpeg)
        this.mse = {
            mediaSource: null,
            sourceBuffer: null,
            audioEl: null,
            queue: [],
            open: false,
            pendingEnd: false,
        };
        
        // Очередь для TTS запросов (предотвращает наложение)
        this.ttsQueue = [];
        this.isPlayingTTS = false;
        this.lastProcessedText = null; // Последний обработанный текст (для дедупликации)
        this.activeTTS = new Set(); // Активные тексты, для которых запущен TTS
        
        // WebAudio для воспроизведения аудио чанков с сервера
        this.aiAudioContext = null;
        this.aiAudioChunks = [];
        this.aiAudioStarted = false;
        this.aiAudioDecoding = false;
        this.currentAudioDuration = 0;
        
        // Элементы DOM
        this.localVideoElement = null;
        this.remoteVideosContainer = null;
        this.statusElement = null;
        this.participantsCountElement = null;
        
        // UI элементы
        this.muteButton = null;
        this.videoButton = null;
        this.aiAgentButton = null;
        this.endMeetingButton = null;
        
        this.init();
    }
    
    async init() {
        console.log(`🚀 Инициализация WebRTC клиента для встречи ${this.meetingId}`);
        
        // Получаем DOM элементы
        this.getDOMElements();
        
        // Настраиваем обработчики событий
        this.setupEventListeners();
        
        // Подключаемся к встрече
        await this.joinMeeting();
    }
    
    getDOMElements() {
        this.localVideoElement = document.getElementById('local-video');
        this.remoteVideosContainer = document.getElementById('remote-videos');
        this.statusElement = document.getElementById('meeting-status');
        this.participantsCountElement = document.getElementById('participants-count');
        
        this.muteButton = document.getElementById('mute-audio');
        this.videoButton = document.getElementById('mute-video');
        this.aiAgentButton = document.getElementById('start-ai-agent');
        this.endMeetingButton = document.getElementById('end-meeting');
    }
    
    setupEventListeners() {
        if (this.muteButton) {
            this.muteButton.addEventListener('click', () => this.toggleMute());
        }
        
        if (this.videoButton) {
            this.videoButton.addEventListener('click', () => this.toggleVideo());
        }
        
        if (this.aiAgentButton) {
            this.aiAgentButton.addEventListener('click', () => this.toggleAIAgent());
        }
        
        if (this.endMeetingButton) {
            this.endMeetingButton.addEventListener('click', () => this.endMeeting());
        }
        
        // Обработка закрытия страницы
        window.addEventListener('beforeunload', () => {
            this.leaveMeeting();
        });
    }
    
    async joinMeeting() {
        try {
            this.updateStatus('Подключение к встрече...', 'connecting');
            
            // Получаем медиа потоки
            await this.getUserMedia();
            
            // Подключаемся к WebSocket
            await this.connectWebSocket();
            
            // Настраиваем WebRTC
            this.setupWebRTC();
            
            this.updateStatus('Подключен к встрече', 'connected');
            this.isConnected = true;
            
            console.log('✅ Успешно подключились к встрече');
            
        } catch (error) {
            console.error('❌ Ошибка подключения к встрече:', error);
            this.updateStatus('Ошибка подключения', 'error');
            this.showError('Не удалось подключиться к встрече: ' + error.message);
        }
    }
    
    async getUserMedia() {
        try {
            this.localStream = await navigator.mediaDevices.getUserMedia({
                video: true,
                audio: true
            });
            
            if (this.localVideoElement) {
                this.localVideoElement.srcObject = this.localStream;
            }
            
            console.log('📹 Получены медиа потоки');
            
        } catch (error) {
            console.error('❌ Ошибка получения медиа потоков:', error);
            throw new Error('Не удалось получить доступ к камере и микрофону');
        }
    }
    
    async connectWebSocket() {
        return new Promise((resolve, reject) => {
            const wsUrl = `ws://localhost:8000/api/webrtc/meetings/${this.meetingId}/join?user_id=${this.userId}`;
            this.websocket = new WebSocket(wsUrl);
            
            this.websocket.onopen = () => {
                console.log('🔌 WebSocket соединение установлено');
                resolve();
            };
            
            this.websocket.onmessage = (event) => {
                this.handleWebSocketMessage(JSON.parse(event.data));
            };
            
            this.websocket.onclose = () => {
                console.log('🔌 WebSocket соединение закрыто');
                this.isConnected = false;
                this.updateStatus('Соединение потеряно', 'error');
            };
            
            this.websocket.onerror = (error) => {
                console.error('❌ Ошибка WebSocket:', error);
                reject(error);
            };
        });
    }
    
    setupWebRTC() {
        // Настройка RTCPeerConnection
        this.peerConnection = new RTCPeerConnection({
            iceServers: [
                { urls: 'stun:stun.l.google.com:19302' },
                { urls: 'stun:stun1.l.google.com:19302' }
            ]
        });
        
        // Добавляем локальные потоки
        if (this.localStream) {
            this.localStream.getTracks().forEach(track => {
                this.peerConnection.addTrack(track, this.localStream);
            });
        }
        
        // Обработка входящих потоков
        this.peerConnection.ontrack = (event) => {
            const [remoteStream] = event.streams;
            this.displayRemoteVideo(remoteStream, event.track.id);
        };
        
        // Обработка ICE кандидатов
        this.peerConnection.onicecandidate = (event) => {
            if (event.candidate && this.websocket) {
                this.websocket.send(JSON.stringify({
                    type: 'ice_candidate',
                    candidate: event.candidate
                }));
            }
        };
        
        // Обработка изменения состояния соединения
        this.peerConnection.onconnectionstatechange = () => {
            console.log('🔗 Состояние соединения:', this.peerConnection.connectionState);
        };
    }
    
    handleWebSocketMessage(message) {
        console.log('📨 Получено сообщение:', message);
        
        switch (message.type) {
            case 'connection_established':
                this.handleConnectionEstablished(message);
                break;
            case 'participant_joined':
                this.handleParticipantJoined(message);
                break;
            case 'participant_left':
                this.handleParticipantLeft(message);
                break;
            case 'ai_agent_started':
                this.handleAIAgentStarted(message);
                break;
            case 'ai_agent_response':
                this.handleAIAgentResponse(message);
                break;
            case 'ai_agent_text':
                if (message.text) {
                    const t7 = performance.now();
                    console.log(`[DEBUG] [ai_agent_text] received:`, {
                        text: message.text.substring(0, 50) + '...',
                        timestamp: message.timestamp,
                        clientTime: t7
                    });
                    // ТОЛЬКО показываем текст, не запускаем локальный TTS
                    // Аудио будет приходить через ai_agent_audio_chunk с сервера
                    this.showAIText(message.text);
                }
                break;
            case 'ai_agent_audio_chunk':
                console.log(`[DEBUG] [ai_agent_audio_chunk] received:`, {
                    dataLength: message.audio_data?.length || 0,
                    timestamp: message.timestamp,
                    meetingId: message.meeting_id
                });
                this.handleAIAgentAudioChunk(message);
                break;
            case 'ai_agent_audio_end':
                console.log(`[DEBUG] [ai_agent_audio_end] received:`, {
                    timestamp: message.timestamp,
                    meetingId: message.meeting_id
                });
                this.handleAIAgentAudioEnd(message);
                break;
            case 'ai_agent_interrupted':
                this.handleAIAgentInterrupted(message);
                break;
            case 'audio_data':
                this.handleAudioData(message);
                break;
            case 'chat_message':
                this.handleChatMessage(message);
                break;
            case 'pong':
                // Ответ на ping
                break;
            case 'error':
                this.showError(message.message);
                break;
        }
    }
    
    handleConnectionEstablished(message) {
        console.log('✅ Соединение установлено');
        this.updateParticipantsCount(message.participants_count || 1);
    }
    
    handleParticipantJoined(message) {
        console.log(`👤 Участник ${message.user_id} присоединился`);
        this.updateParticipantsCount(message.participants_count);
        this.showNotification(`Участник присоединился к встрече`);
    }
    
    handleParticipantLeft(message) {
        console.log(`👤 Участник ${message.user_id} покинул встречу`);
        this.updateParticipantsCount(message.participants_count);
        this.showNotification(`Участник покинул встречу`);
    }
    
    handleAIAgentStarted(message) {
        console.log('🤖 AI агент запущен');
        this.aiAgentActive = true;
        console.log('🤖 Обновляем кнопку ИИ-агента...');
        this.updateAIAgentButton();
        console.log('🤖 Показываем ИИ-агента в интерфейсе...');
        this.showAIAgentInInterface();
        // Не показываем кнопку голосового сообщения - используем непрерывное прослушивание
        // this.showVoiceMessageButton();
        console.log('🤖 Показываем уведомление...');
        this.showNotification('AI агент подключен', 'success');
        
        // НЕ запускаем прослушивание здесь - запустится автоматически после получения приветствия
        // Это гарантирует, что микрофон запросится только после того как AI агент готов
        console.log('⏳ Ожидаем приветствие от AI агента, затем запустим прослушивание...');
    }
    
    handleAIAgentInterrupted(message) {
        console.log('🤖 AI агент прерван пользователем');
        this.showNotification('AI агент прерван', 'info');
    }
    
    handleAIAgentResponse(message) {
        console.log('🤖 [DEBUG] [ai_agent_response] received:', {
            hasText: !!message.text,
            hasAudio: !!message.audio_data,
            textPreview: message.text ? message.text.substring(0, 50) + '...' : null,
            audioLength: message.audio_data?.length || 0,
            timestamp: message.timestamp,
            aiAudioStarted: this.aiAudioStarted,
            aiAudioChunks: this.aiAudioChunks?.length || 0,
            aiAudioDecoding: this.aiAudioDecoding
        });
        
        // Если в данный момент идёт стрим чанков — пропускаем полный ответ
        const streamActive = this.aiAudioStarted || 
                            (this.aiAudioChunks && this.aiAudioChunks.length > 0) || 
                            this.aiAudioDecoding;
        
        if (streamActive) {
            console.log('⚠️ [DUPLICATE_FILTER] Пропускаем готовое аудио: стрим чанков активен', {
                aiAudioStarted: this.aiAudioStarted,
                chunksCount: this.aiAudioChunks?.length || 0,
                isDecoding: this.aiAudioDecoding
            });
            // Показываем текст, если есть, но не воспроизводим аудио
            if (message.text) {
                this.showAIText(message.text);
            }
            return;
        }
        
        // Иначе — это fallback случай (стрим не работал) или приветствие
        if (message.text) {
            this.showAIText(message.text);
        }
        
        if (message.audio_data) {
            console.log('⚠️ [DEBUG] Playing fallback audio_data (no active stream)');
            this.playAIAudio(message.audio_data);
        }
        
        // ВАЖНО: После получения приветствия запускаем прослушивание НЕМЕДЛЕННО
        // Проверяем, это приветствие (содержит "Привет" или "тренер")
        const text = message.text || '';
        const isGreeting = text.includes('Привет') || text.includes('тренер') || text.includes('ИИ-тренер');
        console.log(`🎤 Проверка приветствия: text="${text.substring(0, 50)}...", isGreeting=${isGreeting}, isListening=${this.isListening}`);
        
        if (isGreeting && !this.isListening) {
            console.log('🎤 Получено приветствие, запускаем прослушивание сразу...');
            // Запускаем прослушивание сразу, не ждем окончания воспроизведения
            // Это позволит начать слушать пока приветствие еще играет
            // Небольшая задержка 500ms чтобы микрофон успел инициализироваться
            setTimeout(() => {
                if (!this.isListening) {
                    console.log('🎤 Запускаем прослушивание после приветствия');
                    this.startContinuousListening().catch(error => {
                        console.error('❌ Ошибка запуска прослушивания:', error);
                        this.showNotification(`Ошибка запуска прослушивания: ${error.message}`, 'error');
                    });
                } else {
                    console.log('⚠️ Прослушивание уже активно, пропускаем запуск');
                }
            }, 500); // 500ms на инициализацию микрофона
        } else if (isGreeting && this.isListening) {
            console.log('✅ Приветствие получено, прослушивание уже активно');
        }
        // НЕ запускаем локальный TTS - аудио идет с сервера через ai_agent_audio_chunk
    }
    
    async processTTSQueue() {
        // Если уже воспроизводится или очередь пуста - ничего не делаем
        if (this.isPlayingTTS || this.ttsQueue.length === 0) {
            return;
        }
        
        this.isPlayingTTS = true;
        
        while (this.ttsQueue.length > 0) {
            const item = this.ttsQueue.shift();
            try {
                // Обновляем lastProcessedText перед обработкой
                this.lastProcessedText = item.text;
                await this.playStreamViaProxy(item.text, item.t7);
            } catch (e) {
                console.error('Ошибка при воспроизведении TTS:', e);
            } finally {
                // Удаляем текст из активных TTS после завершения (успешно или с ошибкой)
                this.activeTTS.delete(item.text);
            }
        }
        
        this.isPlayingTTS = false;
    }

    async playStreamViaProxy(text, t7Start = null) {
        try {
            const t7 = t7Start || performance.now();
            console.log(`[LATENCY] t7: TTS proxy fetch started at ${t7}ms`);
            
            const resp = await fetch('/api/tts-proxy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });
            
            if (!resp.ok) {
                console.error(`TTS proxy error: ${resp.status} ${resp.statusText}`);
                throw new Error(`TTS proxy error: ${resp.status}`);
            }
            
            if (!resp.body) {
                console.error('TTS proxy: no response body');
                return;
            }
            
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            await audioCtx.resume();
            const reader = resp.body.getReader();
            
            const t7_received = performance.now();
            console.log(`[LATENCY] t7_received: first byte received at ${t7_received}ms, delta: ${(t7_received-t7).toFixed(3)}ms`);

            const concat = (list) => {
                let total = list.reduce((s, a) => s + a.length, 0);
                const out = new Uint8Array(total);
                let off = 0; for (const a of list) { out.set(a, off); off += a.length; }
                return out;
            };

            let t7Ref = t7Start || t7;
            const decodeAndPlay = async (ab, isFirst = false) => {
                try {
                    const t8_start = performance.now();
                    const buf = await audioCtx.decodeAudioData(ab.slice(0));
                    const src = audioCtx.createBufferSource();
                    src.buffer = buf; src.connect(audioCtx.destination); src.start();
                    
                    const t8 = performance.now();
                    if (isFirst) {
                        console.log(`[LATENCY] t8: audio playback started at ${t8}ms, decode took: ${(t8-t8_start).toFixed(3)}ms`);
                        if (t7Ref) {
                            const totalLatency = t8 - t7Ref;
                            console.log(`[LATENCY] Total: t7→t8 = ${totalLatency.toFixed(3)}ms (from browser receive to play)`);
                        }
                    }
                } catch (e) {
                    // может не хватать фреймов — дождёмся ещё
                    console.warn('decodeAndPlay failed:', e);
                }
            };

            // Стартовый буфер для декодирования MP3 (нужно достаточно данных для полных MP3 фреймов)
            const INITIAL = 50000; // ~50KB - увеличено для надежного декодирования MP3
            let init = [], ibytes = 0;
            let firstChunk = true;
            let attempts = 0;
            const maxAttempts = 100; // Максимум попыток чтения
            
            while (ibytes < INITIAL && attempts < maxAttempts) {
                const { done, value } = await reader.read();
                if (done) break;
                if (value) {
                    init.push(value);
                    ibytes += value.length;
                }
                attempts++;
            }
            
            if (ibytes > 0) {
                try {
                    await decodeAndPlay(concat(init).buffer, firstChunk);
                    firstChunk = false;
                } catch (e) {
                    console.warn('First chunk decode failed, continuing with more data:', e);
                    // Продолжаем чтение, возможно нужно больше данных
                }
            }

            // Остальные куски
            const GROUP = 20000; // ~20KB
            let group = [], gbytes = 0;
            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    if (gbytes > 0) await decodeAndPlay(concat(group).buffer, false);
                    break;
                }
                group.push(value); gbytes += value.length;
                if (gbytes >= GROUP) {
                    await decodeAndPlay(concat(group).buffer, false);
                    group = []; gbytes = 0;
                }
            }
        } catch (e) {
            console.error('Ошибка playStreamViaProxy:', e);
        }
    }

    async handleAIAgentAudioChunk(message) {
        try {
            const base64 = message.audio_data;
            if (!base64) {
                console.warn('[DEBUG] handleAIAgentAudioChunk: no audio_data');
                return;
            }
            
            console.log('[DEBUG] handleAIAgentAudioChunk: processing chunk', {
                chunkSize: base64.length,
                chunksInBuffer: this.aiAudioChunks?.length || 0,
                isPlaying: this.aiAudioStarted,
                isDecoding: this.aiAudioDecoding
            });
            
            // Декодируем base64 в ArrayBuffer
            const binaryString = atob(base64);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            
            // Инициализируем WebAudio контекст один раз
            if (!this.aiAudioContext) {
                console.log('[DEBUG] Initializing AI AudioContext');
                this.aiAudioContext = new (window.AudioContext || window.webkitAudioContext)();
                await this.aiAudioContext.resume();
            }
            
            // Инициализируем очередь чанков для текущего стрима
            if (!this.aiAudioChunks) {
                this.aiAudioChunks = [];
                this.aiAudioStarted = false;
            }
            
            // Добавляем чанк в буфер
            this.aiAudioChunks.push(bytes.buffer);
            
            // Пытаемся декодировать и воспроизвести накопленные чанки
            // Для первого воспроизведения уменьшаем размер буфера (10KB) для минимальной задержки
            // Для последующих используем больший размер (20KB) для эффективности
            const totalSize = this.aiAudioChunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
            const minSize = this.aiAudioStarted ? 20000 : 10000; // 10KB для первого, 20KB для последующих
            if (totalSize >= minSize && !this.aiAudioDecoding) {
                this.aiAudioDecoding = true;
                
                console.log(`[DEBUG] Decoding audio: ${totalSize} bytes (${this.aiAudioChunks.length} chunks), isFirstPlay: ${!this.aiAudioStarted}`);
                
                try {
                    // Объединяем все чанки
                    const combinedBuffer = new ArrayBuffer(totalSize);
                    const combinedView = new Uint8Array(combinedBuffer);
                    let offset = 0;
                    for (const chunk of this.aiAudioChunks) {
                        combinedView.set(new Uint8Array(chunk), offset);
                        offset += chunk.byteLength;
                    }
                    
                    // Декодируем и воспроизводим
                    const audioBuffer = await this.aiAudioContext.decodeAudioData(combinedBuffer.slice(0));
                    const source = this.aiAudioContext.createBufferSource();
                    source.buffer = audioBuffer;
                    source.connect(this.aiAudioContext.destination);
                    
                    if (!this.aiAudioStarted) {
                        const t8 = performance.now();
                        source.start();
                        this.aiAudioStarted = true;
                        console.log(`[LATENCY] t8: AI audio playback started from server chunks at ${t8}ms`);
                    } else {
                        // Если уже воспроизводится - ждем окончания предыдущего
                        source.start(this.aiAudioContext.currentTime + (this.currentAudioDuration || 0));
                        if (this.currentAudioDuration) {
                            this.currentAudioDuration += audioBuffer.duration;
                        } else {
                            this.currentAudioDuration = audioBuffer.duration;
                        }
                    }
                    
                    // Очищаем буфер после успешного декодирования
                    this.aiAudioChunks = [];
                    this.aiAudioDecoding = false;
                } catch (e) {
                    console.warn('Audio decode failed, waiting for more chunks:', e);
                    // Не очищаем буфер, продолжаем накапливать
                    this.aiAudioDecoding = false;
                }
            }
        } catch (e) {
            console.error('❌ Ошибка обработки аудио чанка:', e);
            this.aiAudioDecoding = false;
        }
    }
    
    handleAIAgentAudioEnd(message) {
        console.log('[DEBUG] handleAIAgentAudioEnd called:', {
            chunksRemaining: this.aiAudioChunks?.length || 0,
            isPlaying: this.aiAudioStarted,
            isDecoding: this.aiAudioDecoding
        });
        
        // При окончании стрима воспроизводим оставшиеся чанки
        if (this.aiAudioChunks && this.aiAudioChunks.length > 0) {
            const totalSize = this.aiAudioChunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
            console.log(`[DEBUG] Playing remaining ${totalSize} bytes of audio chunks`);
            
            if (totalSize > 0 && this.aiAudioContext) {
                try {
                    const combinedBuffer = new ArrayBuffer(totalSize);
                    const combinedView = new Uint8Array(combinedBuffer);
                    let offset = 0;
                    for (const chunk of this.aiAudioChunks) {
                        combinedView.set(new Uint8Array(chunk), offset);
                        offset += chunk.byteLength;
                    }
                    
                    this.aiAudioContext.decodeAudioData(combinedBuffer.slice(0)).then(audioBuffer => {
                        const source = this.aiAudioContext.createBufferSource();
                        source.buffer = audioBuffer;
                        source.connect(this.aiAudioContext.destination);
                        
                        // Планируем воспроизведение после текущего
                        const startTime = this.aiAudioContext.currentTime + (this.currentAudioDuration || 0);
                        source.start(startTime);
                        console.log('[DEBUG] Final audio chunks scheduled to play at', startTime);
                        
                        if (this.currentAudioDuration) {
                            this.currentAudioDuration += audioBuffer.duration;
                        } else {
                            this.currentAudioDuration = audioBuffer.duration;
                        }
                    }).catch(e => {
                        console.error('[DEBUG] Failed to decode final audio chunks:', e);
                    });
                } catch (e) {
                    console.error('[DEBUG] Failed to prepare final audio chunks:', e);
                }
            }
        }
        
        // Сбрасываем состояние для следующего стрима через небольшую задержку
        // чтобы дать время завершиться воспроизведению и защитить от дубликатов
        const wasPlaying = this.aiAudioStarted;
        
        // Проиграли все чанки — через safety timeout снимем флаги
        setTimeout(() => {
            this.aiAudioChunks = [];
            this.aiAudioStarted = false;
            this.aiAudioDecoding = false;
            // Сбрасываем duration только если ничего не воспроизводилось
            if (!wasPlaying && !this.aiAudioContext?.currentTime) {
                this.currentAudioDuration = 0;
            }
            console.log('[DEBUG] ✅ AI audio stream ended, state reset after timeout');
        }, 200); // 200ms — чтобы дать время onended сработать
        
        console.log('[DEBUG] ✅ AI audio stream ended, state will reset in 200ms');
    }

    mseAppend(bytes) {
        if (!this.mse.sourceBuffer || !this.mse.open) {
            this.mse.queue.push(bytes);
            return;
        }
        if (this.mse.sourceBuffer.updating) {
            this.mse.queue.push(bytes);
            return;
        }
        try {
            this.mse.sourceBuffer.appendBuffer(bytes);
        } catch (e) {
            console.error('❌ MSE appendBuffer error:', e);
        }
    }

    mseFlush() {
        if (!this.mse.sourceBuffer || !this.mse.open) return;
        if (this.mse.sourceBuffer.updating) return;
        if (this.mse.queue.length > 0) {
            const next = this.mse.queue.shift();
            try { this.mse.sourceBuffer.appendBuffer(next); } catch (e) { console.error('❌ MSE flush error:', e); }
        } else if (this.mse.pendingEnd) {
            try { this.mse.mediaSource.endOfStream(); } catch(e) {}
            // подготовка к следующей реплике
            setTimeout(()=>{
                if (this.mse.audioEl) this.mse.audioEl.remove();
                this.mse = { mediaSource:null, sourceBuffer:null, audioEl:null, queue:[], open:false, pendingEnd:false };
            }, 500);
        }
    }

    
    handleAudioData(message) {
        // Обработка аудио данных от других участников
        // В реальной реализации здесь будет обработка аудио потоков
        console.log('🎵 Получены аудио данные');
    }
    
    async startContinuousListening() {
        try {
            console.log('🎤 [START] Запуск непрерывного прослушивания (ChatGPT Voice режим)...');
            console.log('🎤 [START] Состояние:', {
                isListening: this.isListening,
                hasAudioStream: !!this.audioStream,
                audioStreamActive: this.audioStream?.active,
                hasMediaRecorder: !!this.mediaRecorder,
                mediaRecorderState: this.mediaRecorder?.state,
                hasWebSocket: !!this.websocket,
                websocketState: this.websocket?.readyState
            });
            
            // Проверяем поддержку getUserMedia
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                const error = 'getUserMedia не поддерживается в этом браузере';
                console.error('❌ [START]', error);
                throw new Error(error);
            }
            
            // Используем существующий поток или получаем новый для аудио
            if (!this.audioStream || !this.audioStream.active) {
                console.log('🎤 Запрашиваем доступ к микрофону...');
                
                this.audioStream = await navigator.mediaDevices.getUserMedia({ 
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                        sampleRate: 16000,
                        channelCount: 1
                    } 
                }).catch(error => {
                    console.error('❌ Ошибка getUserMedia:', error);
                    throw error;
                });
            }
            
            console.log('🎤 Микрофон получен, создаем MediaRecorder...');
            
            // Проверяем поддержку MediaRecorder
            if (!window.MediaRecorder) {
                throw new Error('MediaRecorder не поддерживается в этом браузере');
            }
            
            // Останавливаем предыдущий recorder, если есть
            if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                this.mediaRecorder.stop();
            }
            
            // Создаем MediaRecorder для непрерывной записи с оптимальными настройками
            this.mediaRecorder = new MediaRecorder(this.audioStream, {
                mimeType: 'audio/webm;codecs=opus',
                audioBitsPerSecond: 16000 // Низкий битрейт для быстрой передачи
            });
            
            console.log('🎤 MediaRecorder создан, настраиваем обработчики...');
            
            // Обработчик данных аудио
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    console.log(`🎤 [DATA] Получены аудио данные от MediaRecorder: ${event.data.size} байт, type: ${event.data.type}`);
                    this.sendAudioData(event.data);
                } else {
                    console.warn('⚠️ [DATA] MediaRecorder вернул пустые данные');
                }
            };
            
            // Обработчик ошибок MediaRecorder
            this.mediaRecorder.onerror = (event) => {
                console.error('❌ Ошибка MediaRecorder:', event.error);
                this.hideListeningIndicator();
            };
            
            // Обработчик остановки (автоматический перезапуск при необходимости)
            this.mediaRecorder.onstop = () => {
                if (this.isListening) {
                    console.log('🔄 MediaRecorder остановлен, перезапускаем...');
                    setTimeout(() => {
                        if (this.isListening && this.audioStream && this.audioStream.active) {
                            this.mediaRecorder.start(100);
                        }
                    }, 100);
                }
            };
            
            // Запускаем с интервалом 100ms для минимальной задержки (как в ChatGPT Voice)
            console.log('🎤 [START] Запускаем MediaRecorder с интервалом 100ms...');
            this.mediaRecorder.start(100);
            this.isListening = true;
            
            console.log('🎤 [START] MediaRecorder состояние:', this.mediaRecorder.state);
            
            // Показываем индикатор прослушивания
            this.showListeningIndicator();
            
            console.log('✅ [START] Непрерывное прослушивание запущено успешно (интервал: 100ms)');
            console.log('✅ [START] Состояние после запуска:', {
                isListening: this.isListening,
                mediaRecorderState: this.mediaRecorder.state,
                audioStreamActive: this.audioStream?.active
            });
            
        } catch (error) {
            console.error('❌ Ошибка запуска прослушивания:', error);
            this.showNotification(`Ошибка доступа к микрофону: ${error.message}`, 'error');
            this.hideListeningIndicator();
            this.isListening = false;
        }
    }
    
    showListeningIndicator() {
        const indicator = document.getElementById('listening-indicator');
        if (indicator) {
            indicator.style.display = 'flex';
            console.log('✅ Индикатор прослушивания показан');
        }
    }
    
    hideListeningIndicator() {
        const indicator = document.getElementById('listening-indicator');
        if (indicator) {
            indicator.style.display = 'none';
            console.log('✅ Индикатор прослушивания скрыт');
        }
    }
    
    // Старый метод для обратной совместимости
    async startRealtimeAudioCapture() {
        return this.startContinuousListening();
    }
    
    async sendAudioData(audioBlob) {
        try {
            if (!this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
                // Тихая ошибка - просто пропускаем при отсутствии соединения
                return;
            }
            
            // Конвертируем аудио в base64 быстро (FileReader быстрее для больших Blob)
            const reader = new FileReader();
            reader.onloadend = () => {
                try {
                    const base64 = reader.result.split(',')[1]; // Убираем data:audio/webm;base64,
                    
                    // Отправляем с минимальной задержкой на сервер
                    const message = {
                        type: 'audio_data',
                        meeting_id: this.meetingId,
                        audio_data: base64,
                        audio_type: audioBlob.type || 'audio/webm;codecs=opus',
                        timestamp: performance.now() / 1000.0
                    };
                    
                    this.websocket.send(JSON.stringify(message));
                    console.log(`🎤 Отправлен audio_data чанк: ${audioBlob.size} байт, timestamp: ${message.timestamp.toFixed(3)}`);
                } catch (sendError) {
                    console.error('❌ Ошибка отправки аудио данных:', sendError);
                }
            };
            
            reader.onerror = (error) => {
                console.error('❌ Ошибка чтения аудио Blob:', error);
            };
            
            reader.readAsDataURL(audioBlob);
            
        } catch (error) {
            console.error('❌ Ошибка обработки аудио:', error);
        }
    }
    
    stopAudioCapture() {
        // Останавливаем прослушивание
        this.isListening = false;
        
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
        }
        
        if (this.audioStream) {
            this.audioStream.getTracks().forEach(track => track.stop());
            this.audioStream = null;
        }
        
        // Скрываем индикатор прослушивания
        this.hideListeningIndicator();
        
        console.log('🎤 Аудио захват и прослушивание остановлены');
    }
    
    handleChatMessage(message) {
        // Обработка текстовых сообщений в чате
        console.log(`💬 ${message.user_id}: ${message.message}`);
    }
    
    displayRemoteVideo(stream, trackId) {
        const videoElement = document.createElement('video');
        videoElement.srcObject = stream;
        videoElement.autoplay = true;
        videoElement.muted = false;
        videoElement.className = 'remote-video';
        
        const container = document.createElement('div');
        container.className = 'remote-video-container';
        container.appendChild(videoElement);
        
        const label = document.createElement('div');
        label.className = 'video-label';
        label.textContent = 'Участник';
        container.appendChild(label);
        
        this.remoteVideosContainer.appendChild(container);
        this.remoteStreams.set(trackId, container);
        
        console.log('📹 Добавлено удаленное видео');
    }
    
    async toggleMute() {
        if (!this.localStream) return;
        
        const audioTrack = this.localStream.getAudioTracks()[0];
        if (audioTrack) {
            this.isMuted = !this.isMuted;
            audioTrack.enabled = !this.isMuted;
            
            this.updateMuteButton();
            console.log(this.isMuted ? '🔇 Микрофон отключен' : '🎤 Микрофон включен');
        }
    }
    
    async toggleVideo() {
        if (!this.localStream) return;
        
        const videoTrack = this.localStream.getVideoTracks()[0];
        if (videoTrack) {
            this.isVideoEnabled = !this.isVideoEnabled;
            videoTrack.enabled = this.isVideoEnabled;
            
            this.updateVideoButton();
            console.log(this.isVideoEnabled ? '📹 Видео включено' : '📹 Видео отключено');
        }
    }
    
    async toggleAIAgent() {
        if (this.aiAgentActive) {
            // AI агент уже активен, можно добавить функцию остановки
            console.log('🤖 AI агент уже активен');
            return;
        }
        
        try {
            const response = await fetch(`/api/webrtc/meetings/${this.meetingId}/start-ai-agent`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
            
            if (response.ok) {
                const result = await response.json();
                console.log('🤖 AI агент запущен:', result);
                this.showNotification('AI агент запускается...');
            } else {
                let detail = '';
                try { const err = await response.json(); detail = err.detail || JSON.stringify(err); } catch(e) { detail = await response.text(); }
                throw new Error(`Не удалось запустить ИИ агента: ${detail}`);
            }
            
        } catch (error) {
            console.error('❌ Ошибка запуска AI агента:', error);
            this.showError('Не удалось запустить AI агента: ' + error.message);
        }
    }
    
    async endMeeting() {
        if (confirm('Вы уверены, что хотите завершить встречу?')) {
            try {
                const response = await fetch(`/api/webrtc/meetings/${this.meetingId}/end`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    }
                });
                
                if (response.ok) {
                    this.leaveMeeting();
                    window.location.href = '/dashboard';
                } else {
                    throw new Error('Не удалось завершить встречу');
                }
                
            } catch (error) {
                console.error('❌ Ошибка завершения встречи:', error);
                this.showError('Не удалось завершить встречу: ' + error.message);
            }
        }
    }
    
    async leaveMeeting() {
        if (this.websocket) {
            this.websocket.close();
        }
        
        if (this.localStream) {
            this.localStream.getTracks().forEach(track => track.stop());
        }
        
        if (this.peerConnection) {
            this.peerConnection.close();
        }
        
        console.log('👋 Покинули встречу');
    }
    
    // UI обновления
    updateStatus(text, className) {
        if (this.statusElement) {
            this.statusElement.textContent = text;
            this.statusElement.className = `status ${className}`;
        }
    }
    
    updateParticipantsCount(count) {
        if (this.participantsCountElement) {
            this.participantsCountElement.textContent = count;
        }
    }
    
    updateMuteButton() {
        if (this.muteButton) {
            this.muteButton.textContent = this.isMuted ? '🎤' : '🔇';
            this.muteButton.title = this.isMuted ? 'Включить микрофон' : 'Отключить микрофон';
        }
    }
    
    updateVideoButton() {
        if (this.videoButton) {
            this.videoButton.textContent = this.isVideoEnabled ? '📹' : '📷';
            this.videoButton.title = this.isVideoEnabled ? 'Отключить видео' : 'Включить видео';
        }
    }
    
    updateAIAgentButton() {
        if (this.aiAgentButton) {
            if (this.aiAgentActive) {
                this.aiAgentButton.textContent = '🤖 AI активен';
                this.aiAgentButton.disabled = true;
                this.aiAgentButton.className = 'control-btn ai-btn active';
            } else {
                this.aiAgentButton.textContent = '🤖 Запустить ИИ';
                this.aiAgentButton.disabled = false;
                this.aiAgentButton.className = 'control-btn ai-btn';
                // Скрываем ИИ-агента из интерфейса, если он не активен
                this.hideAIAgentFromInterface();
                // Скрываем кнопку голосового сообщения
                this.hideVoiceMessageButton();
                // Останавливаем аудио захват
                this.stopAudioCapture();
            }
        }
    }
    
    playAIAudio(audioData) {
        try {
            const bytes = Uint8Array.from(atob(audioData), c => c.charCodeAt(0));
            // Определяем формат: ID3 -> mp3; 'ftyp' возле начала -> mp4; RIFF -> wav
            let mime = 'audio/wav';
            if (bytes.length >= 3 && bytes[0] === 0x49 && bytes[1] === 0x44 && bytes[2] === 0x33) {
                mime = 'audio/mpeg';
            } else if (bytes.length >= 12 && bytes[4] === 0x66 && bytes[5] === 0x74 && bytes[6] === 0x79 && bytes[7] === 0x70) {
                mime = 'audio/mp4';
            } else if (bytes.length >= 4 && bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46) {
                mime = 'audio/wav';
            }
            const audioBlob = new Blob([bytes], { type: mime });
            const audioUrl = URL.createObjectURL(audioBlob);
            const audio = new Audio(audioUrl);
            audio.onended = () => URL.revokeObjectURL(audioUrl);
            audio.onerror = () => URL.revokeObjectURL(audioUrl);
            const p = audio.play();
            if (p && typeof p.catch === 'function') {
                p.catch(() => {
                    const once = () => {
                        document.removeEventListener('click', once);
                        audio.play().catch(() => {});
                    };
                    document.addEventListener('click', once, { once: true });
            });
            }
        } catch (error) {
            console.error('❌ Ошибка обработки аудио данных:', error);
        }
    }
    
    showAIText(text) {
        // Показываем текст от AI агента
        const notification = document.createElement('div');
        notification.className = 'ai-message';
        notification.textContent = `AI: ${text}`;
        
        // Добавляем в чат или показываем как уведомление
        this.showNotification(`AI: ${text}`);
    }
    
    showAIAgentInInterface() {
        // Показываем ИИ-агента в интерфейсе как участника
        const aiAgentVideo = document.getElementById('ai-agent-video');
        if (aiAgentVideo) {
            aiAgentVideo.style.display = 'block';
            
            // Добавляем анимацию появления
            aiAgentVideo.style.opacity = '0';
            aiAgentVideo.style.transform = 'scale(0.8)';
            
            setTimeout(() => {
                aiAgentVideo.style.transition = 'all 0.5s ease';
                aiAgentVideo.style.opacity = '1';
                aiAgentVideo.style.transform = 'scale(1)';
            }, 100);
            
            console.log('🤖 ИИ-агент отображен в интерфейсе');
        }
        
        // Обновляем счетчик участников
        this.updateParticipantsCount(this.getParticipantsCount() + 1);
    }
    
    hideAIAgentFromInterface() {
        // Скрываем ИИ-агента из интерфейса
        const aiAgentVideo = document.getElementById('ai-agent-video');
        if (aiAgentVideo) {
            aiAgentVideo.style.transition = 'all 0.5s ease';
            aiAgentVideo.style.opacity = '0';
            aiAgentVideo.style.transform = 'scale(0.8)';
            
            setTimeout(() => {
                aiAgentVideo.style.display = 'none';
            }, 500);
            
            console.log('🤖 ИИ-агент скрыт из интерфейса');
        }
        
        // Обновляем счетчик участников
        this.updateParticipantsCount(this.getParticipantsCount() - 1);
    }
    
    showVoiceMessageButton() {
        const voiceButton = document.getElementById('voice-message-btn');
        if (voiceButton) {
            voiceButton.style.display = 'flex';
            console.log('🎤 Кнопка голосового сообщения показана');
        }
    }
    
    hideVoiceMessageButton() {
        const voiceButton = document.getElementById('voice-message-btn');
        if (voiceButton) {
            voiceButton.style.display = 'none';
            console.log('🎤 Кнопка голосового сообщения скрыта');
        }
    }
    
    getParticipantsCount() {
        // Получаем текущее количество участников
        const countElement = document.getElementById('participants-count');
        if (countElement) {
            return parseInt(countElement.textContent) || 1;
        }
        return 1;
    }
    
    showNotification(message) {
        // Создаем уведомление
        const notification = document.createElement('div');
        notification.className = 'notification';
        notification.textContent = message;
        
        document.body.appendChild(notification);
        
        // Удаляем через 3 секунды
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 3000);
    }
    
    showError(message) {
        // Показываем ошибку
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error-message';
        errorDiv.textContent = message;
        
        document.body.appendChild(errorDiv);
        
        // Удаляем через 5 секунд
        setTimeout(() => {
            if (errorDiv.parentNode) {
                errorDiv.parentNode.removeChild(errorDiv);
            }
        }, 5000);
    }
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    // Получаем параметры из URL
    const pathParts = window.location.pathname.split('/');
    const meetingId = pathParts[pathParts.length - 2]; // Берем предпоследнюю часть, так как URL заканчивается на /room
    
    // Получаем данные пользователя (в реальном приложении из сессии)
    const userId = getCurrentUserId();
    const userName = getCurrentUserName();
    
    if (meetingId && userId) {
        console.log(`🚀 Запуск WebRTC клиента для встречи ${meetingId}`);
        window.meetingClient = new WebRTCMeetingClient(meetingId, userId, userName);
    } else {
        console.error('❌ Не удалось получить ID встречи или пользователя');
        document.body.innerHTML = '<div class="error">Ошибка: неверные параметры встречи</div>';
    }
});

// Вспомогательные функции (в реальном приложении будут получать данные из сессии)
function getCurrentUserId() {
    // Временная заглушка - в реальном приложении получать из сессии
    return 1;
}

function getCurrentUserName() {
    // Временная заглушка - в реальном приложении получать из сессии
    return 'Пользователь';
}
