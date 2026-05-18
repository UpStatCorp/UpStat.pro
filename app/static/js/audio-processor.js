const TARGET_SAMPLE_RATE = 24000;
const CHUNK_DURATION_MS = 50; // 50ms per chunk

class AudioProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        // Реальный sampleRate AudioContext (может быть 44100, 48000 или 24000)
        const inputRate = (options && options.processorOptions && options.processorOptions.sampleRate)
            ? options.processorOptions.sampleRate
            : sampleRate; // глобальная переменная AudioWorkletGlobalScope

        this.inputSampleRate = inputRate;
        this.targetSampleRate = TARGET_SAMPLE_RATE;
        this.needsResampling = Math.abs(inputRate - TARGET_SAMPLE_RATE) > 100;

        // Размер входного буфера (50ms при inputRate)
        this.inputBufferSize = Math.round(inputRate * CHUNK_DURATION_MS / 1000);
        this.inputBuffer = new Float32Array(this.inputBufferSize);
        this.inputBufferIndex = 0;

        // Сообщаем main thread параметры для диагностики
        this.port.postMessage({
            type: 'init',
            inputSampleRate: inputRate,
            targetSampleRate: TARGET_SAMPLE_RATE,
            needsResampling: this.needsResampling,
            inputBufferSize: this.inputBufferSize
        });
    }

    // Простой линейный ресемплинг (достаточно для речи)
    resample(inputData, inputRate, outputRate) {
        if (inputRate === outputRate) return inputData;
        const ratio = inputRate / outputRate;
        const outputLength = Math.round(inputData.length / ratio);
        const output = new Float32Array(outputLength);
        for (let i = 0; i < outputLength; i++) {
            const srcIdx = i * ratio;
            const idx0 = Math.floor(srcIdx);
            const idx1 = Math.min(idx0 + 1, inputData.length - 1);
            const frac = srcIdx - idx0;
            output[i] = inputData[idx0] * (1 - frac) + inputData[idx1] * frac;
        }
        return output;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        
        if (input.length > 0) {
            const inputChannel = input[0];
            
            for (let i = 0; i < inputChannel.length; i++) {
                this.inputBuffer[this.inputBufferIndex] = inputChannel[i];
                this.inputBufferIndex++;
                
                if (this.inputBufferIndex >= this.inputBufferSize) {
                    let samples = this.inputBuffer;

                    // Ресемплируем если нужно (48000 → 24000 и т.п.)
                    if (this.needsResampling) {
                        samples = this.resample(this.inputBuffer, this.inputSampleRate, this.targetSampleRate);
                    }

                    // Convert float32 to int16
                    const int16Buffer = new Int16Array(samples.length);
                    for (let j = 0; j < samples.length; j++) {
                        const sample = Math.max(-1, Math.min(1, samples[j]));
                        int16Buffer[j] = Math.round(sample * 32767);
                    }
                    
                    // Отправляем ресемплированные данные (24kHz) в main thread
                    this.port.postMessage(int16Buffer.buffer);
                    
                    // Reset buffer
                    this.inputBufferIndex = 0;
                }
            }
        }
        
        return true;
    }
}

registerProcessor('audio-processor', AudioProcessor);
