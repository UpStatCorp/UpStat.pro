class AzureVoiceLiveClient {
    constructor() {
        this.ws = null;
        this.audioContext = null;
        this.mediaStream = null;
        this.audioWorkletNode = null;
        this.isConnected = false;
        this.isRecording = false;
        
        // Audio settings
        this.sampleRate = 24000;
        this.audioQueue = [];
        this.isPlaying = false;
        this.audioBufferQueue = [];
        this.nextPlayTime = 0;
        this.currentAudioSource = null;
        this.audioChunks = [];  // Buffer for accumulating audio chunks
        this.isProcessingAudio = false;
        
        // UI elements
        this.statusIndicator = document.getElementById('statusIndicator');
        this.statusText = document.getElementById('statusText');
        this.chatArea = document.getElementById('chatArea');
        this.typingIndicator = document.getElementById('typingIndicator');
        this.startBtn = document.getElementById('startBtn');
        this.stopBtn = document.getElementById('stopBtn');
        
        // Configuration - use values from .env if available, otherwise from input fields
        this.endpoint = document.getElementById('endpoint');
        this.apiKey = document.getElementById('apiKey');
        this.model = document.getElementById('model');
        this.voiceSelect = document.getElementById('voiceSelect');
        
        // Load configuration from window.AZURE_CONFIG (injected by server from .env)
        if (window.AZURE_CONFIG) {
            if (this.endpoint && window.AZURE_CONFIG.endpoint) {
                this.endpoint.value = window.AZURE_CONFIG.endpoint;
            }
            if (this.apiKey && window.AZURE_CONFIG.apiKey) {
                this.apiKey.value = window.AZURE_CONFIG.apiKey;
            }
        }
        
        // Transcript tracking
        this.currentResponse = '';
        this.responseTranscripts = new Map();
        this.completedResponses = new Set();
    this.activeResponseId = null;           // Track current AI response
    this.cancelledResponses = new Set();     // Track cancelled responses
    this.isCancelling = false;               // Flag while cancellation in-flight
    this.scheduledSources = [];              // Track all scheduled buffer sources for interruption
    this.lastBargeInTime = 0;                // Timestamp of last interruption trigger
    this.bargeInCooldownMs = 1200;           // Minimum gap between interruption triggers
    this.pendingUserTranscript = '';          // Accumulate partial user transcript
        
        this.setupEventListeners();
    }

    setupEventListeners() {
        this.startBtn.addEventListener('click', () => this.startChat());
        this.stopBtn.addEventListener('click', () => this.stopChat());
        
        // Enter key to start/stop
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !this.isConnected) {
                this.startChat();
            } else if (e.key === 'Escape' && this.isConnected) {
                this.stopChat();
            }
        });
    }

    updateStatus(status, text) {
        this.statusIndicator.className = `status-indicator ${status}`;
        this.statusText.textContent = text;
    }


    addMessage(sender, message, type = 'normal') {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;
        
        if (type !== 'system') {
            const senderDiv = document.createElement('div');
            senderDiv.className = 'sender';
            senderDiv.textContent = sender;
            messageDiv.appendChild(senderDiv);
        }
        
        const contentDiv = document.createElement('div');
        contentDiv.textContent = message;
        messageDiv.appendChild(contentDiv);
        
        this.chatArea.appendChild(messageDiv);
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    }

    showTyping(show) {
        this.typingIndicator.style.display = show ? 'flex' : 'none';
        if (show) {
            this.chatArea.scrollTop = this.chatArea.scrollHeight;
        }
    }

    async startChat() {
        try {
            this.updateStatus('connecting', 'Connecting...');
            this.startBtn.disabled = true;
            
            // Validate inputs - check both from .env and input fields
            const endpoint = this.endpoint?.value || (window.AZURE_CONFIG?.endpoint);
            const apiKey = this.apiKey?.value || (window.AZURE_CONFIG?.apiKey);
            
            if (!endpoint || !apiKey) {
                throw new Error('Please provide endpoint and API key in .env file or input fields');
            }
            
            // Initialize audio context
            await this.initializeAudio();
            
            // Connect to WebSocket
            await this.connectWebSocket();
            
            // Send session configuration
            this.sendSessionUpdate();
            
            this.updateStatus('connected', 'Connected - Listening...');
            this.stopBtn.disabled = false;
            this.isConnected = true;
            
            this.addMessage('System', 'Voice chat started! Speak into your microphone.', 'system');
            
        } catch (error) {
            this.addMessage('System', `Error: ${error.message}`, 'system');
            this.updateStatus('disconnected', 'Connection failed');
            this.startBtn.disabled = false;
            console.error('Error starting chat:', error);
        }
    }

    async initializeAudio() {
        // Request microphone access
        this.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                sampleRate: this.sampleRate,
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });

        // Create audio context
        this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: this.sampleRate
        });

        // Create audio worklet for processing
        await this.audioContext.audioWorklet.addModule('audio-processor.js');
        
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);
        this.audioWorkletNode = new AudioWorkletNode(this.audioContext, 'audio-processor');
        
        // Handle audio data
        this.audioWorkletNode.port.onmessage = (event) => {
            if (this.isConnected && this.ws && this.ws.readyState === WebSocket.OPEN) {
                const audioData = event.data;
                const base64Audio = this.arrayBufferToBase64(audioData);
                
                this.ws.send(JSON.stringify({
                    type: 'input_audio_buffer.append',
                    audio: base64Audio,
                    event_id: ''
                }));
            }
        };
        
        source.connect(this.audioWorkletNode);
    // Don't route microphone back to speakers to avoid echo & possible suppression
    // this.audioWorkletNode.connect(this.audioContext.destination);
    }

    connectWebSocket() {
        return new Promise((resolve, reject) => {
            const wsUrl = this.buildWebSocketUrl();
            
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log('WebSocket connected');
                resolve();
            };
            
            this.ws.onmessage = (event) => {
                this.handleWebSocketMessage(event.data);
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                reject(new Error('WebSocket connection failed'));
            };
            
            this.ws.onclose = () => {
                console.log('WebSocket disconnected');
                this.handleDisconnection();
            };
        });
    }

    buildWebSocketUrl() {
        // Get endpoint and API key from .env config or input fields
        const endpointValue = this.endpoint?.value || (window.AZURE_CONFIG?.endpoint || '');
        const apiKeyValue = this.apiKey?.value || (window.AZURE_CONFIG?.apiKey || '');
        
        const endpoint = endpointValue.replace('https://', 'wss://').replace(/\/$/, '');
        const apiVersion = '2025-05-01-preview';
        const model = this.model.value;
        const apiKey = apiKeyValue;
        
        return `${endpoint}/voice-live/realtime?api-version=${apiVersion}&model=${model}&api-key=${apiKey}`;
    }

    sendSessionUpdate() {
    const chosenModel = this.model.value;
    const isRealtime = /realtime/i.test(chosenModel) || /mm-realtime/i.test(chosenModel);
    console.log('Configuring session update:', { chosenModel, isRealtime });
    // Choose a transcription model if current model is not itself a transcribe model
    const transcriptionModel = /transcribe/i.test(chosenModel) ? chosenModel : 'gpt-4o-transcribe';
    const sessionUpdate = {
            type: 'session.update',
            session: {
                instructions: 'You are a helpful AI assistant. Respond quickly and concisely in natural, engaging language. Keep responses brief and conversational.',
        modalities: ['audio','text'], // ensure both audio + text modalities
                turn_detection: (() => {
                    // Use slightly more sensitive params for realtime models for quicker barge-in and transcripts
                    const td = isRealtime ? {
                        type: 'azure_semantic_vad',
                        threshold: 0.4,
                        prefix_padding_ms: 300,
                        silence_duration_ms: 300,
                        remove_filler_words: true
                    } : {
                        type: 'azure_semantic_vad',
                        threshold: 0.4,
                        prefix_padding_ms: 300,
                        silence_duration_ms: 300,
                        remove_filler_words: true,
                        end_of_utterance_detection: {
                            model: 'semantic_detection_v1',
                            threshold: 0.006,
                            timeout: 1.2
                        }
                    };
                    return td;
                })(),
                input_audio_noise_reduction: {
                    type: 'azure_deep_noise_suppression'
                },
                input_audio_echo_cancellation: {
                    type: 'server_echo_cancellation'
                },
                voice: {
                    name: (this.voiceSelect && this.voiceSelect.value) ? this.voiceSelect.value : 'en-US-Ava:DragonHDLatestNeural',
                    type: 'azure-standard'
                },
                // Only request explicit transcription block for non-realtime models where it's required/available
                // Always request transcription explicitly so user text shows even for realtime models
                input_audio_transcription: {
                    enabled: true,
                    model: transcriptionModel,
                    format: 'text'
                }
            },
            event_id: ''
        };
        
        this.ws.send(JSON.stringify(sessionUpdate));
    }

    handleWebSocketMessage(data) {
        try {
            const event = JSON.parse(data);
            const eventType = event.type;
            
            console.log('Received event:', eventType);
            
            switch (eventType) {
                case 'session.created':
                    console.log('Session created');
                    break;
                    
                case 'input_audio_buffer.speech_started':
                    const nowTs = Date.now();
                    // Debounce multiple rapid speech_started events
                    if (nowTs - this.lastBargeInTime < this.bargeInCooldownMs) {
                        console.log('🛑 Ignoring speech_started (within cooldown)');
                        break;
                    }
                    // Only treat as interruption if AI audio is currently playing / scheduled or active response present
                    const aiSpeaking = this.scheduledSources.length > 0 || this.currentAudioSource;
                    if (aiSpeaking || (this.activeResponseId && !this.isCancelling)) {
                        this.addMessage('System', '🎤 Speech detected (interrupt)...', 'system');
                        this.interruptForUserSpeech();
                        this.lastBargeInTime = nowTs;
                    } else {
                        console.log('Speech started (no AI audio to interrupt)');
                    }
                    break;
                case 'input_audio_buffer.speech_stopped':
                    console.log('Speech stopped event received');
                    // Do NOT send input_audio_buffer.commit when azure_semantic_vad is active; it's automatic.
                    // If switching to a manual VAD mode in future, add conditional commit here.
                    break;
                    
                case 'response.created':
                    this.showTyping(true);
                    this.currentResponse = '';
                    // Allow currently scheduled audio to finish; only reset chunk accumulator
                    this.audioChunks = [];
                    // Maintain nextPlayTime so new audio appends seamlessly
                    this.isCancelling = false;
                    this.activeResponseId = (event.response && event.response.id) || event.response_id || event.item_id || null;
                    // Clear any leftover cancelled response audio
                    this.stopAllScheduledAudio();
                    break;
                case 'conversation.item.created':
                    // Some backends embed transcript here once ready (especially if no explicit transcription block supplied)
                    try {
                        const item = event.item || event.data || {};
                        if (item.type === 'input_audio' && item.transcript) {
                            console.log('✅ User transcript (item.created):', item.transcript);
                            this.addMessage('👤 You', item.transcript, 'user');
                        }
                        // Realtime models may defer transcript; show placeholder once then update when delta arrives
                        if (item.type === 'input_audio' && !item.transcript && !this._pendingPlaceholderShown) {
                            if (/realtime/i.test(this.model.value)) {
                                this.addMessage('👤 You', '(listening...)', 'user');
                                this._pendingPlaceholderShown = true;
                            }
                        }
                        // Sometimes transcript nested in content array
                        if (item.type === 'input_audio' && Array.isArray(item.content)) {
                            for (const c of item.content) {
                                if (c.transcript) {
                                    console.log('✅ User transcript (item.content):', c.transcript);
                                    this.addMessage('👤 You', c.transcript, 'user');
                                    this._pendingPlaceholderShown = false;
                                    break;
                                }
                            }
                        }
                    } catch(e) {
                        console.warn('conversation.item.created parse issue', e);
                    }
                    break;
                    
                case 'conversation.item.input_audio_transcription.completed':
                    const userTranscript = event.transcript;
                    if (userTranscript) {
                        console.log('✅ User transcription completed:', userTranscript);
                        this.addMessage('👤 You', userTranscript, 'user');
                        this.pendingUserTranscript = '';
                    }
                    break;
                case 'conversation.item.input_audio_transcription.delta':
                case 'input_audio_transcription.delta':
                case 'input_audio_buffer.transcription.delta':
                    // Some variants may send incremental user transcription
                    const partial = event.delta || event.text || event.transcript;
                    if (partial) {
                        this.pendingUserTranscript += partial;
                        console.log('🗣️ User partial transcript acc:', this.pendingUserTranscript);
                    }
                    break;
                case 'conversation.item.input_audio_transcription.failed':
                    console.warn('User transcription failed event:', event);
                    break;
                case 'input_audio_transcription.completed':
                case 'input_audio_buffer.transcription.completed':
                    // Fallback completion names
                    const finalUser = event.transcript || this.pendingUserTranscript;
                    if (finalUser) {
                        console.log('✅ User transcription completed (fallback):', finalUser);
                        this.addMessage('👤 You', finalUser, 'user');
                        this.pendingUserTranscript = '';
                    }
                    break;
                    
                case 'response.audio_transcript.delta':
                    const delta = event.delta;
                    const responseId = event.response_id || event.item_id;
                    
                    if (delta && responseId) {
                        if (!this.responseTranscripts.has(responseId)) {
                            this.responseTranscripts.set(responseId, '');
                        }
                        
                        const updatedTranscript = this.responseTranscripts.get(responseId) + delta;
                        this.responseTranscripts.set(responseId, updatedTranscript);
                        this.currentResponse = updatedTranscript;
                    }
                    break;
                    
                case 'response.audio_transcript.done':
                    const finalResponseId = event.response_id || event.item_id;
                    
                    if (finalResponseId && !this.completedResponses.has(finalResponseId)) {
                        this.completedResponses.add(finalResponseId);
                        if (this.activeResponseId === finalResponseId) {
                            this.activeResponseId = null; // Clear active if done
                        }
                        this.showTyping(false);
                        
                        const finalTranscript = this.responseTranscripts.get(finalResponseId) || this.currentResponse;
                        if (finalTranscript) {
                            this.addMessage('🤖 AI', finalTranscript, 'ai');
                        }
                    }
                    break;
                    
                case 'response.audio.delta':
                    const audioData = event.delta;
                    console.log('🔊🔊🔊 AUDIO DELTA EVENT RECEIVED 🔊🔊🔊');
                    console.log('Audio data exists:', !!audioData);
                    console.log('Audio data length:', audioData ? audioData.length : 'N/A');
                    console.log('Current chunks queue:', this.audioChunks.length);
                    if (this.isCancelling) {
                        console.log('⚠️ Ignoring audio delta during cancellation');
                        break;
                    }
                    if (audioData) {
                        console.log('Adding audio chunk, queue length BEFORE:', this.audioChunks.length);
                        // Buffer the audio chunk instead of playing immediately
                        this.audioChunks.push(audioData);
                        console.log('Adding audio chunk, queue length AFTER:', this.audioChunks.length);
                        this.processAudioBuffer();
                    } else {
                        console.log('❌ No audio data in delta event');
                    }
                    break;
                    
                case 'response.audio.done':
                    console.log('🔊🔊🔊 AUDIO DONE EVENT RECEIVED 🔊🔊🔊');
                    console.log('Processing final chunks, count:', this.audioChunks.length);
                    // Process any remaining audio and mark as complete
                    this.processAudioBuffer(true);
                    break;
                    
                case 'error':
                    const error = event.error || {};
                    let msg = (error.message || error.code || 'Unknown error');
                    this.addMessage('System', `Error: ${msg}`, 'system');
                    console.error('Server error FULL EVENT:', event);
                    break;
            }
            // Fallback: if event contains a transcript that looks like user speech and we haven't displayed it
            if (/input_audio.*transcription/i.test(eventType) && event.transcript && !eventType.endsWith('delta') && !eventType.endsWith('completed')) {
                // Avoid duplicates if already added
                if (event.transcript !== this.pendingUserTranscript) {
                    this.addMessage('👤 You', event.transcript, 'user');
                }
            }
            // Debug: log raw event when it's related to input audio and not already handled
            if (/input_audio/.test(eventType) && !/response\./.test(eventType)) {
                console.debug('RAW INPUT AUDIO EVENT:', event);
            }
            
        } catch (error) {
            console.error('Error parsing WebSocket message:', error);
        }
    }

    processAudioBuffer(isComplete = false) {
        console.log('processAudioBuffer called:', {
            isProcessing: this.isProcessingAudio,
            chunks: this.audioChunks.length,
            isComplete
        });
        
        // Don't process if already processing
        if (this.isProcessingAudio) {
            console.log('Already processing audio, skipping');
            return;
        }

        // If we are cancelling, ignore any buffered audio until new response
        if (this.isCancelling) {
            console.log('Cancellation in progress - skipping buffer processing');
            return;
        }
        
        // Wait for more chunks unless complete or we have many chunks
        const minChunks = isComplete ? 1 : 5;
        if (this.audioChunks.length < minChunks) {
            console.log('Waiting for more chunks, current:', this.audioChunks.length, 'min:', minChunks);
            return;
        }
        
        // Process all accumulated chunks at once for better continuity
        const chunksToProcess = this.audioChunks.splice(0, this.audioChunks.length);
        console.log('Processing batch of', chunksToProcess.length, 'chunks');
        
        if (chunksToProcess.length > 0) {
            this.isProcessingAudio = true;
            this.playAudioChunks(chunksToProcess).then(() => {
                this.isProcessingAudio = false;
                console.log('Batch processed, remaining chunks:', this.audioChunks.length);
                // Process any remaining chunks
                if (this.audioChunks.length > 0) {
                    setTimeout(() => this.processAudioBuffer(false), 10);
                }
            }).catch(error => {
                console.error('Error processing audio batch:', error);
                this.isProcessingAudio = false;
            });
        }
    }

    async playAudioChunks(chunks) {
        try {
            console.log('playAudioChunks called with', chunks.length, 'chunks');
            
            // Combine multiple chunks into one buffer for smoother playback
            let totalLength = 0;
            const pcmDataArrays = [];
            
            // Decode all chunks
            for (const base64Audio of chunks) {
                try {
                    const binaryString = atob(base64Audio);
                    const audioData = new ArrayBuffer(binaryString.length);
                    const audioView = new Uint8Array(audioData);
                    
                    for (let i = 0; i < binaryString.length; i++) {
                        audioView[i] = binaryString.charCodeAt(i);
                    }
                    
                    const pcmData = new Int16Array(audioData);
                    pcmDataArrays.push(pcmData);
                    totalLength += pcmData.length;
                } catch (decodeError) {
                    console.error('Error decoding audio chunk:', decodeError);
                }
            }
            
            if (totalLength === 0) {
                console.log('No valid audio data to play');
                return;
            }
            
            console.log('Total PCM samples:', totalLength);
            
            // Combine all PCM data
            const combinedPcmData = new Int16Array(totalLength);
            let offset = 0;
            for (const pcmData of pcmDataArrays) {
                combinedPcmData.set(pcmData, offset);
                offset += pcmData.length;
            }
            
            // Create audio buffer
            const frameCount = combinedPcmData.length;
            const audioBuffer = this.audioContext.createBuffer(1, frameCount, this.sampleRate);
            const outputData = audioBuffer.getChannelData(0);
            
            // Convert 16-bit PCM to float32
            for (let i = 0; i < frameCount; i++) {
                outputData[i] = combinedPcmData[i] / 32768.0;
            }
            
            console.log('Created audio buffer:', {
                duration: audioBuffer.duration,
                sampleRate: audioBuffer.sampleRate,
                length: audioBuffer.length
            });

            // Amplitude diagnostics
            let min = 1.0, max = -1.0, sumSq = 0;
            for (let i = 0; i < outputData.length; i++) {
                const v = outputData[i];
                if (v < min) min = v;
                if (v > max) max = v;
                sumSq += v * v;
            }
            const rms = Math.sqrt(sumSq / outputData.length);
            console.log('PCM amplitude stats:', { min, max, rms });
            
            // Schedule playback
            this.scheduleAudioPlayback(audioBuffer);
            
        } catch (error) {
            console.error('Error processing audio chunks:', error);
        }
    }

    async scheduleAudioPlayback(audioBuffer) {
        console.log('scheduleAudioPlayback called');
        
        // Ensure audio context is running
        if (this.audioContext.state === 'suspended') {
            console.log('🔊 RESUMING SUSPENDED AUDIO CONTEXT');
            await this.audioContext.resume();
        }
        
    // IMPORTANT: Do NOT stop currently playing audio; we want seamless chaining.
    // Only stop when a brand new response starts (handled elsewhere) or on user stop.
        
        // Check if buffer has meaningful duration (at least 10ms)
        if (audioBuffer.duration < 0.01) {
            console.log('Audio buffer too short, skipping:', audioBuffer.duration);
            return;
        }
        
        const source = this.audioContext.createBufferSource();
        const gainNode = this.audioContext.createGain();
        
        source.buffer = audioBuffer;
    gainNode.gain.value = 1.2; // Slight boost (safe headroom)
    if (gainNode.gain.value > 2.0) gainNode.gain.value = 2.0; // clamp
        
        // Connect: source -> gain -> destination
        source.connect(gainNode);
        gainNode.connect(this.audioContext.destination);
        
        console.log('🔊 AUDIO SETUP:', {
            audioContextState: this.audioContext.state,
            sampleRate: this.audioContext.sampleRate,
            bufferDuration: audioBuffer.duration,
            bufferChannels: audioBuffer.numberOfChannels,
            volume: gainNode.gain.value
        });

        // Detect silence buffer (all zeros or near-zero RMS) and log warning
        const chData = audioBuffer.getChannelData(0);
        let sum = 0; let peak = 0;
        for (let i = 0; i < chData.length; i++) { const v = Math.abs(chData[i]); sum += v*v; if (v>peak) peak=v; }
        const rms = Math.sqrt(sum / chData.length);
        if (peak < 0.001 || rms < 0.0003) {
            console.warn('⚠️ Audio buffer appears near-silent', { peak, rms, length: chData.length });
        }
        
        // Play immediately instead of scheduling in future
        const currentTime = this.audioContext.currentTime;
        const startTime = Math.max(currentTime, this.nextPlayTime);
        
        console.log('Scheduling audio playback:', {
            currentTime,
            startTime,
            nextPlayTime: this.nextPlayTime,
            duration: audioBuffer.duration
        });
        
        console.log('🔊 STARTING AUDIO PLAYBACK AT:', startTime);
        source.start(startTime);
        this.currentAudioSource = source;

    // Track scheduled source for potential interruption
    this.scheduledSources.push({ source, startTime, duration: audioBuffer.duration });
        
        // Test if audio is actually playing
        setTimeout(() => {
            if (this.audioContext.state !== 'running') {
                console.error('❌ Audio context not running after playback start!');
            } else {
                console.log('✅ Audio context is running during playback');
            }
        }, 100);
        
        // Update next play time for seamless playback
        this.nextPlayTime = startTime + audioBuffer.duration;
        
        // Auto-cleanup when finished
        source.onended = () => {
            console.log('Audio playback ended naturally');
            if (this.currentAudioSource === source) {
                this.currentAudioSource = null;
            }
            // Remove from scheduled list
            this.scheduledSources = this.scheduledSources.filter(s => s.source !== source);
        };
        
        // Add error handling
        source.onerror = (error) => {
            console.error('Audio playback error:', error);
        };
    }

    stopCurrentAudio() {
        if (this.currentAudioSource) {
            try {
                this.currentAudioSource.stop();
            } catch (e) {
                // Audio might already be stopped
            }
            this.currentAudioSource = null;
        }
    }

    // Legacy method - keeping for compatibility but not used
    async playAudioDelta(base64Audio) {
        // This method is now replaced by the buffered approach above
        console.warn('playAudioDelta called - this should use the new buffered approach');
    }

    stopChat() {
        this.isConnected = false;
        this.showTyping(false);
        
        // Stop and reset all audio
        this.stopCurrentAudio();
        this.nextPlayTime = 0;
        this.audioBufferQueue = [];
        this.audioChunks = [];
        this.isProcessingAudio = false;
        
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        
        this.updateStatus('disconnected', 'Disconnected');
        this.startBtn.disabled = false;
        this.stopBtn.disabled = true;
        
        this.addMessage('System', 'Voice chat stopped.', 'system');
    }

    interruptForUserSpeech() {
        try {
            // Stop current playback immediately
            this.stopAllScheduledAudio();
            // Clear queued / in-flight audio
            this.audioChunks = [];
            this.isProcessingAudio = false;
            this.nextPlayTime = this.audioContext ? this.audioContext.currentTime : 0;
            // Cancel active response server-side
            if (this.ws && this.ws.readyState === WebSocket.OPEN && this.activeResponseId && !this.completedResponses.has(this.activeResponseId)) {
                console.log('⛔ Sending response.cancel for response', this.activeResponseId);
                const cancelMsg = {
                    type: 'response.cancel',
                    response_id: this.activeResponseId,
                    event_id: ''
                };
                this.ws.send(JSON.stringify(cancelMsg));
                this.cancelledResponses.add(this.activeResponseId);
                this.isCancelling = true;
            }
        } catch (e) {
            console.error('Error during interruption:', e);
        }
    }

    stopAllScheduledAudio() {
        const now = this.audioContext ? this.audioContext.currentTime : 0;
        console.log('🛑 Stopping all scheduled audio sources. Count:', this.scheduledSources.length, 'currentTime:', now);
        for (const entry of this.scheduledSources) {
            try {
                entry.source.stop();
            } catch (e) { /* already stopped */ }
        }
        this.scheduledSources = [];
        this.currentAudioSource = null;
    }

    handleDisconnection() {
        if (this.isConnected) {
            this.stopChat();
            this.addMessage('System', 'Connection lost. Please try again.', 'system');
        }
    }

    // Utility functions
    arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    base64ToArrayBuffer(base64) {
        const binaryString = atob(base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        return bytes.buffer;
    }
}

// Initialize the client when the page loads
document.addEventListener('DOMContentLoaded', () => {
    window.voiceLiveClient = new AzureVoiceLiveClient();
});
