/** @odoo-module **/

/**
 * Records spoken turns with MediaRecorder and cuts them on silence.
 *
 * The Web Speech API was the obvious first choice, but Firefox does not
 * implement it at all and Safari is erratic — and in Chrome it quietly
 * ships your audio to Google. MediaRecorder is supported everywhere and
 * hands us the bytes, so we decide where they go.
 *
 * The tricky part is knowing when a sentence ends. We watch the input
 * level through an AnalyserNode: once you start speaking we record, and
 * when it has been quiet for a moment we close the segment and send it
 * off. That mirrors how the browser's own recognition felt, without
 * depending on it.
 */

// Floor for the speech threshold. A fixed value cannot work for every
// microphone — a headset runs hot, a laptop array runs very quiet — so
// the real threshold is calibrated against the room in `_calibrate()`
// and this is only the lower bound.
const MIN_THRESHOLD = 0.004;
// How long we listen to the room before deciding what silence sounds
// like.
const CALIBRATION_MS = 700;
// Speech has to stand out this much above the measured noise floor.
// Raised from 2.5: in a room with people talking nearby, 2.5 let their
// conversation through as if it had been said into the microphone.
const NOISE_MULTIPLIER = 3.5;
// Sustained loudness before we accept it as the start of a turn. A
// door, a cough or a laugh across the room is loud but brief; speech
// aimed at the microphone holds for longer than this.
const SPEECH_ONSET_MS = 250;
// Barge-in: while the agent talks, its own voice returns through the
// speakers. Echo cancellation removes most of that but not all. Only a
// deliberate, sustained "spoken over the agent" signal should interrupt;
// a cough, door or nearby chatter should not.
const BARGE_IN_MULTIPLIER = 3.5;
const BARGE_IN_SUSTAIN_MS = 700;
// If nothing ever crosses the threshold we tell the user instead of
// leaving the microphone open forever with nothing happening.
const NO_SPEECH_TIMEOUT_MS = 12000;
// Quiet for this long ends the turn. Shorter cuts people off mid-thought;
// longer makes the conversation feel sluggish.
const SILENCE_MS = 1100;
// A blip this short is a cough or a door, not a word. Kept deliberately
// low: at a 50ms poll a short "hallo" only registers as loud two or
// three times, and the earlier 350ms cut-off threw exactly those away.
const MIN_SPEECH_MS = 120;
// Poll interval. Fine enough to measure a single word properly.
const POLL_MS = 50;
// Hard stop so a forgotten open microphone cannot record forever.
const MAX_TURN_MS = 30000;

/** Pick a container this browser can actually produce. */
function pickMimeType() {
    const candidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        // Safari records mp4/aac and nothing else.
        "audio/mp4",
    ];
    for (const type of candidates) {
        if (window.MediaRecorder?.isTypeSupported?.(type)) {
            return type;
        }
    }
    return "";
}

export class AudioTurnRecorder {
    /**
     * @param {object} handlers
     * @param {(blob: Blob, mimeType: string) => void} handlers.onTurn
     * @param {(reason: string) => void} handlers.onFatal
     */
    constructor({ onTurn, onFatal, onBargeIn, pcmSink = null }) {
        this.onTurn = onTurn;
        this.onFatal = onFatal;
        // Fired when the user starts talking over the agent. The service
        // uses it to cut the playback short and hand the turn back.
        this.onBargeIn = onBargeIn;
        this.pcmSink = pcmSink;
        this.loudSinceAt = 0;
        this.bargeLoudSinceAt = 0;
        this.stream = null;
        this.audioContext = null;
        this.sampleRate = 0;
        this.analyser = null;
        this.pcmTap = null;
        this.pcmSilence = null;
        this.recorder = null;
        this.chunks = [];
        this.pollTimer = null;
        this.speaking = false;
        this.speechStartedAt = 0;
        this.lastLoudAt = 0;
        this.paused = false;
        this.running = false;
        this.mimeType = pickMimeType();
        this.threshold = MIN_THRESHOLD;
        // Push-to-talk: while this is on, the level detection is bypassed
        // entirely and the turn boundaries are whatever the key says they
        // are. Voice-activity detection is the right default for a
        // hands-free conversation, but it is the wrong model when someone
        // wants to say one thing and be done — it keeps the microphone
        // open, cuts you off after a pause mid-sentence, and gives no way
        // to signal "I have finished".
        this.pushToTalk = false;
        // Loudest level seen since we started. If a user reports "it
        // hears nothing", this number says whether the microphone is
        // delivering audio at all or the threshold was simply too high.
        this.peakLevel = 0;
        this.noiseFloor = 0;
        this.startedAt = 0;
        this.everHeardSpeech = false;
    }

    static get supported() {
        return Boolean(
            navigator.mediaDevices?.getUserMedia && window.MediaRecorder
        );
    }

    async start() {
        if (!AudioTurnRecorder.supported) {
            throw new Error("unsupported");
        }
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });
        } catch (error) {
            const name = error?.name || "";
            if (name === "NotAllowedError" || name === "SecurityError") {
                throw new Error("not-allowed");
            }
            if (name === "NotFoundError" || name === "OverconstrainedError") {
                throw new Error("audio-capture");
            }
            throw new Error(name || "mic-failed");
        }

        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (this.pcmSink) {
            try {
                // Scribe wil 16 kHz; die direct vragen scheelt driekwart van
                // de bytes en een resampler.
                this.audioContext = new AudioCtx({ sampleRate: 16000 });
            } catch {
                // Safari negeert of weigert de wens; de echte samplerate
                // volgt hieronder en gaat dan naar de transcriber.
                this.audioContext = new AudioCtx();
            }
        } else {
            this.audioContext = new AudioCtx();
        }
        this.sampleRate = this.audioContext.sampleRate;
        // Browsers start the context suspended until a user gesture; the
        // click that opened the conversation counts, but resume anyway.
        if (this.audioContext.state === "suspended") {
            await this.audioContext.resume().catch(() => {});
        }
        const source = this.audioContext.createMediaStreamSource(this.stream);
        this.analyser = this.audioContext.createAnalyser();
        this.analyser.fftSize = 2048;
        source.connect(this.analyser);
        if (this.pcmSink) {
            try {
                await this.audioContext.audioWorklet.addModule(
                    "/daadit_agent_voice/static/src/pcm_worklet.js"
                );
                const tap = new AudioWorkletNode(
                    this.audioContext,
                    "daadit-pcm-tap"
                );
                tap.port.onmessage = (event) => {
                    if (!this.running || this.paused) {
                        return;
                    }
                    this.pcmSink?.push(
                        new Int16Array(event.data),
                        Boolean(this.speaking || this.pushToTalk)
                    );
                };
                source.connect(tap);
                // Een worklet zonder pad naar de uitgang wordt niet gepompt;
                // deze stille gain voorkomt dat de microfoon terugkomt via
                // de speakers.
                const silence = this.audioContext.createGain();
                silence.gain.value = 0;
                tap.connect(silence);
                silence.connect(this.audioContext.destination);
                this.pcmTap = tap;
                this.pcmSilence = silence;
            } catch {
                this.pcmSink = null;
            }
        }

        this.running = true;
        this.buffer = new Float32Array(this.analyser.fftSize);
        this.startedAt = Date.now();
        await this._calibrate();
        // Recording runs continuously rather than starting when speech
        // is detected: by the time the level crosses the threshold the
        // first syllable is already gone, which ate the "H" of "hallo".
        // We keep the tape rolling and simply decide, at the silence
        // cut, whether the segment contained speech worth sending.
        this._startSegment();
        this.pollTimer = setInterval(() => this._poll(), POLL_MS);
    }

    /**
     * Listen to the room for a moment and set the speech threshold just
     * above whatever it sounds like when nobody is talking. Without this
     * a quiet laptop microphone never crosses a fixed threshold and the
     * recorder waits forever — which is exactly what happened with the
     * hard-coded value this replaces.
     */
    async _calibrate() {
        const samples = [];
        const deadline = Date.now() + CALIBRATION_MS;
        while (Date.now() < deadline) {
            samples.push(this._level());
            await new Promise((resolve) => setTimeout(resolve, 50));
        }
        samples.sort((a, b) => a - b);
        // Median, so one door slam does not skew the floor.
        this.noiseFloor = samples[Math.floor(samples.length / 2)] || 0;
        this.threshold = Math.max(
            MIN_THRESHOLD,
            this.noiseFloor * NOISE_MULTIPLIER
        );
        this.lastLoudAt = Date.now();
    }

    /** Root-mean-square level of the current audio frame. */
    _level() {
        if (!this.analyser) {
            return 0;
        }
        this.analyser.getFloatTimeDomainData(this.buffer);
        let sum = 0;
        for (let i = 0; i < this.buffer.length; i++) {
            sum += this.buffer[i] * this.buffer[i];
        }
        return Math.sqrt(sum / this.buffer.length);
    }

    _poll() {
        if (!this.running) {
            return;
        }
        const now = Date.now();
        const level = this._level();

        // Held key: the segment is opened and closed by hand, so none of
        // the onset, silence or no-speech logic below applies. We still
        // track the peak so the diagnostics stay useful.
        if (this.pushToTalk) {
            if (level > this.peakLevel) {
                this.peakLevel = level;
            }
            if (level > this.threshold) {
                this.everHeardSpeech = true;
            }
            return;
        }

        // Paused means the agent is talking. We keep listening anyway —
        // that is the only way to notice you cutting in — but we do not
        // record, and we demand a much stronger signal so the agent's
        // own voice coming back through the speakers cannot interrupt
        // itself.
        if (this.paused) {
            const bargeThreshold = this.threshold * BARGE_IN_MULTIPLIER;
            if (level > bargeThreshold) {
                if (!this.bargeLoudSinceAt) {
                    this.bargeLoudSinceAt = now;
                } else if (now - this.bargeLoudSinceAt >= BARGE_IN_SUSTAIN_MS) {
                    this.bargeLoudSinceAt = 0;
                    this.onBargeIn?.();
                }
            } else {
                this.bargeLoudSinceAt = 0;
            }
            return;
        }
        if (level > this.peakLevel) {
            this.peakLevel = level;
        }
        const loud = level > this.threshold;

        // Nothing has crossed the threshold since we opened. Say so,
        // with the numbers, rather than sitting there looking active.
        if (
            !this.everHeardSpeech &&
            now - this.startedAt > NO_SPEECH_TIMEOUT_MS
        ) {
            this.everHeardSpeech = true; // only warn once
            this.onFatal?.(
                `no-input:piek=${this.peakLevel.toFixed(4)} ` +
                `drempel=${this.threshold.toFixed(4)}`
            );
            return;
        }

        if (loud) {
            this.everHeardSpeech = true;
            this.lastLoudAt = now;
            if (!this.speaking) {
                // Require the level to hold before calling it speech.
                // Without this, one loud moment from the room next door
                // opens a turn and we transcribe someone else's meeting.
                if (!this.loudSinceAt) {
                    this.loudSinceAt = now;
                } else if (now - this.loudSinceAt >= SPEECH_ONSET_MS) {
                    this.speaking = true;
                    this.speechStartedAt = this.loudSinceAt;
                }
            }
        } else if (!this.speaking) {
            this.loudSinceAt = 0;
        } else if (this.speaking) {
            const quietFor = now - this.lastLoudAt;
            // Count the poll that first went loud as real speech, or a
            // one-sample word measures as zero and gets discarded.
            const spokeFor = this.lastLoudAt - this.speechStartedAt + POLL_MS;
            if (quietFor >= SILENCE_MS) {
                this.speaking = false;
                this.loudSinceAt = 0;
                // Too short to be a word: drop it rather than pay to
                // transcribe a cough.
                this._stopSegment(spokeFor >= MIN_SPEECH_MS);
            }
        }

        if (this.speaking && now - this.speechStartedAt > MAX_TURN_MS) {
            this.speaking = false;
            this._stopSegment(true);
        }
    }

    _startSegment() {
        if (this.recorder || !this.stream) {
            return;
        }
        this.chunks = [];
        try {
            this.recorder = this.mimeType
                ? new MediaRecorder(this.stream, { mimeType: this.mimeType })
                : new MediaRecorder(this.stream);
        } catch {
            this.recorder = null;
            this.onFatal?.("recorder-failed");
            return;
        }
        this.recorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                this.chunks.push(event.data);
            }
        };
        this.recorder.onstop = () => {
            const blob = new Blob(this.chunks, {
                type: this.recorder?.mimeType || this.mimeType || "audio/webm",
            });
            this.chunks = [];
            const keep = this._keepCurrent;
            this.recorder = null;
            if (keep && blob.size > 512) {
                this.onTurn?.(blob, blob.type);
            }
            // Roll straight into the next segment so the microphone is
            // never closed mid-conversation.
            if (this.running && !this.paused) {
                this._startSegment();
            }
        };
        this.recorder.start();
    }

    _stopSegment(keep) {
        this._keepCurrent = keep;
        // Alleen een echte beurt sluit een realtime-segment af; een kuch, een
        // pauze of een afbreking heeft niets om af te sluiten.
        if (keep) {
            this.pcmSink?.commit();
        }
        if (this.recorder && this.recorder.state !== "inactive") {
            try {
                this.recorder.stop();
            } catch {
                this.recorder = null;
            }
        }
    }

    /**
     * Open a turn because the user pressed and held the talk key.
     *
     * Deliberately ignores the calibrated threshold: the user has told
     * us they are talking, which is a better signal than any level
     * measurement, and it means a quiet voice or a distant microphone
     * still records.
     */
    beginPush() {
        if (!this.running) {
            return;
        }
        this.pushToTalk = true;
        this.bargeLoudSinceAt = 0;
        this.speaking = false;
        this.loudSinceAt = 0;
        // Drop any idle/noise segment so the hold starts clean. Pause
        // is cleared AFTER that stop so onstop does not immediately
        // open a new segment under the old flags.
        if (this.recorder) {
            this.paused = true;
            this._stopSegment(false);
        }
        this.paused = false;
        if (!this.recorder) {
            this._startSegment();
        }
    }

    /**
     * Close the turn on key release and send it.
     *
     * Returns false when nothing was recorded, so the caller can stay
     * silent instead of posting an empty message.
     *
     * `send` is false for a tap too short to be speech — the double-tap
     * that toggles hands-free mode would otherwise post two fragments of
     * room noise.
     */
    endPush(send = true) {
        if (!this.pushToTalk) {
            return false;
        }
        this.pushToTalk = false;
        const had = Boolean(this.recorder);
        // Pause BEFORE stop so onstop does not roll into a new segment
        // — between holds we only listen for barge-in, we do not record.
        this.paused = true;
        this.speaking = false;
        this.loudSinceAt = 0;
        if (had) {
            this._stopSegment(Boolean(send));
        }
        return had;
    }

    /**
     * Stop capturing while the agent speaks — but keep the microphone
     * open and keep measuring, because that is what lets us hear you
     * cutting in. Polling continues; `_poll` takes the paused branch.
     *
     * Always discard the current segment. Leaving MediaRecorder running
     * here pulled the agent's own voice into the next transcription.
     */
    pause() {
        this.paused = true;
        this.bargeLoudSinceAt = 0;
        this.loudSinceAt = 0;
        this.speaking = false;
        this._stopSegment(false);
    }

    resume() {
        this.paused = false;
        this.bargeLoudSinceAt = 0;
        this.loudSinceAt = 0;
        this.lastLoudAt = Date.now();
        if (this.running && !this.recorder) {
            this._startSegment();
        }
    }

    stop() {
        this.running = false;
        this.paused = false;
        clearInterval(this.pollTimer);
        this.pollTimer = null;
        this._stopSegment(false);
        try {
            this.pcmTap?.disconnect();
            this.pcmSilence?.disconnect();
        } catch {
            // The audio graph may already be closed.
        }
        this.pcmTap = null;
        this.pcmSilence = null;
        this.stream?.getTracks().forEach((track) => track.stop());
        this.stream = null;
        this.analyser = null;
        this.audioContext?.close().catch(() => {});
        this.audioContext = null;
    }
}
