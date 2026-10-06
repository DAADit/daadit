/**
 * Zichtbare, terugleesbare denkstappen in de AI-chat.
 *
 * Odoo toont tijdens een agent-antwoord de vaste tekst "AI is thinking...".
 * Bij een vraag die langs twee of drie collega's gaat, staat die er soms
 * veertig seconden of langer — en dat leest als vastgelopen, niet als werk.
 *
 * De server stuurt nu per stap een korte regel over de bus, met een
 * ``turn_id`` (welk antwoord), een ``seq`` (volgorde) en een ``depth``
 * (0 = de hoofd-agent, >0 = een doorgerouteerde collega). We verzamelen de
 * stappen van de lopende beurt en tonen ze als een *groeiende lijst* in
 * plaats van één regel die de vorige overschrijft.
 *
 * Losgekoppeld van het ``Typing``-component: de lijst en zijn data leven in
 * een eigen service + component. ``Typing`` wordt niet meer gepatcht, maar
 * blijft de nette terugval wanneer er (nog) geen denkstap is of wanneer een
 * mens aan het typen is.
 *
 * Valt er niets binnen, dan blijft het gedrag exact zoals het was.
 */
import { Component } from "@odoo/owl";
import { reactive, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { Typing } from "@mail/discuss/typing/common/typing";
import { Composer } from "@mail/core/common/composer";

// Na deze tijd beschouwen we een beurt als achterhaald. Zonder deze grens
// zou de laatste regel van een vorig gesprek nog even oplichten aan het
// begin van het volgende — precies het soort verwarring dat we oplossen.
const MAX_AGE_MS = 120000;

// Hoeveel afgeronde beurten we in het geheugen bewaren zodat ze
// terugleesbaar blijven zonder meteen de server te bevragen. Klein: de
// server (route /daadit_agent_steps/replay) is de bron van waarheid en
// heeft alles uit de audit, dit is enkel een cache voor het laatste
// handjevol beurten in dit tabblad.
const HISTORY_MAX = 20;

const spokenStepState = { turnId: null, spoken: 0 };

// De reactive state die de service teruggeeft en het component leest. Eén
// beurt tegelijk *live*; afgeronde beurten blijven in ``history`` staan
// zodat ze terugleesbaar zijn.
const state = reactive({
    turnId: null,
    agent: "",
    steps: [], // [{ seq, text, depth, kind }]
    done: false,
    at: 0,
    // turn_id -> { agent, steps: [{ seq, text, depth, kind }] }. Groeit
    // mee met de live beurt en blijft staan na afloop (terugleesbaar).
    history: {},
});

// Bewaar de stappen van een beurt onder zijn id, en snoei de oudste weg
// zodra het er te veel worden. Los van de reactive ``steps`` zodat een
// afgeronde beurt niet verdwijnt wanneer de volgende begint.
function remember(turnId, agent, steps) {
    if (!turnId) {
        return;
    }
    state.history[turnId] = { agent: agent || "", steps: steps.slice() };
    const ids = Object.keys(state.history);
    if (ids.length > HISTORY_MAX) {
        delete state.history[ids[0]];
    }
}

function resetTurn(payload) {
    state.turnId = payload.turn_id || null;
    state.agent = payload.agent || "";
    state.steps = [];
    state.done = false;
    spokenStepState.turnId = state.turnId;
    spokenStepState.spoken = 0;
}

/**
 * Geef de terugleesbare denkstappen van beurt ``turnId``. Eerst uit het
 * geheugen (de live beurt of een recent afgeronde), anders uit de audit
 * via de server. De servervariant is dezelfde bron als het runrapport,
 * dus wat je terugleest kan niet afwijken van wat de agent echt deed.
 *
 * Faalt de server (offline, geen eigen beurt met dat id), dan geven we
 * terug wat we nog in het geheugen hebben — nooit een foutmelding in de
 * chat.
 */
async function fetchReplay(turnId) {
    if (!turnId) {
        return { agent: "", steps: [] };
    }
    if (turnId === state.turnId && state.steps.length) {
        return { agent: state.agent, steps: state.steps.slice() };
    }
    if (state.history[turnId]) {
        return state.history[turnId];
    }
    try {
        const res = await rpc("/daadit_agent_steps/replay", { turn_id: turnId });
        if (res && res.ok && Array.isArray(res.steps)) {
            remember(turnId, res.agent, res.steps);
            return { agent: res.agent || "", steps: res.steps };
        }
    } catch {
        // Best-effort: bij een fout houden we het bij wat we hebben.
    }
    return state.history[turnId] || { agent: "", steps: [] };
}

export const agentStepsService = {
    dependencies: ["bus_service", "daadit_voice"],
    start(env, { bus_service, daadit_voice }) {
        bus_service.subscribe("daadit_agent_step", (payload) => {
            if (!payload) {
                return;
            }
            const turnId = payload.turn_id || null;
            // Nieuwe beurt (of de eerste): begin met een schone lijst.
            if (turnId !== state.turnId) {
                resetTurn(payload);
            }
            if (payload.agent) {
                state.agent = payload.agent;
            }
            state.at = Date.now();

            // De afsluitende markering ("done") is geen leesbare stap; hij
            // zet alleen de "bezig"-status uit. We bewaren de afgeronde
            // beurt wel, zodat ze terugleesbaar blijft.
            if (payload.done || payload.kind === "done") {
                state.done = true;
                remember(state.turnId, state.agent, state.steps);
                return;
            }
            const text = payload.text || "";
            if (!text) {
                return;
            }
            const seq = Number.isFinite(payload.seq) ? payload.seq : state.steps.length;
            // Dedupe op volgnummer: de bus levert normaal at-most-once, maar
            // een herverbinding kan een regel herhalen.
            if (state.steps.some((s) => s.seq === seq)) {
                return;
            }
            state.steps.push({
                seq,
                text,
                depth: payload.depth || 0,
                kind: payload.kind || "think",
            });
            state.steps.sort((a, b) => a.seq - b.seq);
            // Houd de terugleesbare kopie gelijk met de live lijst, zodat
            // de stappen niet verdwijnen zodra de volgende beurt begint.
            remember(state.turnId, state.agent, state.steps);
            if (
                !payload.depth
                && payload.kind !== "done"
                && spokenStepState.spoken < 2
            ) {
                spokenStepState.spoken += 1;
                try {
                    daadit_voice?.speakStatus?.(text);
                } catch {
                    // Een optionele voice-service mag de zichtbare stappen
                    // nooit blokkeren.
                }
            }
        });
        // ``fetchReplay`` op de reactive state zodat een component (nu of
        // later, bij het openen van een afgerond bericht) de stappen kan
        // terughalen — uit het geheugen of uit de audit via de server.
        state.fetchReplay = fetchReplay;
        return state;
    },
};

registry.category("services").add("daadit.agent_steps", agentStepsService);

/**
 * Toont de denkstappen-lijst van de lopende beurt. Valt terug op het
 * standaard ``Typing``-component zodra er (nog) geen stap is, zodat de
 * gewone "… is typing"-indicator voor mensen ongemoeid blijft.
 */
export class AgentSteps extends Component {
    static template = "daadit_agent_voice.AgentSteps";
    static components = { Typing };
    static props = {
        channel: { type: Object, optional: true },
        size: { type: String, optional: true },
    };

    setup() {
        // useState abonneert dit component op de reactive, zodat een
        // binnenkomende regel meteen een hertekening geeft.
        this.steps = useState(useService("daadit.agent_steps"));
    }

    /**
     * Tonen zolang de andere kant (de agent) "typt" én er een verse stap
     * is. We hangen bewust aan ``hasOtherMembersTyping`` — precies de
     * conditie waarop het originele ``Typing`` in deze Composer-slot
     * verscheen — zodat de indicator net zo kortstondig is als voorheen en
     * we geen ongedocumenteerde modelvelden hoeven te raden.
     */
    get active() {
        if (!this.steps.steps.length) {
            return false;
        }
        if (Date.now() - (this.steps.at || 0) >= MAX_AGE_MS) {
            return false;
        }
        return this.othersTyping;
    }

    get othersTyping() {
        const channel = this.props.channel;
        return !!(channel && channel.hasOtherMembersTyping);
    }

    get visibleSteps() {
        return this.steps.steps;
    }

    isLast(step) {
        const arr = this.steps.steps;
        return arr.length && arr[arr.length - 1].seq === step.seq;
    }

    /** Sub-run-stappen (depth > 0) springen in onder de hoofdstap. */
    indentStyle(step) {
        const depth = Math.min(step.depth || 0, 3);
        return depth ? `padding-left:${depth * 0.9}rem` : "";
    }
}

// Losgekoppeld van Typing: we patchen het Typing-component niet, we
// registreren ons eigen component op de Composer (zelfde idioom als mail
// zelf gebruikt voor Typing) zodat de template het kan tonen.
patch(Composer, {
    components: { ...Composer.components, AgentSteps },
});
