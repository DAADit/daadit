# -*- coding: utf-8 -*-
"""Beweringen als data: elk "ik heb X gedaan" wijst naar een tool-actie (1.1).

De woordcontrole in ``_compute_claims_unverified`` leest de vrije tekst
en vergelijkt aantallen. Dat vangt veel, maar een agent die het juiste
werkwoord vermijdt, ontsnapt eraan. Hier levert de agent zelf een lijst
beweringen met per bewering de soort (handeling of waarneming) en de
tool-actie die de handeling bewijst. De code toetst die verwijzing; het
model hoeft niets te begrijpen van de controle om eraan onderworpen te
zijn.

Alles in dit bestand is puur: geen ORM, zodat de toets zonder database
te testen is en dezelfde uitkomst geeft in een run en in een replay.
"""
import json
import re

KIND_ACTION = "handeling"
KIND_OBSERVATION = "waarneming"

_KIND_ALIASES = {
    "handeling": KIND_ACTION,
    "action": KIND_ACTION,
    "actie": KIND_ACTION,
    "waarneming": KIND_OBSERVATION,
    "observation": KIND_OBSERVATION,
    "bevinding": KIND_OBSERVATION,
}

INSTRUCTION = (
    "VERANTWOORDING (verplicht, wordt door het systeem gecontroleerd): "
    "sluit je antwoord af met één codeblok ```claims met een JSON-lijst "
    "van je beweringen. Per bewering: \"text\" (de bewering in één zin), "
    "\"kind\" (\"handeling\" als je iets hebt aangemaakt, gewijzigd, "
    "verstuurd of klaargezet; \"waarneming\" als je iets hebt gezien of "
    "nagekeken) en bij een handeling \"action_ref\": het volgnummer van "
    "de tool-aanroep die het deed (1 = je eerste tool-aanroep in deze "
    "run), of \"record\": {\"model\": ..., \"id\": ...} zoals het "
    "toolresultaat het teruggaf. Een handeling zonder geslaagde "
    "tool-aanroep blokkeert je verslag. Niets gedaan? Schrijf dan alleen "
    "waarnemingen. Voorbeeld:\n"
    "```claims\n"
    "[{\"text\": \"Taak 91 aan Jan toegewezen\", \"kind\": \"handeling\", "
    "\"action_ref\": 3},\n"
    " {\"text\": \"Geen achterstallige facturen gezien\", "
    "\"kind\": \"waarneming\"}]\n"
    "```"
)

# ```claims … ``` of ```json … ``` met "claims"/"kind" erin. Mistral laat
# de taal van het codeblok soms weg; die variant telt ook mee, zolang de
# inhoud er een beweringenlijst uitziet.
_FENCE = re.compile(
    r"```[ \t]*(claims|json)?[ \t]*\r?\n(.*?)```", re.S | re.I,
)


def _as_claim_list(payload):
    if isinstance(payload, dict):
        payload = payload.get("claims")
    if not isinstance(payload, list):
        return None
    if not all(isinstance(item, dict) for item in payload):
        return None
    return payload


def split(findings):
    """Haal het beweringenblok uit de tekst.

    Geeft ``(tekst_zonder_blok, beweringen)``. ``beweringen`` is ``None``
    als er geen leesbaar blok was — dan blijft de tekst onaangeroerd en
    beslist alleen de woordcontrole. Het laatste geldige blok wint: een
    agent die zichzelf corrigeert, bedoelt de laatste versie.
    """
    text = findings or ""
    found = None
    span = None
    for match in _FENCE.finditer(text):
        lang = (match.group(1) or "").lower()
        body = match.group(2).strip()
        if lang != "claims" and '"kind"' not in body and '"claims"' not in body:
            continue
        try:
            claims = _as_claim_list(json.loads(body))
        except ValueError:
            claims = None
        if claims is None:
            continue
        found, span = claims, match.span()
    if found is None:
        return text, None
    rest = (text[:span[0]] + text[span[1]:]).strip()
    return rest, found


def _int(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("#").isdigit():
        return int(value.strip().lstrip("#"))
    return 0


def _record_ref(claim):
    rec = claim.get("record")
    if isinstance(rec, dict):
        return (str(rec.get("model") or "").strip(), _int(rec.get("id")))
    if isinstance(rec, str) and "," in rec:
        model, _sep, rid = rec.partition(",")
        return model.strip(), _int(rid)
    return "", 0


def verify(claims, actions, dry_run=False):
    """Toets elke bewering tegen de acties van dezelfde run.

    ``actions`` is een lijst dicts met ``sequence``, ``id``, ``effective``
    (geslaagde, echte schrijfactie), ``error``, ``model`` en ``ref``.
    Een handeling is gedekt als haar ``action_ref`` (volgnummer of id)
    of haar ``record`` naar een effectieve schrijfactie wijst. In een
    dry-run is niets een bewijs: daar is niets geschreven.

    Een onbekende of ontbrekende ``kind`` geldt als handeling. De agent
    kiest de soort; wie hem weglaat, krijgt de strengste lezing.
    """
    by_seq = {a["sequence"]: a for a in actions if a.get("sequence")}
    by_id = {a["id"]: a for a in actions if a.get("id")}
    out = []
    for claim in claims:
        text = str(claim.get("text") or "").strip()[:200]
        kind = _KIND_ALIASES.get(
            str(claim.get("kind") or "").strip().lower(), KIND_ACTION,
        )
        row = {"text": text, "kind": kind, "ok": True, "reason": ""}
        if kind == KIND_OBSERVATION:
            out.append(row)
            continue
        ref = _int(claim.get("action_ref"))
        model, rid = _record_ref(claim)
        action = by_seq.get(ref) or by_id.get(ref) if ref else None
        if action is None and model and rid:
            action = next(
                (a for a in actions
                 if a.get("effective") and a.get("model") == model
                 and a.get("ref") == rid),
                None,
            )
        if dry_run:
            row.update(ok=False, reason="dry-run: niets geschreven")
        elif action is None:
            row.update(ok=False, reason="geen tool-actie gevonden")
        elif action.get("error"):
            row.update(ok=False, reason="tool-actie gaf een fout")
        elif not action.get("effective"):
            row.update(ok=False, reason="tool-actie schreef niets")
        if action is not None:
            row["action"] = action.get("sequence") or 0
        out.append(row)
    return out
