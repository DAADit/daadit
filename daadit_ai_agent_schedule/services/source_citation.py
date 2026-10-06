# -*- coding: utf-8 -*-
"""Bronvermelding bij kennis: zonder bron geen bewering (taak 1485).

De feitenregel (``claim_ledger``) toetst handelingen aan tool-acties.
Dit is dezelfde regel voor kennis: een feit uit de kennisbronnen van
een collega staat in het antwoord met ``[bron: <naam>]`` erachter, en
die naam moet een bron zijn die de collega in deze run werkelijk heeft
opgezocht. Een genoemde bron die niet geraadpleegd is, telt als
verzonnen.

Puur: geen ORM, zodat de toets zonder database te testen is.
"""
import json
import re

CITE = re.compile(r"\[bron:\s*([^\[\]\n]{1,160}?)\s*\]", re.IGNORECASE)

KNOWLEDGE_TOOL = "search_knowledge"

# Tools die bronnen teruggeven in dezelfde vorm als search_knowledge
# (``chunks`` met ``source`` en ``url``).
KNOWLEDGE_TOOLS = (
    KNOWLEDGE_TOOL,
    "similar_tickets",
    "helpdesk_rules",
    "project_overrun",
    "lead_summary",
    "quotations_waiting",
)

# De naam waaronder het inwerkdossier van een plaatsing als bron telt.
MEMORY_SOURCE = "inwerkdossier"

INSTRUCTION = (
    "BRONNEN: haal je een feit uit je kennisbronnen (de tool "
    "search_knowledge), zet dan direct achter die zin [bron: <naam>], "
    "met de naam precies zoals de tool hem teruggaf bij \"source\". Geeft "
    "de tool niets terug, zeg dan dat je bronnen het antwoord niet "
    "bevatten. Noem nooit een bron die je niet hebt opgezocht."
)

RETRY = (
    "Je antwoord gebruikt kennis uit je bronnen zonder juiste "
    "bronvermelding. Schrijf je antwoord opnieuw en zet achter elke zin "
    "die op een bron steunt [bron: <naam>]. Je mag alleen deze bronnen "
    "noemen: %s. Staat iets niet in die bronnen, haal het dan weg. "
    "Gebruik geen tools en voeg geen nieuw werk toe; neem het "
    "```claims-blok ongewijzigd over."
)


def _norm(name):
    return " ".join((name or "").split()).casefold()


def _payload(result):
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


def is_knowledge_tool(tool_name):
    return (tool_name or "").rsplit(".", 1)[-1].endswith(KNOWLEDGE_TOOLS)


def consulted(actions):
    """De bronnen die een run opzocht: ``{naam: url}``, in volgorde.

    ``actions`` zijn de vastgelegde tool-aanroepen (``tool_name`` en
    ``result``); alleen geslaagde zoekacties in de kennisbronnen tellen.
    """
    sources = {}
    for action in actions or ():
        if not is_knowledge_tool(action.get("tool_name")):
            continue
        data = _payload(action.get("result"))
        if not data or not data.get("ok"):
            continue
        for chunk in data.get("chunks") or ():
            if not isinstance(chunk, dict):
                continue
            name = " ".join((chunk.get("source") or "").split())
            if name and name not in sources:
                sources[name] = chunk.get("url") or ""
    return sources


def cited(text):
    """De genoemde bronnen in de tekst, elk één keer, in volgorde."""
    seen = []
    for match in CITE.finditer(text or ""):
        name = " ".join(match.group(1).split())
        if name and name not in seen:
            seen.append(name)
    return seen


def resolve(name, sources):
    """De naam zoals de bron hem draagt, of ``None`` als hij onbekend is."""
    wanted = _norm(name)
    for known in sources or {}:
        if _norm(known) == wanted:
            return known
    return None


def check(text, sources):
    """Toets de bronvermelding van ``text`` tegen ``sources``.

    ``needed``: de run zocht kennis op, dus een bewering daaruit moet
    een bron dragen. ``ok``: geen verzonnen bron, en als er kennis is
    opgezocht en gebruikt, staat er minstens één echte bron bij.
    """
    names = cited(text)
    known = [n for n in names if resolve(n, sources)]
    unknown = [n for n in names if not resolve(n, sources)]
    needed = bool(sources)
    return {
        "needed": needed,
        "cited": names,
        "known": known,
        "unknown": unknown,
        "ok": not unknown and (not needed or bool(known)),
    }
