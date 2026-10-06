/** @odoo-module **/

/**
 * Turn a recorded blob into 16 kHz mono PCM16 WAV.
 *
 * Wispr Flow only accepts that exact shape (base64, 16 kHz, PCM16), and
 * ElevenLabs Scribe accepts it too — so converting here gives both
 * providers one identical audio path instead of a per-provider branch.
 * Doing it in the browser also keeps the server free of ffmpeg, which
 * Odoo.sh does not guarantee.
 *
 * Everything used here (AudioContext.decodeAudioData +
 * OfflineAudioContext) ships in every browser that can record at all,
 * so this does not narrow the browser support we already have.
 */

export const TARGET_SAMPLE_RATE = 16000;

/** Decode any recorded container (webm/opus, mp4/aac, …) to raw samples. */
async function decode(blob) {
    const bytes = await blob.arrayBuffer();
    const Ctor = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctor();
    try {
        // decodeAudioData is callback-based in older Safari; the promise
        // form is standard everywhere we support.
        return await ctx.decodeAudioData(bytes);
    } finally {
        // Release the hardware context; we only needed the decoder.
        ctx.close?.();
    }
}

/** Downmix to mono and resample to 16 kHz. */
async function toMono16k(audioBuffer) {
    const frames = Math.ceil(
        audioBuffer.duration * TARGET_SAMPLE_RATE
    );
    if (!frames) {
        return new Float32Array(0);
    }
    const offline = new OfflineAudioContext(1, frames, TARGET_SAMPLE_RATE);
    const source = offline.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(offline.destination);
    source.start(0);
    const rendered = await offline.startRendering();
    return rendered.getChannelData(0);
}

/** Float samples (-1..1) → little-endian PCM16 WAV bytes. */
function encodeWav(samples) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeText = (offset, text) => {
        for (let i = 0; i < text.length; i++) {
            view.setUint8(offset + i, text.charCodeAt(i));
        }
    };
    const dataBytes = samples.length * 2;

    writeText(0, "RIFF");
    view.setUint32(4, 36 + dataBytes, true);
    writeText(8, "WAVE");
    writeText(12, "fmt ");
    view.setUint32(16, 16, true); // PCM chunk size
    view.setUint16(20, 1, true); // format: PCM
    view.setUint16(22, 1, true); // channels: mono
    view.setUint32(24, TARGET_SAMPLE_RATE, true);
    view.setUint32(28, TARGET_SAMPLE_RATE * 2, true); // byte rate
    view.setUint16(32, 2, true); // block align
    view.setUint16(34, 16, true); // bits per sample
    writeText(36, "data");
    view.setUint32(40, dataBytes, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
        // Clamp before scaling: values slightly outside -1..1 occur after
        // resampling and would wrap around into loud noise.
        const clamped = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(
            offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true,
        );
        offset += 2;
    }
    return new Blob([buffer], { type: "audio/wav" });
}

/**
 * @param {Blob} blob recorded audio in any browser-supported container
 * @returns {Promise<Blob|null>} 16 kHz mono PCM16 WAV, or null when the
 *   audio could not be decoded (caller then sends the original blob).
 */
export async function toWav16k(blob) {
    try {
        const decoded = await decode(blob);
        const samples = await toMono16k(decoded);
        if (!samples.length) {
            return null;
        }
        return encodeWav(samples);
    } catch (error) {
        console.warn("daadit_agent_voice: WAV conversion failed", error);
        return null;
    }
}
