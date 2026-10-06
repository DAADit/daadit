/** @odoo-module **/

/**
 * Speech in and out for the agent chat.
 *
 * Both directions go through our own server, and that is a deliberate
 * choice. The browser's Web Speech API was the first implementation,
 * but Firefox does not implement recognition at all, Safari is erratic,
 * and Chrome ships the audio to Google. Recording with MediaRecorder and
 * transcribing server-side gives every colleague the same behaviour in
 * whatever browser they happen to use, and keeps the audio on a path we
 * chose.
 *
 * The Web Speech API survives as a fallback for one case only: no
 * transcription key configured. Then Chrome users still get something,
 * and everyone else gets a clear message instead of a dead button.
 */

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { reactive } from "@odoo/owl";
import { AudioTurnRecorder } from "./audio_recorder";
import { RealtimeTranscriber } from "./realtime_stt";
import { toWav16k } from "./wav_encode";

const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition || null;
// Hoe lang we op de realtime-transcriptie wachten voordat we de
// opname alsnog uploaden. Scribe begint pas te verwerken na ~2s
// audio, dus een heel korte beurt levert soms niets op — dan is de
// batch-route de vangnet.
const REALTIME_GRACE_MS = 1800;

/**
 * Voice names carry no gender flag, so we guess from the name. These are
 * the common Dutch/English voices shipped by macOS, Windows and Chrome.
 * An agent that needs certainty gets an explicit `voice_name` instead.
 */
const FEMALE_HINTS = [
    "female", "vrouw", "woman",
    "ellen", "lotte", "claire", "fenna", "colette", "xander_female",
    "samantha", "victoria", "karen", "moira", "tessa", "fiona",
    "google nederlands", "zira", "hazel", "susan", "linda",
];
const MALE_HINTS = [
    "male", "man", "heer",
    "xander", "bram", "daniel", "alex", "fred", "thomas", "rishi",
    "david", "mark", "george", "james", "ruben", "frank",
];

const DUTCH_SPEECH_ABBREVIATIONS = {
    "bijv.": "bijvoorbeeld",
    bijv: "bijvoorbeeld",
    "o.a.": "onder andere",
    "i.p.v.": "in plaats van",
    "d.w.z.": "dat wil zeggen",
    "m.a.w.": "met andere woorden",
    "e.d.": "en dergelijke",
    "enz.": "enzovoort",
    "etc.": "et cetera",
    etc: "et cetera",
    "t/m": "tot en met",
    "ca.": "circa",
    "m.b.t.": "met betrekking tot",
    "a.u.b.": "alstublieft",
    aub: "alstublieft",
    "incl.": "inclusief",
    incl: "inclusief",
    "excl.": "exclusief",
    excl: "exclusief",
    "ong.": "ongeveer",
    "nr.": "nummer",
    "blz.": "bladzijde",
    "pag.": "pagina",
    "dhr.": "de heer",
    "mevr.": "mevrouw",
    "evt.": "eventueel",
    "i.v.m.": "in verband met",
    ivm: "in verband met",
    "m.i.v.": "met ingang van",
    "z.s.m.": "zo snel mogelijk",
    zsm: "zo snel mogelijk",
    "n.a.v.": "naar aanleiding van",
};

function normalizeForSpeech(text) {
    if (text == null) {
        return "";
    }
    let normalized = String(text);
    if (!normalized.trim()) {
        return "";
    }

    normalized = normalized.replace(
        /https?:\/\/[^\s<>()]+/gi,
        " link "
    );
    const abbreviations = Object.entries(DUTCH_SPEECH_ABBREVIATIONS)
        .sort(([left], [right]) => right.length - left.length);
    for (const [abbreviation, expansion] of abbreviations) {
        const escaped = abbreviation.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        normalized = normalized.replace(
            new RegExp(
                `(^|[^\\p{L}\\p{N}])${escaped}` +
                `(?=$|[^\\p{L}\\p{N}])`,
                "giu"
            ),
            `$1${expansion}`
        );
    }

    normalized = normalized
        .replace(/&/g, " en ")
        .replace(/%/g, " procent")
        .replace(/€/g, " euro ")
        .replace(/@/g, " at ")
        .replace(/(?<=\p{L})\s*\/\s*(?=\p{L})/gu, " ")
        .replace(/\+/g, " plus ")
        .replace(/\.{3,}|…+/g, ".")
        .replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu, "")
        .replace(/[*_#`~[\]{}()<>]/g, "")
        .replace(/\s+/g, " ")
        .trim();
    return normalized;
}

function guessGender(voice) {
    const name = (voice.name || "").toLowerCase();
    if (FEMALE_HINTS.some((h) => name.includes(h))) {
        return "female";
    }
    if (MALE_HINTS.some((h) => name.includes(h))) {
        return "male";
    }
    return "unknown";
}

export const voiceService = {
    start() {
        const state = reactive({
            /** Channel of the open conversation, or null when idle. */
            conversationChannelId: null,
            /** Recognition language for that conversation. */
            conversationLang: null,
            /** True while the microphone is actually open. */
            listening: false,
            /** True while the agent is speaking. */
            speaking: false,
            /** True while a one-off dictation is active. */
            dictating: false,
            /** True when the open conversation is hold-to-talk. */
            pushToTalk: false,
            /** Last soft warning (e.g. no-input); never blocks chat. */
            lastError: null,
        });

        /** channel id -> voice config from the server (cached per session) */
        const configCache = new Map();
        /** Bumped on every start/stop so late STT/TTS results are dropped. */
        let conversationEpoch = 0;
        /** Bumped on every speak() so a newer reply cancels an older one. */
        let speakEpoch = 0;
        /** Serialise sends so rapid VAD turns cannot overlap composer sends. */
        let sendChain = Promise.resolve();
        let mutedChannels = new Set();
        try {
            const saved = JSON.parse(
                localStorage.getItem("daadit_voice_muted_channels") || "[]"
            );
            if (Array.isArray(saved)) {
                mutedChannels = new Set(saved);
            }
        } catch {
            // A malformed browser preference must not disable voice.
        }

        /**
         * Voice lists load asynchronously in Chrome; the first call after
         * page load often returns []. Resolve once they are really there.
         */
        function getVoices() {
            return new Promise((resolve) => {
                const synth = window.speechSynthesis;
                if (!synth) {
                    resolve([]);
                    return;
                }
                const ready = synth.getVoices();
                if (ready && ready.length) {
                    resolve(ready);
                    return;
                }
                let settled = false;
                const done = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    resolve(synth.getVoices() || []);
                };
                synth.addEventListener("voiceschanged", done, { once: true });
                // Safety net: some browsers never fire the event.
                setTimeout(done, 1000);
            });
        }

        /** Pick the best installed voice for a config. */
        async function pickVoice(config) {
            const voices = await getVoices();
            if (!voices.length) {
                return null;
            }
            if (config.voice_name) {
                const exact = voices.find((v) => v.name === config.voice_name);
                if (exact) {
                    return exact;
                }
            }
            const lang = (config.lang || "nl-NL").toLowerCase();
            const base = lang.split("-")[0];
            // Same language+region first, then the language family.
            const sameLang = voices.filter(
                (v) => (v.lang || "").toLowerCase() === lang
            );
            const sameBase = voices.filter((v) =>
                (v.lang || "").toLowerCase().startsWith(base)
            );
            const pool = sameLang.length ? sameLang : sameBase;
            if (!pool.length) {
                return voices[0];
            }
            if (config.gender && config.gender !== "any") {
                const matching = pool.filter(
                    (v) => guessGender(v) === config.gender
                );
                if (matching.length) {
                    return matching[0];
                }
            }
            return pool[0];
        }

        async function getConfig(channelId) {
            if (!channelId) {
                return { enabled: false };
            }
            if (configCache.has(channelId)) {
                return configCache.get(channelId);
            }
            let config = { enabled: false };
            try {
                config = await rpc("/web/dataset/call_kw", {
                    model: "discuss.channel",
                    method: "daadit_voice_config",
                    args: [channelId],
                    kwargs: {},
                });
            } catch {
                // A channel without an agent, or no access: stay silent
                // rather than breaking the chat.
                config = { enabled: false };
            }
            configCache.set(channelId, config);
            return config;
        }

        function markSpokenMode(channelId, active, minutes) {
            if (!channelId) {
                return;
            }
            try {
                rpc("/web/dataset/call_kw", {
                    model: "discuss.channel",
                    method: "daadit_voice_set_spoken",
                    args: [channelId, active],
                    kwargs: minutes ? { minutes } : {},
                }).catch(() => {});
            } catch {
                // Een mislukte statusstempel mag het gesprek nooit stoppen.
            }
        }

        /** Read `text` out loud using the agent's voice settings. */
        /** Currently playing ElevenLabs audio, so we can cut it off. */
        let currentAudio = null;
        let playbackAbort = null;

        function splitSentences(text) {
            const parts = text.split(/(?<=[.!?…])\s+/).filter(Boolean);
            const result = [];
            for (const part of parts) {
                const previous = result[result.length - 1] || "";
                const nonTerminal = /(?:^|\s)(?:\d+|[\p{L}])\.$/u.test(
                    previous
                );
                if ((part.length < 15 || nonTerminal) && result.length) {
                    result[result.length - 1] += ` ${part}`;
                } else {
                    result.push(part);
                }
            }
            return result.length ? result : [text];
        }

        /**
         * Try the server-side neural voice first.
         * Resolves true when it actually played, false when we should
         * fall back to the browser's own voice — a missing key, an
         * empty ElevenLabs credit balance and a network hiccup all end
         * up here, and in every case the user still hears a reply.
         */
        async function speakViaServer(text, channelId, onDone) {
            const sentences = splitSentences(text);
            const controller = new AbortController();
            playbackAbort = controller;
            let aborted = false;
            let started = false;
            let activeUrl = null;
            const pendingUrls = new Set();
            const fetchAudio = async (sentence) => {
                const form = new FormData();
                form.append("text", sentence);
                form.append("channel_id", channelId);
                form.append("csrf_token", odoo.csrf_token);
                const response = await fetch("/daadit_voice/speak", {
                    method: "POST",
                    body: form,
                    signal: controller.signal,
                });
                if (!response.ok || response.status === 204) {
                    throw new Error("unavailable");
                }
                const blob = await response.blob();
                if (!blob || blob.size < 128) {
                    throw new Error("unavailable");
                }
                if (controller.signal.aborted) {
                    throw new Error("aborted");
                }
                const url = URL.createObjectURL(blob);
                if (controller.signal.aborted) {
                    URL.revokeObjectURL(url);
                    throw new Error("aborted");
                }
                pendingUrls.add(url);
                return url;
            };
            try {
                let next = fetchAudio(sentences[0]);
                next.catch(() => {});
                state.speaking = true;
                for (let index = 0; index < sentences.length; index++) {
                    if (controller.signal.aborted) {
                        break;
                    }
                    const url = await next;
                    pendingUrls.delete(url);
                    activeUrl = url;
                    if (controller.signal.aborted) {
                        URL.revokeObjectURL(url);
                        activeUrl = null;
                        break;
                    }
                    if (index + 1 < sentences.length) {
                        next = fetchAudio(sentences[index + 1]);
                        next.catch(() => {});
                    }
                    await new Promise((resolve, reject) => {
                        const audio = new Audio(url);
                        currentAudio = audio;
                        let settled = false;
                        const cleanup = () => {
                            controller.signal.removeEventListener(
                                "abort",
                                onAbort
                            );
                        };
                        const finish = (callback) => (value) => {
                            if (settled) {
                                return;
                            }
                            settled = true;
                            cleanup();
                            callback(value);
                        };
                        const onAbort = finish(() => {
                            audio.pause();
                            reject(new Error("aborted"));
                        });
                        const onEnded = finish(resolve);
                        const onError = finish(() => reject(new Error("playback")));
                        controller.signal.addEventListener(
                            "abort",
                            onAbort,
                            { once: true }
                        );
                        audio.onended = onEnded;
                        audio.onerror = onError;
                        if (controller.signal.aborted) {
                            onAbort();
                            return;
                        }
                        Promise.resolve(audio.play())
                            .then(() => {
                                started = true;
                            })
                            .catch(onError);
                    });
                    URL.revokeObjectURL(url);
                    activeUrl = null;
                    currentAudio = null;
                }
                if (!controller.signal.aborted) {
                    state.speaking = false;
                    onDone?.();
                }
                return true;
            } catch {
                aborted = controller.signal.aborted;
                if (currentAudio) {
                    currentAudio = null;
                }
                if (activeUrl) {
                    URL.revokeObjectURL(activeUrl);
                    activeUrl = null;
                }
                for (const url of pendingUrls) {
                    URL.revokeObjectURL(url);
                }
                pendingUrls.clear();
                state.speaking = false;
                if (aborted) {
                    return true;
                }
                if (started) {
                    onDone?.();
                    return true;
                }
                return false;
            } finally {
                for (const url of pendingUrls) {
                    URL.revokeObjectURL(url);
                }
                pendingUrls.clear();
                if (playbackAbort === controller) {
                    playbackAbort = null;
                }
            }
        }

        async function speak(text, channelId) {
            const synth = window.speechSynthesis;
            if (!text) {
                return;
            }
            // A newer reply (or stop) supersedes this one — multi-part
            // agent answers were stacking or cutting each other mid-word.
            const mySpeak = ++speakEpoch;
            const config = await getConfig(channelId);
            if (mySpeak !== speakEpoch) {
                return;
            }
            if (!config.enabled) {
                return;
            }
            if (isMuted(channelId)) {
                return;
            }
            const speechText = normalizeForSpeech(text);
            if (!speechText) {
                return;
            }
            const handBackTurn = () => {
                if (mySpeak === speakEpoch) {
                    scheduleRestart();
                }
            };
            stopSpeaking({ keepEpoch: true });
            // Close the microphone first, or the agent's own voice comes
            // straight back in as your next question.
            pauseMic();
            if (mySpeak !== speakEpoch) {
                return;
            }
            if (config.provider === "elevenlabs") {
                const played = await speakViaServer(
                    speechText,
                    channelId,
                    handBackTurn
                );
                if (played || mySpeak !== speakEpoch) {
                    return;
                }
                // Fell through: the browser voice takes over below so a
                // credit or key problem never leaves the agent mute.
            }
            if (!synth || mySpeak !== speakEpoch) {
                return;
            }
            synth.cancel();
            const utterance = new SpeechSynthesisUtterance(speechText);
            const voice = await pickVoice(config);
            if (mySpeak !== speakEpoch) {
                return;
            }
            if (voice) {
                utterance.voice = voice;
            }
            utterance.lang = config.lang || "nl-NL";
            utterance.rate = Math.min(Math.max(config.rate || 1, 0.5), 2);
            utterance.pitch = Math.min(Math.max(config.pitch || 1, 0.5), 2);
            state.speaking = true;
            utterance.onend = () => {
                if (mySpeak !== speakEpoch) {
                    return;
                }
                state.speaking = false;
                // Hand the turn back: reopen the microphone now that we
                // are no longer talking over ourselves.
                scheduleRestart();
            };
            utterance.onerror = () => {
                if (mySpeak !== speakEpoch) {
                    return;
                }
                state.speaking = false;
                scheduleRestart();
            };
            synth.speak(utterance);
        }

        function speakStatus(text) {
            const synth = window.speechSynthesis;
            const channelId = state.conversationChannelId;
            if (
                !channelId
                || state.dictating
                || state.speaking
                || isMuted(channelId)
                || !synth
            ) {
                return;
            }
            const speechText = normalizeForSpeech(text);
            if (!speechText) {
                return;
            }
            const mySpeak = ++speakEpoch;
            stopSpeaking({ keepEpoch: true });
            pauseMic();
            const utterance = new SpeechSynthesisUtterance(speechText);
            utterance.lang = state.conversationLang || "nl-NL";
            utterance.rate = 1.1;
            state.speaking = true;
            const finish = () => {
                if (mySpeak !== speakEpoch) {
                    return;
                }
                state.speaking = false;
                scheduleRestart();
            };
            utterance.onend = finish;
            utterance.onerror = finish;
            synth.speak(utterance);
        }

        function stopSpeaking({ keepEpoch = false } = {}) {
            if (!keepEpoch) {
                speakEpoch += 1;
            }
            window.speechSynthesis?.cancel();
            playbackAbort?.abort();
            playbackAbort = null;
            if (currentAudio) {
                try {
                    currentAudio.pause();
                    currentAudio.currentTime = 0;
                } catch {
                    // Already finished.
                }
                currentAudio = null;
            }
            state.speaking = false;
        }

        // ---------------------------------------------------------------
        // Conversation mode
        // ---------------------------------------------------------------
        //
        // One click opens the conversation and it stays open until you
        // close it. That needs more than `recognition.start()`: Chrome
        // ends a recognition session on its own — after a pause, after
        // roughly a minute, whenever it feels like it — so the only way
        // to keep a conversation alive is to reopen the microphone every
        // time it closes. Hence the restart loop below.
        //
        // The other half is not listening to ourselves: while the agent
        // speaks we close the microphone, or its own voice comes back in
        // as your next question.

        let onTranscript = null;
        let onFatal = null;
        let recorder = null;
        let realtime = null;
        let fallbackRecognition = null;
        let restartTimer = null;

        /**
         * Send one recorded turn to the server for transcription.
         * Falls back to the browser's own recognition when no key is
         * configured, so Chrome users still get something rather than a
         * dead button.
         */
        async function transcribeTurn(blob, mimeType, channelId, epoch) {
            try {
                const form = new FormData();
                // Convert to 16 kHz mono WAV up front: Wispr Flow accepts
                // nothing else, and Scribe reads it just as happily — so
                // both providers share one audio path instead of a
                // per-provider branch. If this browser cannot decode its
                // own recording we send the original, which Scribe still
                // handles.
                const wav = await toWav16k(blob);
                const ext = wav
                    ? "wav"
                    : (mimeType.includes("mp4") ? "m4a" : "webm");
                form.append("audio", wav || blob, `speech.${ext}`);
                form.append("channel_id", channelId);
                form.append("csrf_token", odoo.csrf_token);
                const response = await fetch("/daadit_voice/transcribe", {
                    method: "POST",
                    body: form,
                });
                // Drop stale results from a previous conversation — a
                // late STT response after channel switch was posting
                // into the new chat.
                if (
                    epoch !== conversationEpoch ||
                    state.conversationChannelId !== channelId
                ) {
                    return;
                }
                const data = await response.json();
                if (
                    epoch !== conversationEpoch ||
                    state.conversationChannelId !== channelId
                ) {
                    return;
                }
                if (data.ok) {
                    const text = (data.text || "").trim();
                    if (text) {
                        onTranscript?.(text);
                    }
                    return;
                }
                // A spent quota is not a broken setup — the browser can
                // carry the conversation until the credits reset. Same
                // recovery as a missing key, different message.
                if (data.error === "quota_exceeded") {
                    const notify = onFatal;
                    if (SpeechRecognition && state.conversationChannelId) {
                        startBrowserFallback(channelId);
                        notify?.("quota_fallback");
                    } else {
                        stopConversation();
                        notify?.(`quota_exceeded:${data.detail || ""}`);
                    }
                    return;
                }
                if (data.error === "bad_key" || data.error === "unreachable") {
                    onFatal?.(`${data.error}:${data.detail || ""}`);
                    return;
                }
                if (data.error === "no_key") {
                    // Nothing to transcribe with. Say so once and switch
                    // to the browser route if this browser has one.
                    const notify = onFatal;
                    if (SpeechRecognition && state.conversationChannelId) {
                        startBrowserFallback(channelId);
                        notify?.("no_key_fallback");
                    } else {
                        stopConversation();
                        notify?.("no_key");
                    }
                    return;
                }
                onFatal?.(
                    data.detail
                        ? `${data.error || "transcribe_failed"}:${data.detail}`
                        : data.error || "transcribe_failed"
                );
            } catch {
                if (epoch === conversationEpoch) {
                    onFatal?.("transcribe_failed");
                }
            }
        }

        /**
         * Chrome/Edge-only safety net: the Web Speech API, used only when
         * the server has no transcription key. Firefox and Safari get a
         * clear message instead of a button that silently does nothing.
         */
        function startBrowserFallback(channelId) {
            if (!SpeechRecognition) {
                return;
            }
            stopRecorder();
            const rec = new SpeechRecognition();
            fallbackRecognition = rec;
            rec.lang = state.conversationLang || "nl-NL";
            rec.continuous = true;
            rec.interimResults = false;
            rec.onresult = (event) => {
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const result = event.results[i];
                    if (result.isFinal) {
                        const text = (result[0]?.transcript || "").trim();
                        if (text) {
                            onTranscript?.(text);
                        }
                    }
                }
            };
            rec.onend = () => {
                fallbackRecognition = null;
                if (state.conversationChannelId === channelId && !state.speaking) {
                    clearTimeout(restartTimer);
                    restartTimer = setTimeout(
                        () => startBrowserFallback(channelId),
                        500
                    );
                }
            };
            rec.onerror = (event) => {
                fallbackRecognition = null;
                const error = event.error || "";
                if (error === "not-allowed" || error === "service-not-allowed") {
                    const notify = onFatal;
                    stopConversation();
                    notify?.(error);
                }
            };
            try {
                rec.start();
                state.listening = true;
            } catch {
                fallbackRecognition = null;
            }
        }

        function stopRecorder() {
            if (recorder) {
                try {
                    recorder.stop();
                } catch {
                    // Already torn down.
                }
                recorder = null;
            }
        }

        function stopFallback() {
            clearTimeout(restartTimer);
            if (fallbackRecognition) {
                try {
                    fallbackRecognition.abort();
                } catch {
                    // Already stopped.
                }
                fallbackRecognition = null;
            }
        }

        /** Close the microphone while the agent talks. */
        function pauseMic() {
            recorder?.pause();
            if (fallbackRecognition) {
                stopFallback();
            }
            state.listening = false;
        }

        /** Reopen it once the agent has finished. */
        function scheduleRestart() {
            if (!state.conversationChannelId) {
                return;
            }
            // Hold-to-talk stays paused between turns: the key opens the
            // next capture. Resuming here used to flip a PTT session into
            // continuous VAD after the agent's reply — and pull room
            // noise into the next message.
            if (state.pushToTalk) {
                recorder?.pause?.();
                state.listening = false;
                return;
            }
            if (recorder) {
                recorder.resume();
                state.listening = true;
                return;
            }
            if (SpeechRecognition && !fallbackRecognition) {
                startBrowserFallback(state.conversationChannelId);
            }
        }

        /**
         * Start a hands-free conversation on this channel.
         * ``onText`` fires for every finished sentence you speak.
         */
        async function startConversation(
            channelId, onText, onFatalError, options = {}
        ) {
            if (state.conversationChannelId) {
                stopConversation();
            }
            // Prefer a fresh config: agent voice settings change often
            // and a stale cache left the mic using yesterday's provider.
            configCache.delete(String(channelId));
            const config = await getConfig(channelId);
            const epoch = ++conversationEpoch;
            sendChain = Promise.resolve();
            state.conversationChannelId = channelId;
            markSpokenMode(channelId, true);
            state.conversationLang = config.lang || "nl-NL";
            state.lastError = null;
            if (options.dictation) {
                state.dictating = true;
            }
            // Queue sends so two quick VAD turns cannot race composerText
            // / sendMessage and drop a sentence.
            onTranscript = (text) => {
                markSpokenMode(state.conversationChannelId, true);
                sendChain = sendChain
                    .then(() => onText?.(text))
                    .catch(() => {});
            };
            onFatal = onFatalError;
            stopSpeaking();

            let preparedRealtime = null;
            if (config.provider === "elevenlabs") {
                const transcriber = new RealtimeTranscriber({
                    channelId,
                    onFallback: () => onFatal?.("realtime_fallback"),
                });
                if (await transcriber.prepare()) {
                    if (
                        epoch === conversationEpoch
                        && state.conversationChannelId === channelId
                    ) {
                        preparedRealtime = transcriber;
                        realtime = transcriber;
                    } else {
                        transcriber.stop();
                    }
                }
            }
            const realtimeForConversation = preparedRealtime;

            if (!AudioTurnRecorder.supported) {
                // Ancient browser: try the Web Speech API or give up.
                if (!SpeechRecognition) {
                    state.conversationChannelId = null;
                    throw new Error("unsupported");
                }
                startBrowserFallback(channelId);
                return;
            }

            const rec = new AudioTurnRecorder({
                pcmSink: realtimeForConversation,
                onTurn: async (blob, mimeType) => {
                    // Eén emit-pad: de realtime-transcriber stuurt zelf nooit
                    // tekst de chat in. Levert hij binnen de grace niets op,
                    // dan gaat de opname die we toch al hebben alsnog naar de
                    // batch-route.
                    if (realtimeForConversation?.isLive()) {
                        const text = await realtimeForConversation.awaitTurn(
                            REALTIME_GRACE_MS
                        );
                        if (
                            epoch === conversationEpoch
                            && state.conversationChannelId === channelId
                            && text
                        ) {
                            onTranscript?.(text.trim());
                            return;
                        }
                    }
                    transcribeTurn(blob, mimeType, channelId, epoch);
                },
                onFatal: (reason) => {
                    // "I hear nothing" is a soft warning: keep the mic
                    // open so the user can fix the input and keep talking.
                    // Stopping here left the UI lit while the service was
                    // already dead.
                    if (String(reason).startsWith("no-input:")) {
                        state.lastError = reason;
                        onFatal?.(reason);
                        return;
                    }
                    const notify = onFatal;
                    stopConversation();
                    notify?.(reason);
                },
                // You started talking while the agent was still reading
                // out its answer. Cut it off mid-sentence and listen —
                // that is what a person would do, and sitting through a
                // long answer you already understood is the main reason
                // spoken assistants feel tedious.
                onBargeIn: () => {
                    if (!state.speaking) {
                        return;
                    }
                    stopSpeaking();
                    if (state.pushToTalk) {
                        // PTT: wait for the key; do not start VAD capture.
                        state.listening = false;
                        return;
                    }
                    rec.resume();
                    state.listening = true;
                },
            });
            try {
                await rec.start();
            } catch (error) {
                if (epoch === conversationEpoch) {
                    state.conversationChannelId = null;
                }
                throw error;
            }
            if (epoch !== conversationEpoch) {
                rec.stop();
                return;
            }
            recorder = rec;
            if (realtimeForConversation) {
                realtimeForConversation.connect(rec.sampleRate);
            }
            if (options.pushToTalk) {
                // Hold-to-talk: stay open but capture nothing until the
                // key goes down. Without this the microphone would record
                // the room between turns exactly as before.
                rec.pause();
                state.pushToTalk = true;
                state.listening = false;
            } else {
                state.pushToTalk = false;
                state.listening = true;
            }
        }

        async function startDictation(channelId, onText, onFatalError) {
            if (state.conversationChannelId) {
                stopConversation();
            }
            return startConversation(
                channelId,
                (text) => {
                    try {
                        onText?.(text);
                    } finally {
                        stopConversation();
                    }
                },
                onFatalError,
                { dictation: true }
            );
        }

        /**
         * Talk key pressed. Returns false when there is nothing to talk
         * into, so the caller can start a conversation first.
         */
        function beginPushToTalk() {
            if (!recorder || !state.conversationChannelId) {
                return false;
            }
            stopSpeaking();
            recorder.beginPush();
            state.listening = true;
            return true;
        }

        /** Talk key released: close the turn and send it. */
        function endPushToTalk(send = true) {
            if (!recorder) {
                return false;
            }
            const had = recorder.endPush(send);
            state.listening = false;
            return had;
        }

        function stopConversation() {
            const id = state.conversationChannelId;
            const wasDictating = state.dictating;
            conversationEpoch += 1;
            sendChain = Promise.resolve();
            clearTimeout(restartTimer);
            if (id) {
                markSpokenMode(id, wasDictating, wasDictating ? 3 : undefined);
            }
            state.conversationChannelId = null;
            state.conversationLang = null;
            onTranscript = null;
            onFatal = null;
            stopRecorder();
            realtime?.stop();
            realtime = null;
            stopFallback();
            stopSpeaking();
            state.listening = false;
            state.dictating = false;
            state.pushToTalk = false;
            state.lastError = null;
        }

        function isMuted(channelId) {
            return mutedChannels.has(String(channelId));
        }

        function setMuted(channelId, muted) {
            const id = String(channelId);
            if (muted) {
                mutedChannels.add(id);
                markSpokenMode(channelId, false);
                stopSpeaking();
            } else {
                mutedChannels.delete(id);
                if (isConversing(channelId)) {
                    markSpokenMode(channelId, true);
                }
            }
            localStorage.setItem(
                "daadit_voice_muted_channels",
                JSON.stringify([...mutedChannels])
            );
        }

        function isConversing(channelId) {
            return state.conversationChannelId === channelId;
        }

        return {
            state,
            speak,
            speakStatus,
            stopSpeaking,
            pauseMic,
            startConversation,
            startDictation,
            stopConversation,
            beginPushToTalk,
            endPushToTalk,
            isConversing,
            isMuted,
            setMuted,
            getConfig,
            getVoices,
            guessGender,
            pickVoice,
            // Recording is what decides whether the button appears, and
            // MediaRecorder exists everywhere — so Firefox and Safari
            // users get the microphone too. Tying this to
            // SpeechRecognition (as the first version did) would have
            // hidden the button in exactly the browsers we set out to
            // support.
            canListen:
                AudioTurnRecorder.supported || Boolean(SpeechRecognition),
            canSpeak: Boolean(window.speechSynthesis),
        };
    },
};

registry.category("services").add("daadit_voice", voiceService);
