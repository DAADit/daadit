# -*- coding: utf-8 -*-
"""Zichtbare denkstappen in de chat — gedeelde emit-laag.

Eén plek die de korte, mensvriendelijke voortgangsregels ("denkstappen")
samenstelt en over de bus naar de chattende gebruiker duwt, voor *elke*
provider (Mistral, Loes, Claude). Zonder deze laag had elke provider zijn
eigen kopie van de labels en het bus-verkeer nodig; nu roepen ze allemaal
dezelfde functies aan.

Wat de gebruiker ziet
---------------------
Tijdens een agent-antwoord verschijnt niet één vaste "AI is thinking…",
maar een *groeiende, terugleesbare lijst* van korte regels: "Ik zoek de
gegevens erbij", "Ik leg dit voor aan Sem", "Sem heeft geantwoord, ik
verwerk het". Bij een doorgerouteerde vraag komen de stappen van de
collega ingesprongen onder de hoofdstap (via ``depth``).

VEILIGHEID (weegt zwaarder dan de feature)
------------------------------------------
Een denkstap wordt **altijd** uit een vaste labelset gebouwd
(:data:`STEP_LABELS` / :func:`label_for_tool_call`), **nooit** uit
modeltekst, ruwe tool-argumenten, tool-resultaten, promptfragmenten of
sub-run-JSON. De enige variabele die in een label terechtkomt is een
**agent-/collega-naam** (bijv. "Sem") — een interne agentnaam, geen PII,
en wat de agent zelf al ondertekent. Zo kan het "interne verkeer" dat de
antwoord-sanitizer in ``daadit_ai_mistral`` weghaalt, hier per definitie
niet in een stap belanden.

Waarom een eigen, direct-committende cursor
-------------------------------------------
Bus-notificaties worden bij commit verstuurd en een hele chatbeurt is één
transactie. Op de hoofdcursor zouden alle regels pas ná het antwoord in
één keer aankomen — nutteloos. Een kortlevende eigen cursor commit meteen,
zodat de regel verschijnt terwijl de agent nog werkt. Alles hier is
best-effort: een fout in de voortgangsmelding mag nooit een chatbeurt
breken.
"""
import json
import logging
import re
import threading
import uuid

_logger = logging.getLogger(__name__)

# Bus-type waar de frontend (daadit_agent_voice) op abonneert.
BUS_TYPE = "daadit_agent_step"

# De routing-tool ("AI: Ask Agent") heeft in elke provider dezelfde slug.
# Los gehouden van de providermodules zodat deze helper niets van een
# specifieke provider hoeft te importeren.
ROUTER_TOOL_SLUG = "ir_actions_server_ask_agent"
OPEN_CHAT_TOOL_SLUG = "ir_actions_server_open_agent_chat"

# Een agent-/collega-naam is het enige stukje variabele tekst dat in een
# label mag. We laten daarom alleen iets door dat er als een naam uitziet:
# één woord dat met een hoofdletter begint (de agents heten Robin, Sem,
# Nova …), letters (incl. accenten), koppelteken of apostrof, maximaal 24
# tekens. Alles daarbuiten (JSON, een zin, cijfers, een e-mailadres, een
# kleingeschreven woord als "de") valt terug op de naamloze variant, zodat
# modeltekst of PII nooit via dit veld in de chat kan lekken.
_NAME_RE = re.compile(r"^[^\W\d_][\w'-]{0,23}$", re.UNICODE)

# Per-thread staat: het huidige turn-id en een oplopende volgnummer, zodat
# de frontend de stappen van één antwoord kan groeperen en op volgorde kan
# tonen. Een sub-run (depth > 0) erft het turn-id van zijn ouder omdat hij
# op dezelfde thread draait; alleen een top-level beurt begint een nieuwe.
_state = threading.local()


# Mensvriendelijke labels per tool-slug. Alles wat hier niet in staat valt
# terug op een ge-de-slugde naam (zie :func:`label_for_tool_call`). Nooit
# een tool-resultaat of -argument.
STEP_LABELS = {
    "ir_actions_server_search": "Ik zoek de gegevens erbij",
    "ir_actions_server_read_group": "Ik tel de cijfers bij elkaar",
    "ir_actions_server_get_fields": "Ik kijk welke velden er bestaan",
    "ir_actions_server_search_knowledge": "Ik zoek het op in de kennisbank",
    "ir_actions_server_schedule_activity": "Ik zet een taak klaar",
    "ir_actions_server_assign_user": "Ik wijs het toe",
    "ir_actions_server_create_draft_blogpost_blogposter": (
        "Ik schrijf het concept-artikel"
    ),
    "ir_actions_server_campaign_calendar_marketing_office": (
        "Ik kijk in de campagnekalender"
    ),
    "ir_actions_server_haal_claude_design": (
        "Ik haal de huisstijl uit Claude Design"
    ),
    "ir_actions_server_ai_marketing_haal_claude_design": (
        "Ik haal de huisstijl uit Claude Design"
    ),
}


def begin_turn(turn_id=None):
    """Begin een nieuwe chatbeurt op deze thread en geef het turn-id terug.

    Aan te roepen aan het begin van een *top-level* run (depth 0). Reset
    het volgnummer. Sub-runs roepen dit NIET aan — die erven het turn-id.

    Providers houden zelf al één uuid per top-level beurt bij
    (``router_state.turn_uuid``); geef dat mee als ``turn_id`` zodat "wat
    je live ziet" en "wat er in de usage-/run-administratie staat" hetzelfde
    id dragen. Zonder argument genereren we er zelf één.
    """
    _state.turn_id = turn_id or uuid.uuid4().hex
    _state.seq = 0
    return _state.turn_id


def end_turn():
    """Sluit de huidige beurt af (best-effort; puur opruimen)."""
    _state.turn_id = None
    _state.seq = 0


def current_turn_id():
    return getattr(_state, "turn_id", None)


def _resolve_turn_id(turn_id):
    """Bepaal het turn-id voor deze stap en reset het volgnummer wanneer we
    aan een nieuwe beurt beginnen. Sub-runs (zelfde ``turn_id``) tellen door
    op hetzelfde volgnummer, zodat de frontend ze op volgorde onder de
    hoofdstappen zet."""
    if turn_id and turn_id != getattr(_state, "turn_id", None):
        begin_turn(turn_id)
    elif not getattr(_state, "turn_id", None):
        begin_turn(turn_id)
    return _state.turn_id


def _next_seq():
    seq = getattr(_state, "seq", 0) + 1
    _state.seq = seq
    return seq


def _deslug(name):
    """``ir_actions_server_do_thing`` -> ``do thing``. Alleen de toolnaam,
    geen argumenten — die zijn nooit veilig om te tonen."""
    return (name or "").replace("ir_actions_server_", "").replace("_", " ").strip()


def label_for_tool_call(tool_call):
    """Vaste, PII-vrije NL-regel voor een op handen zijnde tool-aanroep.

    Leest **alleen** de toolnaam en — uitsluitend voor de routing-tool en
    de ``ask_<naam>``-tools — de agent-/collega-naam. Nooit vrije
    modeltekst of een tool-resultaat.
    """
    fn = (tool_call or {}).get("function") or {}
    name = fn.get("name") or ""
    if name == ROUTER_TOOL_SLUG:
        wie = _router_target_name(fn.get("arguments"))
        return (
            "Ik leg dit voor aan %s" % wie if wie
            else "Ik leg dit voor aan een collega"
        )
    if name == OPEN_CHAT_TOOL_SLUG:
        wie = _router_target_name(fn.get("arguments"))
        return (
            "Ik open een chat met %s" % wie if wie
            else "Ik open een chat met een collega"
        )
    if name.startswith("ir_actions_server_ask_"):
        rest = name[len("ir_actions_server_ask_"):]
        wie = rest.split("_")[0]
        if wie:
            return "Ik leg dit voor aan %s" % wie.capitalize()
    if name in STEP_LABELS:
        return STEP_LABELS[name]
    leesbaar = _deslug(name)
    return "Ik voer '%s' uit" % (leesbaar or "een actie")


def safe_agent_name(raw):
    """Geef ``raw`` alleen terug als het er als een agent-naam uitziet.

    De vangrail voor de enige variabele die in een label mag. Neemt het
    eerste woord en accepteert het alleen als het op :data:`_NAME_RE`
    past; anders een lege string. Zo kan vrije modeltekst, JSON of PII
    nooit als zichtbare naam doorlekken.
    """
    first = (raw or "").strip().split(" ")[0] if raw else ""
    if not first or not first[:1].isupper() or not _NAME_RE.match(first):
        return ""
    return first


def _router_target_name(arguments):
    """Haal alleen de collega-naam uit de router-argumenten. Faalt dit,
    dan geen naam — nooit de ruwe argumenten teruggeven."""
    try:
        args = json.loads(arguments or "{}")
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(args, dict):
        return ""
    return safe_agent_name(args.get("agent_name"))


def build_payload(agent, text, *, turn_id=None, depth=0, kind="think",
                  done=False):
    """Bouw de bus-payload voor één denkstap (zonder te versturen).

    Losgetrokken van :func:`emit` zodat de labels/volgorde/velden zonder
    database of bus getest kunnen worden.
    """
    return {
        "turn_id": _resolve_turn_id(turn_id),
        "seq": _next_seq(),
        "text": text,
        "agent": (agent.name or "") if agent is not None else "",
        "depth": int(depth or 0),
        "kind": kind,
        "done": bool(done),
    }


def emit(agent, text, *, turn_id=None, depth=0, kind="think", done=False):
    """Duw één denkstap naar de chattende gebruiker. Best-effort.

    :param agent: het ``ai.agent``-record dat aan het werk is.
    :param text: een regel uit de vaste labelset (zie moduledoc). Wordt
        NIET uit modeltekst opgebouwd.
    :param turn_id: het uuid van deze chatbeurt (meestal
        ``router_state.turn_uuid``). Sub-runs geven hetzelfde id mee.
    :param depth: 0 voor de hoofd-agent, >0 voor een doorgerouteerde
        sub-run (de frontend springt die in).
    :param kind: ``tool`` | ``route`` | ``think`` | ``done`` — puur voor
        de frontend, geen inhoud.
    :param done: ``True`` op de afsluitende regel van de beurt.
    """
    if not agent or not text:
        return
    try:
        import odoo
        from odoo import api, SUPERUSER_ID
        env = agent.env
        user = env.user
        # Alleen interactieve chats. Een cron-run heeft geen kijker en
        # OdooBot (uid 1) is nooit een wachtende persoon.
        if not user or user.id == 1 or not user.partner_id:
            return
        payload = build_payload(
            agent, text, turn_id=turn_id, depth=depth, kind=kind, done=done,
        )
        partner_id = user.partner_id.id
        dbname = env.cr.dbname
        with odoo.registry(dbname).cursor() as cr2:
            env2 = api.Environment(cr2, SUPERUSER_ID, {})
            partner = env2["res.partner"].browse(partner_id)
            env2["bus.bus"]._sendone(partner, BUS_TYPE, payload)
            cr2.commit()
    except Exception:  # noqa: BLE001
        _logger.debug(
            "daadit_ai_agent_schedule.agent_steps: denkstap niet bezorgd",
            exc_info=True,
        )


def emit_tool_step(agent, tool_call, *, turn_id=None, depth=0):
    """Gemak: bouw het vaste label voor ``tool_call`` en zend het uit."""
    emit(agent, label_for_tool_call(tool_call), turn_id=turn_id,
         depth=depth, kind="tool")
