/** @odoo-module **/

/**
 * Field widget for `daadit_elevenlabs_voice_id`.
 *
 * Pulls the voices out of the configured ElevenLabs account so an
 * operator picks a name instead of pasting a 20-character id, and plays
 * ElevenLabs' own sample so you can hear a voice before assigning it —
 * previewing through our own endpoint would spend credits on every
 * click.
 */

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, useState } from "@odoo/owl";

export class ElevenLabsPickerField extends Component {
    static template = "daadit_agent_voice.ElevenLabsPickerField";
    static props = { ...standardFieldProps };

    setup() {
        this.notification = useService("notification");
        this.state = useState({ voices: [], loading: true, error: null });
        this.preview = null;
        onWillStart(() => this.loadVoices());
    }

    async loadVoices() {
        this.state.loading = true;
        let result = { ok: false };
        try {
            result = await rpc("/daadit_voice/voices", {});
        } catch {
            result = { ok: false, error: "request_failed" };
        }
        this.state.loading = false;
        if (result.ok) {
            this.state.voices = result.voices || [];
            this.state.error = null;
        } else {
            this.state.voices = [];
            this.state.error =
                result.error === "no_key"
                    ? _t(
                          "Nog geen ElevenLabs-sleutel ingesteld. Vul die in " +
                          "bij Instellingen → Praten met AI-agents."
                      )
                    : _t("Kon de stemmen niet ophalen bij ElevenLabs.");
        }
    }

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    onChange(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value || false });
    }

    labelFor(voice) {
        const labels = voice.labels || {};
        const bits = [labels.gender, labels.accent, labels.age].filter(Boolean);
        return bits.length ? `${voice.name} — ${bits.join(", ")}` : voice.name;
    }

    /** Play ElevenLabs' own preview clip — costs no credits. */
    onPreview() {
        const voice = this.state.voices.find((v) => v.voice_id === this.value);
        if (!voice?.preview_url) {
            this.notification.add(
                _t("Voor deze stem is geen voorbeeldfragment beschikbaar."),
                { type: "info" }
            );
            return;
        }
        this.preview?.pause();
        this.preview = new Audio(voice.preview_url);
        this.preview.play().catch(() => {
            this.notification.add(_t("Afspelen lukte niet."), {
                type: "warning",
            });
        });
    }
}

registry.category("fields").add("daadit_elevenlabs_picker", {
    component: ElevenLabsPickerField,
    displayName: _t("ElevenLabs-stemkiezer"),
    supportedTypes: ["char"],
});
