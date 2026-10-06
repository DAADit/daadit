/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

const VALID_SAMPLE_RATES = new Set([
    8000,
    16000,
    22050,
    24000,
    44100,
    48000,
]);
const PRE_ROLL_CHUNKS = 4;
const RECONNECT_DELAYS = [500, 1500, 3000];
const AUTH_ERRORS = new Set([
    "auth_error",
    "quota_exceeded",
    "unaccepted_terms",
    "invalid_request",
]);

export class RealtimeTranscriber {
    constructor({ channelId, onFallback } = {}) {
        this.channelId = channelId;
        this.sampleRate = 0;
        this.onFallback = onFallback;
        this.socket = null;
        this.sessionStarted = false;
        this.pendingText = null;
        this.waiter = null;
        this.waiterTimer = null;
        this.preRoll = [];
        this.reconnectTimer = null;
        this.reconnectAttempt = 0;
        this.connecting = false;
        this.started = false;
        this.disabled = false;
        this.dead = false;
        this.stopping = false;
        this.fallbackCalled = false;
    }

    async prepare() {
        if (this.dead) {
            return false;
        }
        let response;
        try {
            response = await rpc("/daadit_voice/realtime_token", {
                channel_id: this.channelId,
            });
        } catch (error) {
            console.debug("Realtime Scribe token request failed", error);
            this._giveUp();
            return false;
        }
        if (!response?.ok) {
            if (response?.error === "disabled") {
                this.disabled = true;
                return false;
            }
            this._giveUp();
            return false;
        }
        this.pending = response;
        return true;
    }

    connect(sampleRate) {
        this.sampleRate = sampleRate;
        if (
            this.dead
            || !VALID_SAMPLE_RATES.has(this.sampleRate)
            || typeof WebSocket === "undefined"
            || !this.pending
        ) {
            this._giveUp();
            return;
        }
        const response = this.pending;
        this.pending = null;
        this.started = true;
        try {
            this._openSocket(response);
        } catch (error) {
            console.debug("Realtime Scribe socket setup failed", error);
            this._scheduleReconnect();
        }
    }

    isLive() {
        return Boolean(
            this.socket
            && this.socket.readyState === WebSocket.OPEN
            && this.sessionStarted
        );
    }

    push(int16, active) {
        if (!this.isLive()) {
            return;
        }
        if (!active) {
            this.preRoll.push(int16);
            if (this.preRoll.length > PRE_ROLL_CHUNKS) {
                this.preRoll.shift();
            }
            return;
        }
        for (const chunk of this.preRoll) {
            this._sendChunk(chunk);
        }
        this.preRoll = [];
        this._sendChunk(int16);
    }

    commit() {
        // Op commit-moment is tekst van een vorige beurt per definitie oud.
        this.pendingText = null;
        if (!this.isLive()) {
            this.preRoll = [];
            return;
        }
        try {
            this.socket.send(JSON.stringify({ message_type: "commit" }));
        } catch (error) {
            console.debug("Realtime Scribe commit failed", error);
            this._handleSocketFailure(this.socket);
        }
        this.preRoll = [];
    }

    awaitTurn(timeoutMs) {
        if (this.pendingText !== null) {
            const text = this.pendingText;
            this.pendingText = null;
            return Promise.resolve(text);
        }
        if (this.waiter) {
            this.waiter(null);
            clearTimeout(this.waiterTimer);
        }
        return new Promise((resolve) => {
            this.waiter = resolve;
            this.waiterTimer = setTimeout(() => {
                if (this.waiter === resolve) {
                    this.waiter = null;
                    this.waiterTimer = null;
                }
                resolve(null);
            }, timeoutMs);
        });
    }

    stop() {
        this.stopping = true;
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
        clearTimeout(this.waiterTimer);
        this.waiterTimer = null;
        if (this.waiter) {
            this.waiter(null);
            this.waiter = null;
        }
        // Na stoppen hoort een laat transcript niet in een nieuw gesprek.
        this.pendingText = null;
        this.preRoll = [];
        this.sessionStarted = false;
        if (this.socket) {
            try {
                this.socket.close(1000);
            } catch {
                // The socket may already have closed.
            }
            this.socket = null;
        }
    }

    async _fetchToken() {
        return rpc("/daadit_voice/realtime_token", {
            channel_id: this.channelId,
        });
    }

    _openSocket(response) {
        const params = new URLSearchParams({
            ...response.params,
            token: response.token,
            audio_format: `pcm_${this.sampleRate}`,
        });
        const socket = new WebSocket(`${response.ws_url}?${params}`);
        this.socket = socket;
        this.sessionStarted = false;
        socket.onopen = () => {
            this.connecting = false;
        };
        socket.onmessage = (event) => this._onMessage(event);
        socket.onerror = () => {
            console.debug("Realtime Scribe socket error");
            this._handleSocketFailure(socket);
        };
        socket.onclose = () => {
            this._handleSocketFailure(socket);
        };
    }

    _onMessage(event) {
        let message;
        try {
            message = JSON.parse(event.data);
        } catch {
            console.debug("Realtime Scribe returned invalid JSON");
            return;
        }
        if (message.message_type === "session_started") {
            this.sessionStarted = true;
            this.reconnectAttempt = 0;
            return;
        }
        if (message.message_type === "committed_transcript") {
            this.pendingText = (message.text || "").trim();
            if (this.waiter) {
                const resolve = this.waiter;
                this.waiter = null;
                clearTimeout(this.waiterTimer);
                this.waiterTimer = null;
                const text = this.pendingText;
                this.pendingText = null;
                resolve(text);
            }
            return;
        }
        if (message.message_type !== "error") {
            return;
        }
        const error = message.error;
        const errorCode = typeof error === "string" ? error : error?.code;
        console.debug("Realtime Scribe error", errorCode || error);
        if (errorCode === "insufficient_audio_activity") {
            return;
        }
        if (AUTH_ERRORS.has(errorCode)) {
            this._giveUp();
        }
    }

    _handleSocketFailure(socket) {
        if (
            this.stopping
            || this.dead
            || this.socket !== socket
        ) {
            return;
        }
        this.sessionStarted = false;
        this.connecting = false;
        this.socket = null;
        this._scheduleReconnect();
    }

    _scheduleReconnect() {
        if (this.stopping || this.dead || this.reconnectTimer) {
            return;
        }
        if (this.reconnectAttempt >= RECONNECT_DELAYS.length) {
            this._giveUp();
            return;
        }
        const delay = RECONNECT_DELAYS[this.reconnectAttempt++];
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this._reconnect();
        }, delay);
    }

    async _reconnect() {
        if (this.stopping || this.dead || this.connecting) {
            return;
        }
        this.connecting = true;
        try {
            const response = await this._fetchToken();
            if (!response?.ok) {
                this.disabled = true;
                this._giveUp();
                return;
            }
            this._openSocket(response);
        } catch (error) {
            console.debug("Realtime Scribe reconnect failed", error);
            this.connecting = false;
            this._scheduleReconnect();
        }
    }

    _sendChunk(int16) {
        if (!this.isLive()) {
            return;
        }
        const bytes = new Uint8Array(
            int16.buffer,
            int16.byteOffset,
            int16.byteLength
        );
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 8192) {
            const block = bytes.subarray(offset, offset + 8192);
            binary += String.fromCharCode.apply(null, block);
        }
        try {
            this.socket.send(JSON.stringify({
                message_type: "input_audio_chunk",
                audio_base_64: btoa(binary),
                commit: false,
                sample_rate: this.sampleRate,
            }));
        } catch (error) {
            console.debug("Realtime Scribe audio send failed", error);
            this._handleSocketFailure(this.socket);
        }
    }

    _giveUp() {
        if (this.dead) {
            return;
        }
        this.dead = true;
        this.sessionStarted = false;
        this.connecting = false;
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
        clearTimeout(this.waiterTimer);
        this.waiterTimer = null;
        if (this.waiter) {
            this.waiter(null);
            this.waiter = null;
        }
        if (this.socket) {
            try {
                this.socket.close();
            } catch {
                // The socket may already have closed.
            }
            this.socket = null;
        }
        if (!this.fallbackCalled) {
            this.fallbackCalled = true;
            try {
                this.onFallback?.();
            } catch {
                // A fallback notice must not affect the voice conversation.
            }
        }
    }
}
