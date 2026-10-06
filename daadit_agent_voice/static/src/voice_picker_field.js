/** @odoo-module **/

/**
 * Field widget for `daadit_voice_name`.
 *
 * The installed voices live in the browser, not in the database, so a
 * plain Selection field cannot know them. This widget lists what *this*
 * machine actually has, filtered to the agent's language, and lets you
 * hear a voice before committing to it.
 *
 * Leaving it on "Automatisch" keeps the value empty, which is the
 * portable choice: colleagues on another operating system then get the
 * best local match for the configured language and gender instead of a
 * voice name that does not exist for them.
 */

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, useState } from "@odoo/owl";

export class VoicePickerField extends Component {
    static template = "daadit_agent_voice.VoicePickerField";
    static props = { ...standardFieldProps };

    setup() {
        this.voice = useService("daadit_voice");
        this.notification = useService("notification");
        this.state = useState({ voices: [], loading: true });
        onWillStart(async () => {
            const voices = await this.voice.getVoices();
            this.state.voices = voices.map((v) => ({
                name: v.name,
                lang: v.lang,
                gender: this.voice.guessGender(v),
            }));
            this.state.loading = false;
        });
    }

    /** Voices matching the agent's configured language, best first. */
    get relevantVoices() {
        const lang = (this.props.record.data.daadit_voice_lang || "nl-NL")
            .toLowerCase();
        const base = lang.split("-")[0];
        const scored = this.state.voices.map((v) => {
            const vlang = (v.lang || "").toLowerCase();
            let score = 2;
            if (vlang === lang) {
                score = 0;
            } else if (vlang.startsWith(base)) {
                score = 1;
            }
            return { ...v, score };
        });
        return scored.sort(
            (a, b) => a.score - b.score || a.name.localeCompare(b.name)
        );
    }

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    onChange(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value || false });
    }

    labelFor(voice) {
        const gender =
            voice.gender === "female"
                ? _t("vrouwelijk")
                : voice.gender === "male"
                ? _t("mannelijk")
                : _t("onbekend");
        const local = voice.score === 2 ? ` — ${voice.lang}` : "";
        return `${voice.name} (${gender})${local}`;
    }

    /** Speak a sample line with the settings as they are right now. */
    async onTest() {
        if (!this.voice.canSpeak) {
            this.notification.add(
                _t("Deze browser kan geen tekst voorlezen."),
                { type: "warning" }
            );
            return;
        }
        const data = this.props.record.data;
        const name = data.name || _t("je AI-collega");
        const sample = _t(
            "Hoi, ik ben %s. Zo klink ik als we samen aan het werk zijn.",
            name
        );
        const config = {
            enabled: true,
            lang: data.daadit_voice_lang || "nl-NL",
            gender: data.daadit_voice_gender || "any",
            voice_name: this.value,
            rate: data.daadit_voice_rate || 1,
            pitch: data.daadit_voice_pitch || 1,
            handsfree: false,
        };
        const synth = window.speechSynthesis;
        synth.cancel();
        const utterance = new SpeechSynthesisUtterance(sample);
        const picked = await this.voice.pickVoice(config);
        if (picked) {
            utterance.voice = picked;
        }
        utterance.lang = config.lang;
        utterance.rate = Math.min(Math.max(config.rate, 0.5), 2);
        utterance.pitch = Math.min(Math.max(config.pitch, 0.5), 2);
        synth.speak(utterance);
    }
}

registry.category("fields").add("daadit_voice_picker", {
    component: VoicePickerField,
    displayName: _t("Stemkiezer"),
    supportedTypes: ["char"],
});
