/** @odoo-module **/

/**
 * Microphone button in the chat composer.
 *
 * Registered through the stock composer-action registry rather than an
 * XPath on the composer template: Odoo owns the layout, we only add an
 * entry, so a redesign of the composer cannot leave us with a broken
 * view. The button only shows up in an AI chat whose agent has voice
 * switched on.
 */

import { _t } from "@web/core/l10n/translation";
import {
    ComposerAction,
    registerComposerAction,
} from "@mail/core/common/composer_actions";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onMounted, onWillUnmount, useState } from "@odoo/owl";

/**
 * Opt the microphone back into the AI chat composer.
 *
 * Odoo's own `ai` module strips every composer action except
 * "send-message" once the conversation partner is an agent — which is
 * why an AI chat shows no emoji or attachment button either. That is a
 * deliberate simplification, but it also swallowed our microphone.
 *
 * Returning `undefined` here is not a no-op: `Action.condition` treats
 * it as "no opinion" and falls through to the action's own condition.
 * So this hands the decision back to our definition without disturbing
 * the filter for any other action.
 */
patch(ComposerAction.prototype, {
    _condition() {
        if (
            [
                "daadit-voice-mic",
                "daadit-voice-dictate",
                "daadit-voice-speaker",
            ].includes(this.id)
        ) {
            return undefined;
        }
        return super._condition(...arguments);
    },
});

/**
 * Resolve the AI-chat channel id behind a composer, if any.
 *
 * Deliberately synchronous. An earlier version also waited for the
 * server to confirm that voice was enabled before showing the button —
 * but at composer setup the thread is often not loaded yet, so that
 * check ran against nothing and the microphone never appeared. Whether
 * the agent actually speaks is decided when you click, where an async
 * round-trip costs nothing.
 */
function channelIdOf(composer) {
    const thread = composer?.targetThread || composer?.thread;
    if (!thread || thread.model !== "discuss.channel") {
        return null;
    }
    // Only in a conversation with an agent: dictating into a colleague
    // chat is a different feature with different expectations.
    if (thread.channel_type !== "ai_chat") {
        return null;
    }
    return thread.id;
}


// ---------------------------------------------------------------------
// Talk key
// ---------------------------------------------------------------------
//
// Voice-activity detection is the right default for hands-free use, but
// it is the wrong model when you want to say one thing and be done: the
// microphone stays open, a pause mid-sentence cuts you off, and there is
// no way to signal "finished". So the key decides the turn instead.
//
//   hold Option+Shift (Alt+Shift on Windows) .. talk, release to send
//   double-tap the same combination ........... hands-free on/off
//
// The default uses modifiers only, so nothing is typed into the composer.
// Configured key-based combinations are intercepted only when they match.

const TALK_TAP_MS = 260;      // below this it was a tap, not speech
const TALK_DOUBLE_MS = 450;   // two taps inside this window toggle mode

const TALK_COMBOS = {
    "alt+shift": { modifiers: new Set(["alt", "shift"]) },
    "ctrl+alt": { modifiers: new Set(["ctrl", "alt"]) },
    "ctrl+shift": { modifiers: new Set(["ctrl", "shift"]) },
    "ctrl+space": { modifiers: new Set(["ctrl"]), key: "space" },
    "alt+shift+space": {
        modifiers: new Set(["alt", "shift"]),
        key: "space",
    },
    "alt+`": { modifiers: new Set(["alt"]), key: "`" },
};

function parseTalkCombo(value) {
    return TALK_COMBOS[normalizedTalkCombo(value)];
}

function normalizedTalkCombo(value) {
    const key = String(value || "").trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(TALK_COMBOS, key)
        ? key
        : "alt+shift";
}

function isTalkComboPart(ev, combo) {
    if (combo.key) {
        return (
            ev.key.toLowerCase() === combo.key ||
            ev.code.toLowerCase() === combo.key
        );
    }
    return ["Alt", "Control", "Shift", "Meta"].includes(ev.key);
}

function isTalkComboRelease(ev, combo) {
    if (isTalkComboPart(ev, combo)) {
        return true;
    }
    const releasedModifier = {
        Alt: "alt",
        Control: "ctrl",
        Shift: "shift",
        Meta: "meta",
    }[ev.key];
    return Boolean(releasedModifier && combo.modifiers.has(releasedModifier));
}

function isTalkCombo(ev, combo) {
    if (
        combo.modifiers.has("alt") !== ev.altKey ||
        combo.modifiers.has("ctrl") !== ev.ctrlKey ||
        combo.modifiers.has("shift") !== ev.shiftKey ||
        combo.modifiers.has("meta") !== ev.metaKey
    ) {
        return false;
    }
    return isTalkComboPart(ev, combo);
}

/** The composer behind a component, however this Odoo version exposes it. */
function composerOf(owner) {
    return owner?.props?.composer || owner?.composer || null;
}

/**
 * Open a voice conversation for this composer.
 *
 * Shared by the microphone button and the talk key so both take exactly
 * the same route — the only difference is `pushToTalk`, which decides
 * whether the recorder opens listening or waits for a key.
 *
 * Returns true when the conversation is live.
 */
/**
 * Open a voice conversation for this composer.
 *
 * Shared by the microphone button and the talk key so both take exactly
 * the same route. The only difference is `pushToTalk`, which decides
 * whether the recorder opens listening or waits for the key.
 *
 * Returns true when the conversation is live.
 */
async function openConversation({ owner, composer, channelId, notify, pushToTalk }) {
    const voice = owner.daaditVoice?.service;
    if (!voice || !channelId) {
        return false;
    }

    // Checked here rather than in the action's condition: by the time
    // someone talks the thread is certainly loaded, so this answer is
    // reliable.
    const config = await voice.getConfig(channelId);
    if (!config.enabled) {
        notify(
            _t(
                "Praten staat uit voor deze agent. Zet 'Praten met deze " +
                "agent' aan op zijn kaart."
            ),
            "info"
        );
        return false;
    }

    try {
        await voice.startConversation(
            channelId,
            // Every finished turn goes straight out as a message, so you
            // see what was understood as you talk.
            async (text) => {
                try {
                    markVoiceTurn(channelId);
                    // `composerText` is the field the composer model
                    // actually exposes; assigning `text` silently did
                    // nothing, so every transcription was dropped.
                    composer.composerText = text;
                    await owner.sendMessage();
                } catch {
                    // A failed send must not kill the conversation.
                }
            },
            (error) => onVoiceError({ owner, notify, error }),
            { pushToTalk }
        );
        owner.daaditVoiceState.conversing = true;
        if (!pushToTalk) {
            notify(
                _t("Gesprek gestart — praat maar. Klik nogmaals om te stoppen."),
                "info"
            );
        }
        return true;
    } catch (error) {
        owner.daaditVoiceState.conversing = false;
        notify(
            error?.message === "unsupported"
                ? _t(
                      "Deze browser kan geen spraak herkennen. Gebruik " +
                      "Chrome of Edge; voorlezen werkt hier wel."
                  )
                : _t("Het gesprek kon niet starten: %s", String(error?.message || error))
        );
        return false;
    }
}

/**
 * Turn a signal from the voice service into something readable.
 *
 * Two of these do NOT end the conversation: a missing key and a spent
 * quota both fall back to the browser's own recognition, so the button
 * stays lit and only the message changes.
 *
 * Everything else arrives as "code:detail" when the server could read a
 * reason from the provider. Splitting that here is what turns a bare
 * "request_failed" into a sentence someone can act on — the reason was
 * always in the response body, it just never reached the screen.
 */
function onVoiceError({ owner, notify, error }) {
    if (error === "quota_fallback") {
        notify(
            _t(
                "Het ElevenLabs-tegoed is op. Ik ga verder met de " +
                "spraakherkenning van de browser — dat werkt, maar de agent " +
                "praat terug met de systeemstem."
            ),
            "info"
        );
        return;
    }
    if (error === "no_key_fallback") {
        notify(
            _t(
                "Nog geen ElevenLabs-sleutel ingesteld — ik gebruik zolang de " +
                "spraakherkenning van de browser. Vul de sleutel in bij " +
                "Instellingen → Praten met AI-agents."
            ),
            "info"
        );
        return;
    }
    if (error === "realtime_fallback") {
        notify(
            _t(
                "Het live meeschrijven viel weg — ik schrijf je woorden nu " +
                "uit na elke zin. Je kunt gewoon doorpraten."
            ),
            "info"
        );
        return;
    }
    // Diagnostic from the recorder: it carries the measured levels, which
    // is the difference between "your microphone is muted" and "my
    // threshold was wrong". Keep the conversation running.
    if (String(error).startsWith("no-input:")) {
        notify(
            _t(
                "Ik hoor niets binnenkomen. Controleer of de juiste microfoon " +
                "actief is in Systeeminstellingen → Geluid → Invoer, en of het " +
                "niveau uitslaat als je praat. (%s)",
                String(error).slice("no-input:".length)
            ),
            "warning"
        );
        return;
    }

    owner.daaditVoiceState.conversing = false;
    owner.daaditVoiceState.handsFree = false;
    owner.daaditVoiceState.talking = false;

    const raw = String(error);
    const split = raw.indexOf(":");
    const code = split === -1 ? raw : raw.slice(0, split);
    const detail = split === -1 ? "" : raw.slice(split + 1).trim();

    const messages = {
        quota_exceeded: _t(
            "Het ElevenLabs-tegoed is op en deze browser kan zelf geen spraak " +
            "herkennen. Gebruik Chrome of Edge, of verhoog het abonnement."
        ),
        bad_key: _t(
            "ElevenLabs weigert de sleutel. Controleer hem bij Instellingen → " +
            "Praten met AI-agents."
        ),
        unreachable: _t(
            "ElevenLabs was niet bereikbaar. Meestal tijdelijk; probeer het zo " +
            "nog eens."
        ),
        no_key: _t(
            "Er is geen ElevenLabs-sleutel ingesteld, en deze browser kan zelf " +
            "geen spraak herkennen. Vul de sleutel in bij Instellingen → Praten " +
            "met AI-agents."
        ),
        transcribe_failed: _t(
            "Het uitschrijven van je opname mislukte. Controleer of de " +
            "ElevenLabs-sleutel geldig is en of er nog tegoed is."
        ),
        too_large: _t("Die opname was te lang. Praat in kortere stukken."),
        "not-allowed": _t(
            "Geen toegang tot de microfoon. Klik op het slotje links in de " +
            "adresbalk en zet de microfoon op 'Toestaan'."
        ),
        "service-not-allowed": _t(
            "De browser blokkeert de spraakdienst. Dit werkt alleen in Chrome " +
            "of Edge, en niet in een incognitovenster."
        ),
        network: _t(
            "De spraakherkenning kon Google's spraakdienst niet bereiken. " +
            "Chrome verstuurt je audio daarheen; een VPN of firewall kan dat " +
            "blokkeren."
        ),
        "audio-capture": _t(
            "Er werd geen microfoon gevonden. Controleer je invoerapparaat in " +
            "Systeeminstellingen → Geluid."
        ),
        "no-speech": _t(
            "De microfoon staat open maar er komt geen geluid binnen. " +
            "Controleer in Systeeminstellingen → Geluid of de juiste microfoon " +
            "geselecteerd is."
        ),
        "no-audio": _t(
            "De microfoon ging wel open, maar er kwam geen geluid binnen. " +
            "Controleer in Systeeminstellingen → Geluid welke microfoon actief " +
            "is, en of Chrome die mag gebruiken."
        ),
    };

    const known = messages[code];
    notify(
        known
            ? (detail ? `${known} (${detail})` : known)
            : _t(
                  "Het gesprek stopte: %s",
                  detail ? `${code} — ${detail}` : code
              )
    );
}


registerComposerAction("daadit-voice-dictate", {
    sequenceQuick: 24,
    sequence: 24,
    condition: ({ composer, owner }) =>
        Boolean(
            channelIdOf(composer) &&
            !composer.message &&
            owner.daaditVoiceState?.available &&
            !owner.daaditVoiceState?.conversing
        ),
    icon: ({ owner }) =>
        owner.daaditVoiceState?.dictating
            ? "fa fa-circle text-danger"
            : "fa fa-pencil-square-o",
    btnClass: ({ owner }) =>
        owner.daaditVoiceState?.dictating ? "o-daadit-voice-active" : "",
    name: () =>
        _t(
            "Eenmalig inspreken — zet je woorden in het invoerveld, zonder te versturen. Klik om te stoppen."
        ),
    async onSelected({ owner, composer }) {
        const voice = owner.daaditVoice?.service;
        const channelId = channelIdOf(composer);
        if (!voice || !channelId) {
            return;
        }
        if (owner.daaditVoiceState.conversing) {
            return;
        }
        if (owner.daaditVoiceState.dictating) {
            voice.stopConversation();
            owner.daaditVoiceState.dictating = false;
            return;
        }
        const notify = (msg, type = "warning") =>
            owner.env.services.notification.add(msg, { type });
        owner.daaditVoiceState.dictating = true;
        try {
            await voice.startDictation(
                channelId,
                (text) => {
                    const current = (composer.composerText || "").trim();
                    composer.composerText = current ? `${current} ${text}` : text;
                    owner.daaditVoiceState.dictating = false;
                },
                (error) => {
                    owner.daaditVoiceState.dictating = false;
                    onVoiceError({ owner, notify, error });
                }
            );
        } catch (error) {
            owner.daaditVoiceState.dictating = false;
            notify(
                _t(
                    "Eenmalig inspreken kon niet starten: %s",
                    String(error?.message || error)
                )
            );
        }
    },
});

registerComposerAction("daadit-voice-speaker", {
    sequenceQuick: 26,
    sequence: 26,
    condition: ({ composer, owner }) =>
        Boolean(
            channelIdOf(composer) &&
            !composer.message &&
            owner.daaditVoiceState?.available
        ),
    icon: ({ owner, composer }) => {
        const channelId = channelIdOf(composer);
        return owner.daaditVoice?.service.isMuted(channelId)
            ? "fa fa-volume-off text-muted"
            : "fa fa-volume-up";
    },
    btnClass: ({ owner, composer }) =>
        owner.daaditVoice?.service.isMuted(channelIdOf(composer))
            ? "text-muted"
            : "",
    name: ({ owner, composer }) =>
        owner.daaditVoice?.service.isMuted(channelIdOf(composer))
            ? _t("Terugpraten uit — klik om antwoorden weer te laten voorlezen.")
            : _t("Terugpraten aan — klik om antwoorden niet meer voor te laten lezen."),
    onSelected({ owner, composer }) {
        const channelId = channelIdOf(composer);
        const voice = owner.daaditVoice?.service;
        if (voice && channelId) {
            voice.setMuted(channelId, !voice.isMuted(channelId));
        }
    },
});

registerComposerAction("daadit-voice-mic", {
    // Without a sequenceQuick the action lands in the overflow menu,
    // where nobody looks for a microphone. 25 puts it just left of the
    // send button, next to the emoji picker.
    sequenceQuick: 25,
    sequence: 25,
    condition: ({ composer, owner }) =>
        Boolean(
            channelIdOf(composer) &&
            !composer.message &&
            owner.daaditVoiceState?.available &&
            !owner.daaditVoiceState?.dictating
        ),
    icon: ({ owner }) =>
        owner.daaditVoiceState?.conversing || owner.daaditVoiceState?.talking
            ? "fa fa-microphone o-daadit-voice-listening"
            : "fa fa-microphone",
    btnClass: ({ owner }) =>
        owner.daaditVoiceState?.conversing || owner.daaditVoiceState?.talking
            ? "o-daadit-voice-active text-danger"
            : "",
    isActive: ({ owner }) => Boolean(owner.daaditVoiceState?.conversing),
    name: ({ owner }) =>
        owner.daaditVoiceState?.talking
            ? _t(
                  "Aan het luisteren — laat %s los om te versturen",
                  owner.daaditVoiceState?.hotkey || "alt+shift"
              )
            : owner.daaditVoiceState?.conversing
            ? _t("Doorlopend gesprek loopt — klik om te stoppen")
            : _t(
                  "Start gesprek. Of houd %s ingedrukt om te praten; " +
                  "dubbeltik voor een doorlopend gesprek.",
                  owner.daaditVoiceState?.hotkey || "alt+shift"
              ),
    async onSelected({ owner, composer }) {
        const voice = owner.daaditVoice?.service;
        const channelId = channelIdOf(composer);
        const notify = (msg, type = "warning") =>
            owner.env.services.notification.add(msg, { type });
        if (!voice || !channelId) {
            return;
        }

        // Second click closes the conversation.
        if (owner.daaditVoiceState.conversing) {
            voice.stopConversation();
            owner.daaditVoiceState.conversing = false;
            owner.daaditVoiceState.handsFree = false;
            owner.daaditVoiceState.talking = false;
            return;
        }

        // Clicking the button is the hands-free route: someone who wants
        // to hold a key does not reach for the mouse first.
        const started = await openConversation({
            owner, composer, channelId, notify, pushToTalk: false,
        });
        if (started) {
            owner.daaditVoiceState.handsFree = true;
        }
    },
    setup({ owner }) {
        const voice = useService("daadit_voice");
        owner.daaditVoiceState = useState({
            conversing: false,
            available: voice.canListen,
            talking: false,
            handsFree: false,
            dictating: false,
            hotkey: "alt+shift",
        });
        owner.daaditVoice = { service: voice };

        const notify = (msg, type = "warning") =>
            owner.env.services.notification.add(msg, { type });

        let pressedAt = 0;
        let lastTapAt = 0;
        let engaged = false;
        const initialChannelId = channelIdOf(composerOf(owner));
        if (initialChannelId) {
            voice.getConfig(initialChannelId).then((config) => {
                owner.daaditVoiceState.hotkey = normalizedTalkCombo(
                    config.ptt_hotkey
                );
            });
        }

        async function onKeyDown(ev) {
            // Only the modifiers themselves, and only once per hold:
            // the browser repeats keydown while a key is held.
            const composer = composerOf(owner);
            const channelId = channelIdOf(composer);
            if (
                !channelId ||
                !owner.daaditVoiceState.available ||
                owner.daaditVoiceState.dictating
            ) {
                return;
            }
            const combo = parseTalkCombo(owner.daaditVoiceState.hotkey);
            if (!isTalkCombo(ev, combo)) {
                return;
            }
            ev.preventDefault();
            if (engaged) {
                return;
            }
            engaged = true;
            pressedAt = Date.now();

            // Hands-free already running: the key interrupts the agent
            // (barge-in by intent) and a short double-tap still toggles
            // hands-free off on keyup.
            if (owner.daaditVoiceState.handsFree) {
                voice.stopSpeaking();
                return;
            }
            if (!owner.daaditVoiceState.conversing) {
                const started = await openConversation({
                    owner, composer, channelId, notify, pushToTalk: true,
                });
                // Key may have been released during getUserMedia /
                // calibration (~700ms). Beginning a push after that
                // recorded silence until the next keyup.
                if (!started || !engaged) {
                    engaged = false;
                    owner.daaditVoiceState.talking = false;
                    return;
                }
            }
            if (!engaged) {
                return;
            }
            if (voice.beginPushToTalk()) {
                owner.daaditVoiceState.talking = true;
            }
        }

        function onKeyUp(ev) {
            if (!engaged) {
                return;
            }
            const combo = parseTalkCombo(owner.daaditVoiceState.hotkey);
            if (!isTalkComboRelease(ev, combo)) {
                return;
            }
            engaged = false;
            const held = Date.now() - pressedAt;
            if (owner.daaditVoiceState.handsFree) {
                maybeToggle(held);
                return;
            }
            // Too short to be speech: throw the fragment away rather than
            // posting a burst of room noise.
            if (owner.daaditVoiceState.talking || voice.state?.listening) {
                voice.endPushToTalk(held >= TALK_TAP_MS);
            }
            owner.daaditVoiceState.talking = false;
            maybeToggle(held);
        }

        function maybeToggle(held) {
            if (held >= TALK_TAP_MS) {
                lastTapAt = 0;
                return;
            }
            const now = Date.now();
            if (lastTapAt && now - lastTapAt <= TALK_DOUBLE_MS) {
                lastTapAt = 0;
                toggleHandsFree();
                return;
            }
            lastTapAt = now;
        }

        async function toggleHandsFree() {
            const composer = composerOf(owner);
            const channelId = channelIdOf(composer);
            if (!channelId) {
                return;
            }
            if (owner.daaditVoiceState.handsFree) {
                voice.stopConversation();
                owner.daaditVoiceState.handsFree = false;
                owner.daaditVoiceState.conversing = false;
                owner.daaditVoiceState.talking = false;
                notify(_t("Doorlopend gesprek uit."), "info");
                return;
            }
            // Switching modes means reopening the microphone: the
            // recorder is started paused for push-to-talk and listening
            // for hands-free, and that is decided at start.
            voice.stopConversation();
            owner.daaditVoiceState.conversing = false;
            const started = await openConversation({
                owner, composer, channelId, notify, pushToTalk: false,
            });
            if (started) {
                owner.daaditVoiceState.handsFree = true;
                notify(
                    _t(
                        "Doorlopend gesprek aan — praat maar. Dubbeltik " +
                        "Option+Shift om te stoppen."
                    ),
                    "info"
                );
            }
        }

        onMounted(() => {
            document.addEventListener("keydown", onKeyDown, true);
            document.addEventListener("keyup", onKeyUp, true);
        });
        onWillUnmount(() => {
            document.removeEventListener("keydown", onKeyDown, true);
            document.removeEventListener("keyup", onKeyUp, true);
            if (owner.daaditVoiceState.conversing) {
                voice.stopConversation();
            }
        });
    },
});

/**
 * Channels whose current turn was started by speaking. The message patch
 * reads this to decide whether to read a reply out loud, so a typed
 * conversation never suddenly starts talking.
 */
const voiceTurns = new Set();

export function markVoiceTurn(channelId) {
    voiceTurns.add(channelId);
}

export function consumeVoiceTurn(channelId) {
    if (voiceTurns.has(channelId)) {
        voiceTurns.delete(channelId);
        return true;
    }
    return false;
}
