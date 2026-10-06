/** @odoo-module **/

/**
 * Read the agent's reply out loud — but only when the user actually
 * spoke. A typed conversation stays silent, and scrolling back through
 * history never re-triggers speech: we remember which messages have
 * been handled and only ever consider ones that arrive after mount.
 */

import { patch } from "@web/core/utils/patch";
import { Message } from "@mail/core/common/message";
import { useService } from "@web/core/utils/hooks";
import { onMounted } from "@odoo/owl";
import { consumeVoiceTurn } from "./composer_voice_action";

/** Message ids we already spoke (or deliberately skipped). */
const handled = new Set();

/** Strip markup so the speech engine reads prose, not tags. */
function toPlainText(html) {
    if (!html) {
        return "";
    }
    const el = document.createElement("div");
    el.innerHTML = html;
    // Drop code blocks and links' href noise — they read terribly.
    el.querySelectorAll("pre, code").forEach((node) => node.remove());
    return (el.textContent || "").replace(/\s+/g, " ").trim();
}

patch(Message.prototype, {
    setup() {
        super.setup(...arguments);
        try {
            this.daaditVoice = useService("daadit_voice");
            onMounted(() => this._daaditMaybeSpeak());
        } catch {
            // Voice is a nicety bolted onto every single message in the
            // app. If anything here misbehaves the chat must still
            // render, so we swallow rather than propagate.
            this.daaditVoice = null;
        }
    },

    _daaditMaybeSpeak() {
        try {
            this._daaditSpeakInner();
        } catch {
            // A silent reply is an inconvenience; a crashed message
            // list is a broken Odoo.
        }
    },

    _daaditSpeakInner() {
        if (!this.daaditVoice) {
            return;
        }
        const message = this.props.message;
        if (!message || handled.has(message.id)) {
            return;
        }
        handled.add(message.id);
        const thread = message.thread;
        if (!thread || thread.model !== "discuss.channel") {
            return;
        }
        // Only the agent's own words, never our own message coming back.
        if (message.isSelfAuthored) {
            return;
        }
        // Speak only when the user is actually talking to this agent:
        // either an open conversation, or a single spoken question.
        const conversing = this.daaditVoice.isConversing?.(thread.id);
        if (this.daaditVoice.isMuted?.(thread.id)) {
            return;
        }
        // One-off dictation only fills the input; it never speaks back.
        if (conversing && this.daaditVoice.state?.dictating) {
            return;
        }
        if (!conversing && !consumeVoiceTurn(thread.id)) {
            return;
        }
        consumeVoiceTurn(thread.id);
        const text = toPlainText(message.body);
        if (text) {
            this.daaditVoice.speak(text, thread.id);
        }
    },
});
