class PcmTap extends AudioWorkletProcessor {
    constructor() {
        super();
        // 2048 samples is ongeveer 128 ms bij 16 kHz: klein genoeg om een
        // woordbegin niet te missen, maar groot genoeg voor weinig berichten.
        this.buffer = new Int16Array(2048);
        this.offset = 0;
    }

    process(inputs) {
        const input = inputs[0]?.[0];
        if (!input) {
            return true;
        }
        for (let index = 0; index < input.length; index++) {
            const sample = Math.max(-1, Math.min(1, input[index]));
            this.buffer[this.offset++] = sample * 0x7fff;
            if (this.offset === this.buffer.length) {
                const buffer = this.buffer;
                this.port.postMessage(buffer.buffer, [buffer.buffer]);
                this.buffer = new Int16Array(2048);
                this.offset = 0;
            }
        }
        return true;
    }
}

registerProcessor("daadit-pcm-tap", PcmTap);
