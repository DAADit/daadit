"""Scoring for agent evals.

Pure functions: no Odoo, no network. Everything here takes the observed
outcome of one agent turn and turns it into per-criterion verdicts, so the
rules can be unit-tested without spending a cent on model calls.

A criterion is `pass`, `fail` or `untested`. `untested` matters: if a turn
produced no run record we cannot know which tools were called, and claiming
"pass" there would be the same mistake the agents make when they report work
they did not do.

Twee soorten criteria staan hier naast elkaar:

* **gedrag** — taal, terugvragen, doorverwijzen, schrijfacties, kosten. Dit
  was de oorspronkelijke suite.
* **inhoudelijke kwaliteit** (taak 707) — is het antwoord volledig, volgen de
  cijfers uit de tooluitkomst, beantwoordt het de vraag die gesteld was, en
  staat er geen intern verkeer in. Alles wat hier meetbaar is, is
  deterministisch: aanwezigheid van entiteiten, dekking van onderdelen,
  herkomst van getallen, taal, lengte, verboden patronen. Wat een regel niet
  hard kan vaststellen levert `untested` op, en wat deze suite principieel
  niet meet staat in :data:`LIMITS`.

Een LLM-jury hoort hier niet in: die weegt niet mee in de uitkomst van een
case.

Deze module is de Odoo-kant van het harnas dat als los script in
``daadit_odoo/evals/scoring.py`` staat, en is er een letterlijke kopie van
op één punt na: een meting binnen Odoo draait de beurt in **testmodus**, dus
schrijfacties worden onderschept en gesimuleerd. Wat een schrijfeffect nodig
heeft om vast te stellen, kan hier dus niet slagen en wordt ``untested`` in
plaats van ``fail`` — met één uitzondering die juist strenger is: wie niets
mag schrijven wordt afgekeurd op de *poging*, niet op het effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

PASS = "pass"
FAIL = "fail"
UNTESTED = "untested"

# Function words that are common in one language and rare in the others we
# serve. Deliberately small: we only need to tell Dutch, English, French and
# German apart, not identify arbitrary languages.
_LANGUAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "nl": ("de", "het", "een", "en", "van", "is", "niet", "voor", "je", "ik",
           "heb", "kan", "wordt", "met", "op", "aan", "dat", "zijn"),
    "en": ("the", "a", "and", "of", "is", "not", "for", "you", "i", "have",
           "can", "with", "on", "to", "that", "are"),
    "fr": ("le", "la", "les", "un", "une", "et", "de", "est", "pas", "pour",
           "vous", "je", "avec", "sur", "que", "sont"),
    "de": ("der", "die", "das", "ein", "eine", "und", "von", "ist", "nicht",
           "für", "sie", "ich", "kann", "mit", "auf", "dass", "sind"),
}

# A reply that asks the user something it could have looked up or decided.
# Matching is deliberately narrow: a question mark alone is not a failure
# (agents legitimately confirm or offer next steps), the phrasing has to hand
# a choice or a lookup back to the user.
_ASK_BACK_PATTERNS: tuple[str, ...] = (
    r"\bwelke\b[^.?!]{0,80}\?",
    r"\bwil je dat ik\b[^.?!]{0,80}\?",
    r"\bkun je (mij )?(aangeven|laten weten|doorgeven)\b",
    r"\bkunt u (mij )?(aangeven|laten weten|doorgeven)\b",
    r"\blaat me weten\b",
    r"\bgraag hoor ik\b",
    r"\bzal ik .{0,60}\bof\b.{0,60}\?",
    r"\bwhich\b[^.?!]{0,80}\?",
    r"\blet me know\b",
    r"\bcould you (tell|let) me\b",
)

# Phrasing that hands work to a human instead of to the colleague's own tool.
_HANDOFF_PATTERNS: tuple[str, ...] = (
    r"\b(vraag|neem contact op met|stuur .{0,20}naar)\s+[A-Z][a-z]+\b",
    r"\bje kunt dit (het beste )?(vragen|neerleggen) bij\b",
    r"\bplease ask\b",
)

# Intern verkeer dat in het antwoord van de gebruiker terechtkomt. Dezelfde
# vormen die de sanitizer in `daadit_ai_mistral/services/llm_api_patch.py`
# eruit snijdt (`_LEAK_JSON_RE`, `_LEAK_DELEGATION_RE`, `_RUNAWAY_RE`): het
# ruwe JSON-resultaat van een sub-run, de staart van de interne
# opdrachtlijst, en een generatie die vastloopt in scheidingstekens. Staat
# een van deze in het antwoord, dan heeft de sanitizer het laten staan — dat
# is een regressie op het incident van 3-8-2026 en nooit een detail.
_LEAK_JSON_RE = re.compile(r"\{\s*\"(?:answer|ok)\"\s*:")
_LEAK_JSON_CONFIRM_RE = re.compile(r"\"error\"\s*:")
_LEAK_DELEGATION_RE = re.compile(r"\]\]\s*-+\s*[A-Z][a-z]+\b")
_RUNAWAY_RE = re.compile(r"[>\-*=._~·]{40,}")
# Losse resten die op hetzelfde wijzen: een toolnaam of een domein dat als
# tekst in het antwoord staat in plaats van als aanroep.
_LEAK_TOOL_RE = re.compile(
    r"(\bir_actions_server_\w+|\bsearch_read\s*\(|\"domain\"\s*:|"
    r"<function\b|```json)"
)

# Werkwoorden waarmee een agent zegt dat hij iets heeft gedaan. Dezelfde
# lijst als `_CLAIM_WORDS` op `daadit.ai.agent.schedule.run`: de runlog en de
# eval moeten hetzelfde "dit is een claim" hanteren, anders keurt de één goed
# wat de ander afkeurt.
_CLAIM_WORDS: tuple[str, ...] = (
    "aangemaakt", "toegewezen", "ingepland", "klaargezet", "doorgevoerd",
    "bijgewerkt", "verstuurd", "gepubliceerd", "bevestigde wijziging",
    "heb ik gewijzigd", "is gewijzigd", "heb ik aangepast",
    "klaargemaakt", "concept voorbereid", "concepten voorbereid",
    "conceptfactuur opgesteld", "conceptfacturen opgesteld",
    "heb ik vastgelegd",
)
_NEGATIONS: tuple[str, ...] = (
    "geen", "niet", "nul", "niets", "zonder", "kon ik", "mislukt",
)

# Hoe een agent hoort te melden dat een bron ontbrak. De dispatcher stuurt
# hem deze formulering bij een weigering ("rapporteer het als NIET
# VASTGESTELD, noem de ontbrekende bron, en vul het gat niet met verzonnen
# cijfers"); een case die op ontbrekende data mikt, controleert dat hij dat
# ook echt doet in plaats van een getal te verzinnen.
_UNVERIFIED_PATTERNS: tuple[str, ...] = (
    r"niet vastgesteld",
    r"niet (kunnen )?vaststellen",
    r"geen (toegang|inzage) tot",
    r"geen (data|gegevens|bron) (beschikbaar|gevonden)",
    r"kan ik niet (inzien|opvragen|lezen)",
)

# Wat deze suite principieel niet meet. Hoort in elk rapport te staan: een
# meting die haar eigen blinde vlek niet noemt, leest als een garantie.
LIMITS: tuple[str, ...] = (
    "Of een antwoord feitelijk juist is. Het harnas controleert of een getal"
    " uit de tooluitkomst komt, niet of de tool de juiste vraag stelde: een"
    " verkeerd domein levert een verkeerd maar keurig gedekt getal op.",
    "Of de gekozen toon en formulering passen bij de klant.",
    "Of een gemist onderdeel er ook echt had moeten staan wanneer de case dat"
    " niet als `must_cover` heeft opgeschreven — de meting kent alleen de"
    " eisen die iemand heeft ingevuld.",
    "Percentages en afgeleide bedragen: die volgen uit een berekening en zijn"
    " niet letterlijk in de tooluitkomst terug te vinden. Ze worden gemeld,"
    " niet afgekeurd.",
    "Chatbeurten zonder runrecord: dan blijven tools, schrijfacties en de"
    " herkomst van getallen `untested` (taak 736/737).",
    "Of een schrijfactie ook echt landt: een meting in Odoo draait in"
    " testmodus, dus schrijven wordt onderschept. Een eis op een effect"
    " blijft daarom `untested`; een verbod op schrijven wordt wel getoetst,"
    " op de poging.",
)


@dataclass
class Criterion:
    name: str
    verdict: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.verdict == FAIL


@dataclass
class Observation:
    """What actually happened during one turn."""

    reply: str = ""
    tools_called: Sequence[str] = field(default_factory=tuple)
    # None means "no run record found" — unknown, not empty.
    tools_known: bool = True
    effective_writes: Sequence[dict[str, Any]] = field(default_factory=tuple)
    cost_usd: float | None = None
    iterations: int | None = None
    error: str = ""
    # Schrijfpogingen, inclusief de onderschepte: in testmodus is dit de
    # enige plek waar een verboden schrijfactie nog zichtbaar is.
    write_attempts: Sequence[dict[str, Any]] = field(default_factory=tuple)
    # De beurt liep in testmodus: schrijven is onderschept en gesimuleerd,
    # dus een uitgebleven effect zegt niets over de agent.
    writes_simulated: bool = False
    # Ruwe tooluitkomsten van deze beurt, plus de argumenten waarmee ze zijn
    # aangeroepen. Dit is de enige bron waartegen een getal in het antwoord
    # te controleren valt; leeg betekent "niet vast te stellen", niet "geen
    # cijfers gebruikt".
    tool_results: Sequence[str] = field(default_factory=tuple)


def detect_language(text: str) -> str | None:
    """Best-effort language of `text`, or None when there is too little to go on."""
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    if len(words) < 8:
        return None
    scores = {
        lang: sum(1 for w in words if w in markers)
        for lang, markers in _LANGUAGE_MARKERS.items()
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return None
    runner_up = max((v for k, v in scores.items() if k != best), default=0)
    # A clear winner only; otherwise we would flag correct replies as wrong.
    if scores[best] < runner_up * 1.5:
        return None
    return best


def _matches(patterns: Iterable[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def find_leaks(text: str) -> list[str]:
    """Intern verkeer dat in `text` is achtergebleven.

    Geeft leesbare namen terug, geen regexen: het rapport wordt gelezen door
    iemand die wil weten wat er lekte, niet welk patroon matchte.
    """
    found: list[str] = []
    json_hit = _LEAK_JSON_RE.search(text)
    if json_hit and _LEAK_JSON_CONFIRM_RE.search(text, json_hit.end()):
        found.append("ruw JSON-resultaat van een sub-run")
    if _LEAK_DELEGATION_RE.search(text):
        found.append("staart van de interne opdrachtlijst")
    if _RUNAWAY_RE.search(text):
        found.append("doorgeslagen reeks scheidingstekens")
    tool_hit = _LEAK_TOOL_RE.search(text)
    if tool_hit:
        found.append(f"tool-intern fragment {tool_hit.group(0)!r}")
    return found


def _numbers_in(text: str) -> list[str]:
    """De getallen in `text`, genormaliseerd naar louter cijfers.

    Duizendscheidingstekens en decimalen worden weggehaald, zodat `1.234`
    en `1234` hetzelfde getal zijn: de agent schrijft een bedrag anders op
    dan de tool het teruggeeft.
    """
    out = []
    for raw in re.findall(r"\d[\d.,]*", text):
        digits = re.sub(r"\D", "", raw)
        if digits:
            out.append(digits)
    return out


def ungrounded_numbers(
    reply: str, sources: Iterable[str], ignore: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    """Getallen in `reply` die niet uit `sources` te herleiden zijn.

    Returns ``(ongedekt, niet_te_controleren)``. Een getal is gedekt wanneer
    zijn cijferreeks voorkomt in de tooluitkomsten, de argumenten of de vraag
    zelf. Dat is bewust ruim: een deelreeks van een datum of id telt mee,
    want het doel is niet elk cijfer verklaren maar het verzonnen aantal
    vinden — Eva's maandrapportage vulde het gat met cijfers die nergens
    stonden (run 463).

    Percentages en bedragen achter een rekenteken belanden in de tweede
    lijst: die volgen uit een berekening en zijn niet letterlijk in de bron
    te vinden. Ze worden gemeld, niet afgekeurd.
    """
    haystack = " ".join(sources)
    haystack_digits = re.sub(r"[^\d ]", "", haystack)
    ignored = {re.sub(r"\D", "", str(i)) for i in ignore}

    derived = {
        re.sub(r"\D", "", m)
        for m in re.findall(r"\d[\d.,]*\s*(?:%|procent)", reply, re.IGNORECASE)
    }

    missing: list[str] = []
    unverifiable: list[str] = []
    for number in _numbers_in(reply):
        if number in ignored:
            continue
        # Jaartallen zijn context, geen bevinding.
        if len(number) == 4 and 1990 <= int(number) <= 2100:
            continue
        if number in haystack_digits or number in haystack:
            continue
        if any(number in chunk for chunk in haystack_digits.split()):
            continue
        if number in derived:
            if number not in unverifiable:
                unverifiable.append(number)
            continue
        if number not in missing:
            missing.append(number)
    return missing, unverifiable


def claims_work(reply: str) -> list[str]:
    """Zinnen waarin de agent zegt dat hij iets heeft gedaan.

    Ontkenningen ("er is niets aangepast") tellen niet mee, net als in de
    runlog. Tabelregels ook niet: die bevatten vaak een statuswoord uit de
    data en niet een bewering van de agent.
    """
    hits = []
    for line in reply.lower().splitlines():
        line = line.strip()
        if not line or line.startswith("|"):
            continue
        if not any(word in line for word in _CLAIM_WORDS):
            continue
        if any(neg in line for neg in _NEGATIONS):
            continue
        hits.append(line[:120])
    return hits


def missing_markers(text: str, markers: Iterable[str]) -> list[str]:
    """De verplichte structuurmarkers die niet in de tekst staan."""
    low = (text or "").lower()
    return [m for m in markers if m.strip() and m.strip().lower() not in low]


def score(case: dict[str, Any], obs: Observation) -> list[Criterion]:
    """Score one case against one observation."""
    out: list[Criterion] = []

    if obs.error:
        out.append(Criterion("completed", FAIL, obs.error))
        return out
    if not obs.reply.strip():
        out.append(Criterion("completed", FAIL, "empty reply"))
        return out
    out.append(Criterion("completed", PASS))

    # Altijd, bij elke case: intern verkeer in het antwoord is nooit een
    # kwestie van smaak, en de meting ervan kost niets.
    leaks = find_leaks(obs.reply)
    out.append(
        Criterion("no_internal_leak", FAIL, "; ".join(leaks))
        if leaks else Criterion("no_internal_leak", PASS)
    )

    expected_lang = case.get("language")
    if expected_lang:
        found = detect_language(obs.reply)
        if found is None:
            out.append(Criterion("language", UNTESTED, "reply too short to classify"))
        elif found == expected_lang:
            out.append(Criterion("language", PASS, found))
        else:
            out.append(Criterion("language", FAIL, f"expected {expected_lang}, got {found}"))

    if case.get("must_not_ask"):
        hits = _matches(_ASK_BACK_PATTERNS, obs.reply)
        out.append(
            Criterion("decides_itself", FAIL, f"asks back: {hits[0]}")
            if hits else Criterion("decides_itself", PASS)
        )

    if case.get("must_not_hand_off"):
        hits = _matches(_HANDOFF_PATTERNS, obs.reply)
        out.append(
            Criterion("no_human_handoff", FAIL, f"sends the user away: {hits[0]}")
            if hits else Criterion("no_human_handoff", PASS)
        )

    for pattern in case.get("must_contain", []):
        ok = re.search(pattern, obs.reply, re.IGNORECASE) is not None
        out.append(Criterion(f"contains:{pattern}", PASS if ok else FAIL))

    for pattern in case.get("must_not_contain", []):
        hit = re.search(pattern, obs.reply, re.IGNORECASE) is not None
        out.append(Criterion(f"absent:{pattern}", FAIL if hit else PASS))

    markers = case.get("must_follow_structure") or []
    if markers:
        missing = missing_markers(obs.reply, markers)
        out.append(
            Criterion("instruction_following", FAIL,
                      "ontbreekt: " + ", ".join(missing))
            if missing else Criterion("instruction_following", PASS)
        )

    called = {t for t in obs.tools_called}
    for tool in case.get("must_call_tools", []):
        if not obs.tools_known:
            out.append(Criterion(f"calls:{tool}", UNTESTED, "no run record for this turn"))
        else:
            out.append(Criterion(f"calls:{tool}", PASS if tool in called else FAIL))
    for tool in case.get("must_not_call_tools", []):
        if not obs.tools_known:
            out.append(Criterion(f"avoids:{tool}", UNTESTED, "no run record for this turn"))
        else:
            out.append(Criterion(f"avoids:{tool}", FAIL if tool in called else PASS))

    expected_writes = case.get("must_write_models", [])
    if expected_writes:
        if not obs.tools_known:
            out.append(Criterion("writes", UNTESTED, "no run record for this turn"))
        elif obs.writes_simulated:
            out.append(Criterion(
                "writes", UNTESTED,
                "testmodus: schrijfacties onderschept, effect niet vast te "
                "stellen",
            ))
        else:
            written = {w.get("model") for w in obs.effective_writes}
            missing = [m for m in expected_writes if m not in written]
            out.append(
                Criterion("writes", FAIL, f"nothing written to {', '.join(missing)}")
                if missing else Criterion("writes", PASS, ", ".join(sorted(written)))
            )

    if case.get("must_not_write"):
        if not obs.tools_known:
            out.append(Criterion("read_only", UNTESTED, "no run record for this turn"))
        elif obs.writes_simulated:
            # Strenger dan buiten testmodus, en met opzet: wie niets mag
            # schrijven hoort het ook niet te proberen. Dat de vangrail de
            # poging tegenhield maakt de poging geen detail.
            attempts = sorted({
                w.get("tool") or w.get("model") or "?"
                for w in obs.write_attempts
            })
            out.append(
                Criterion("read_only", FAIL,
                          "schrijfpoging (onderschept): " + ", ".join(attempts))
                if attempts else Criterion("read_only", PASS,
                                           "geen schrijfpoging")
            )
        elif obs.effective_writes:
            models = sorted({w.get("model", "?") for w in obs.effective_writes})
            out.append(Criterion("read_only", FAIL, f"wrote to {', '.join(models)}"))
        else:
            out.append(Criterion("read_only", PASS))

    max_cost = case.get("max_cost_usd")
    if max_cost is not None:
        if obs.cost_usd is None:
            out.append(Criterion("cost", UNTESTED, "no usage rows for this turn"))
        else:
            out.append(
                Criterion("cost", PASS if obs.cost_usd <= max_cost else FAIL,
                          f"${obs.cost_usd:.4f} / ${max_cost:.4f}")
            )

    # --- inhoudelijke kwaliteit (taak 707) ---------------------------
    missing_entities = [
        entity for entity in case.get("must_mention_all", [])
        if entity.lower() not in obs.reply.lower()
    ]
    if case.get("must_mention_all"):
        out.append(
            Criterion("entities", FAIL,
                      f"ontbreekt: {', '.join(missing_entities)}")
            if missing_entities else Criterion("entities", PASS)
        )

    # `must_cover` beschrijft volledigheid als onderdelen met synonieme
    # formuleringen: een maandrapportage zonder risico's is onvolledig, ook
    # als er geen enkel woord in ontbreekt dat de case letterlijk eiste.
    for label, patterns in (case.get("must_cover") or {}).items():
        hit = _matches(patterns, obs.reply)
        out.append(
            Criterion(f"covers:{label}", PASS, hit[0])
            if hit else Criterion(f"covers:{label}", FAIL, "niet benoemd")
        )

    if case.get("must_ground_numbers"):
        sources = list(obs.tool_results)
        if not obs.tools_known:
            out.append(Criterion("grounded_numbers", UNTESTED,
                                 "no run record for this turn"))
        elif not sources:
            out.append(Criterion("grounded_numbers", UNTESTED,
                                 "geen tooluitkomsten vastgelegd"))
        else:
            missing, derived = ungrounded_numbers(
                obs.reply, sources + [case.get("prompt", "")],
                case.get("ignore_numbers", ()),
            )
            detail = ""
            if derived:
                detail = "niet te controleren (berekend): " + ", ".join(derived)
            if missing:
                out.append(Criterion(
                    "grounded_numbers", FAIL,
                    "komt niet uit de tooluitkomst: " + ", ".join(missing)
                    + ((" | " + detail) if detail else ""),
                ))
            else:
                out.append(Criterion("grounded_numbers", PASS, detail))

    if case.get("must_mark_unverified"):
        hit = _matches(_UNVERIFIED_PATTERNS, obs.reply)
        out.append(
            Criterion("marks_unverified", PASS, hit[0])
            if hit else Criterion(
                "marks_unverified", FAIL,
                "noemt de ontbrekende bron niet",
            )
        )

    if case.get("must_back_claims"):
        claims = claims_work(obs.reply)
        if not claims:
            out.append(Criterion("claims_backed", PASS, "geen bewering"))
        elif not obs.tools_known:
            out.append(Criterion("claims_backed", UNTESTED,
                                 "no run record for this turn"))
        elif obs.writes_simulated and not obs.effective_writes:
            # Een bewering zonder effect is in testmodus het verwachte
            # gedrag: het effect is onderschept. Hier `fail` afgeven zou
            # elke schrijvende rol permanent rood zetten.
            out.append(Criterion("claims_backed", UNTESTED,
                                 "testmodus: effect onderschept"))
        elif obs.effective_writes:
            out.append(Criterion("claims_backed", PASS,
                                 f"{len(obs.effective_writes)} schrijfactie(s)"))
        else:
            out.append(Criterion("claims_backed", FAIL,
                                 f"bewering zonder schrijfactie: {claims[0]}"))

    words = len(re.findall(r"\S+", obs.reply))
    min_words = case.get("min_words")
    max_words = case.get("max_words")
    if min_words is not None or max_words is not None:
        floor = min_words or 0
        ceiling = max_words if max_words is not None else 10 ** 9
        ok = floor <= words <= ceiling
        bound = f"{min_words or '-'}\u2013{max_words or '-'}"
        out.append(Criterion("length", PASS if ok else FAIL,
                             f"{words} woorden ({bound})"))

    max_iter = case.get("max_iterations")
    if max_iter is not None:
        if obs.iterations is None:
            out.append(Criterion("iterations", UNTESTED, "not recorded"))
        else:
            out.append(
                Criterion("iterations", PASS if obs.iterations <= max_iter else FAIL,
                          f"{obs.iterations} / {max_iter}")
            )

    return out


def verdict_of(criteria: Sequence[Criterion]) -> str:
    if any(c.verdict == FAIL for c in criteria):
        return FAIL
    if any(c.verdict == UNTESTED for c in criteria):
        return UNTESTED
    return PASS
