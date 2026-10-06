# -*- coding: utf-8 -*-
"""Drift tussen de promptregistry en de draaiende planningen (taak 707, OAS).

De knowledge-registry is de bron van waarheid voor agent- en
planningsprompts: een uurlijkse sync in de tenant schrijft het artikel
naar de agent en naar de planning terug. Maar niets *toont* of die twee
op dit moment gelijk zijn. Zolang dat onzichtbaar is, kun je niet
aantonen wat de governance belooft — dat de gepubliceerde grens ook de
gebruikte grens is. Tussen twee synchronisaties, bij een sync die op een
fout afbrak, of bij een handmatige wijziging in de database is de
draaiende instructie een andere tekst dan de gepubliceerde, en dat merkt
niemand.

Deze module leest en vergelijkt; hij schrijft nooit. Herstellen blijft
het werk van de bestaande sync (of van een mens die het artikel
aanpast) — een tweede tekst die terugschrijft zou een tweede bron van
waarheid zijn.

Twee dingen zijn met opzet overgenomen uit de sync in plaats van
zelfbedacht:

* **De koppeling artikel ↔ agent ↔ planning** staat in de
  systeemparameter ``daadit_prompt_registry.map``, in de vorm
  ``artikel:agent:planning`` per entry, gescheiden door ``;``. Een entry
  van twee delen (``274:11``) hoort bij een artikel zonder planning en
  is dus geen drift.
* **Het uitpakken van de blokken.** Blok 1 (system-prompt) en blok 2
  (schedule-instructie) staan als ``<pre>`` in de body, met ``<br>`` als
  regeleinde en ge-escapete punthaken. De sync neemt de blokken in
  volgorde en maakt ze op dezelfde manier plat; wie hier anders
  normaliseert, meldt drift die de sync niet ziet (of omgekeerd).

Een leeg blok 2 is een bewuste keuze in het artikel — "dit deel is niet
gesynchroniseerd" — en dus geen drift. Dat is de reden dat blok 1 bij de
meeste artikelen leeg staat: de system-prompt is te lang om betrouwbaar
in het artikel over te nemen.
"""
import html
import logging

_logger = logging.getLogger(__name__)

# De systeemparameter die de bestaande sync ook leest. Bewust dezelfde
# bron: een eigen lijst zou binnen een week uit elkaar lopen met de
# lijst die het werk doet.
REGISTRY_MAP_PARAM = "daadit_prompt_registry.map"

# Uitkomsten per registry-entry.
SAME = "gelijk"
DRIFT = "drift"
NO_BLOCK = "geen_blok"
NO_SCHEDULE = "geen_planning"
MISSING = "ontbreekt"
INACTIVE = "planning_uit"

# Uitkomsten waarbij er niets is vergeleken. Ze mogen niet meetellen als
# "gelijk": een koppeling naar een planning die niet meer bestaat of die
# uit staat, is geen bewijs dat de gepubliceerde tekst ook draait — dat
# was precies de stille fout waardoor artikel 274 maandenlang op een
# verdwenen planning bleef wijzen zonder dat iets afging.
UNVERIFIED = (MISSING, INACTIVE, NO_SCHEDULE, NO_BLOCK)

# Het blok met de schedule-instructie: de tweede <pre> in de body.
_SCHEDULE_BLOCK = 1

# Genoeg om beide blokken te vinden zonder een body met veel
# codevoorbeelden helemaal af te lopen.
_MAX_BLOCKS = 4


def parse_map(raw):
    """De koppelingen uit de systeemparameter, in leesvolgorde.

    Geeft een lijst tuples ``(artikel_id, agent_id, planning_id)``, waarbij
    ``planning_id`` 0 is als de entry geen planning noemt. Onleesbare
    entries worden overgeslagen en gelogd: een typefout in één entry mag
    de hele controle niet blind maken.
    """
    entries = []
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(":")
        try:
            article = int(pieces[0])
            agent = int(pieces[1])
        except (IndexError, ValueError):
            _logger.warning(
                "promptdrift: onleesbare registry-entry %r overgeslagen",
                part,
            )
            continue
        schedule = 0
        if len(pieces) > 2 and pieces[2].strip():
            try:
                schedule = int(pieces[2])
            except ValueError:
                _logger.warning(
                    "promptdrift: onleesbaar planning-id in entry %r",
                    part,
                )
                continue
        entries.append((article, agent, schedule))
    return entries


def extract_blocks(body):
    """De ``<pre>``-blokken uit een artikelbody, plat gemaakt.

    Zelfde bewerking als de sync: ``<br>`` en het einde van een alinea
    worden een regeleinde, de overige opmaak verdwijnt, en daarna gaan de
    HTML-entiteiten terug naar gewone tekens. Die volgorde is belangrijk:
    andersom zou een in het artikel *beschreven* punthaak (``&lt;br&gt;``)
    als opmaak worden gelezen en verdwijnen.
    """
    blocks = []
    rest = body or ""
    while "<pre" in rest and len(blocks) < _MAX_BLOCKS:
        start = rest.find("<pre")
        open_end = rest.find(">", start)
        close = rest.find("</pre>", open_end) if open_end != -1 else -1
        if open_end == -1 or close == -1:
            break
        blocks.append(_flatten(rest[open_end + 1:close]))
        rest = rest[close + len("</pre>"):]
    return blocks


def _flatten(fragment):
    """Eén blok als platte tekst."""
    text = fragment
    for tag in ("<br/>", "<br />", "<br>", "</p>", "</div>"):
        text = text.replace(tag, "\n")
    out = []
    in_tag = False
    for char in text:
        if char == "<":
            in_tag = True
        elif char == ">":
            in_tag = False
        elif not in_tag:
            out.append(char)
    return html.unescape("".join(out)).replace("\xa0", " ").strip()


def normalize(text):
    """De vorm waarin twee teksten met elkaar te vergelijken zijn.

    Regeleindes, tabs en herhaalde spaties zeggen niets over de
    instructie: de editor van Knowledge maakt ze anders dan het tekstveld
    van de planning, en een verschil dat alleen daaruit bestaat zou de
    meting elke dag laten afgaan zonder dat er iets aan de hand is.
    """
    return " ".join((text or "").split())


def compare(block, prompt):
    """Hoe blok 2 zich verhoudt tot de instructie van de planning.

    Geeft ``(uitkomst, toelichting)``. Een leeg blok is met opzet niet
    gesynchroniseerd en dus geen drift — dat onderscheid is de reden dat
    deze functie geen boolean teruggeeft.
    """
    published = normalize(block)
    running = normalize(prompt)
    if not published:
        return NO_BLOCK, "blok 2 is leeg — met opzet niet gesynchroniseerd"
    if published == running:
        return SAME, ""
    if not running:
        return DRIFT, "de planning heeft geen instructie, het artikel wel"
    return DRIFT, _difference(published, running)


def _difference(published, running):
    """Waar de twee teksten uit elkaar lopen, in leesbare vorm.

    Een reviewer moet kunnen zien *wat* er anders is zonder twee lappen
    tekst naast elkaar te leggen; de positie plus het eerste stuk dat
    verschilt is daarvoor genoeg.
    """
    limit = min(len(published), len(running))
    at = limit
    for index in range(limit):
        if published[index] != running[index]:
            at = index
            break
    return (
        "verschil vanaf teken %d — gepubliceerd %r, draaiend %r "
        "(%d tegen %d tekens)"
    ) % (at, published[at:at + 60], running[at:at + 60],
         len(published), len(running))


def scan(env):
    """De stand van elke registry-koppeling, als lijst regels.

    Elke regel is een dict met ``article``, ``agent``, ``schedule``,
    ``schedule_name``, ``status`` en ``detail``. Alleen lezen: deze
    functie wijzigt geen artikel, geen agent en geen planning.
    """
    param = env["ir.config_parameter"].sudo()
    entries = parse_map(param.get_param(REGISTRY_MAP_PARAM, ""))
    if not entries:
        return []
    if "knowledge.article" not in env:
        # De registry leeft in Knowledge; zonder die app is er niets te
        # vergelijken. Dan liever niets melden dan "geen drift" — dat
        # laatste zou een gerustheid zijn die niemand heeft gemeten.
        _logger.info(
            "promptdrift: knowledge.article is niet beschikbaar; "
            "%d koppeling(en) niet te controleren", len(entries),
        )
        return []

    bodies = {
        rec["id"]: rec["body"]
        for rec in env["knowledge.article"].sudo().search_read(
            [("id", "in", [e[0] for e in entries])], ["body"],
        )
    }
    schedules = {
        rec.id: rec
        for rec in env["daadit.ai.agent.schedule"].sudo().with_context(
            active_test=False,
        ).browse([e[2] for e in entries if e[2]]).exists()
    }

    rows = []
    for article, agent, schedule_id in entries:
        row = {
            "article": article,
            "agent": agent,
            "schedule": schedule_id,
            "schedule_name": "",
            "status": SAME,
            "detail": "",
        }
        if article not in bodies:
            row["status"] = MISSING
            row["detail"] = "het registry-artikel bestaat niet meer"
            rows.append(row)
            continue
        if not schedule_id:
            row["status"] = NO_SCHEDULE
            row["detail"] = "entry zonder planning — alleen system-prompt"
            rows.append(row)
            continue
        schedule = schedules.get(schedule_id)
        if schedule is None:
            row["status"] = MISSING
            row["detail"] = (
                "de planning bestaat niet meer — de instructie in dit "
                "artikel wordt nergens toegepast"
            )
            rows.append(row)
            continue
        row["schedule_name"] = schedule.name or ""
        if not schedule.active:
            # Vergelijken zou hier "gelijk" kunnen opleveren en dat leest
            # als "in orde", terwijl deze tekst nergens draait. De
            # koppeling is niet fout, maar ook geen dekking.
            row["status"] = INACTIVE
            row["detail"] = (
                "de planning staat uit — de gepubliceerde instructie "
                "draait nergens"
            )
            rows.append(row)
            continue
        blocks = extract_blocks(bodies[article])
        block = (
            blocks[_SCHEDULE_BLOCK] if len(blocks) > _SCHEDULE_BLOCK else ""
        )
        row["status"], row["detail"] = compare(block, schedule.prompt)
        rows.append(row)
    return rows


def drift_rows(rows):
    """Alleen de regels waar gepubliceerd en draaiend uit elkaar lopen."""
    return [row for row in rows if row["status"] == DRIFT]


def summary(rows):
    """De drift als tekst voor het scherm, of een lege string."""
    lines = []
    for row in drift_rows(rows):
        lines.append("artikel %d \u2194 planning %d (%s): %s" % (
            row["article"], row["schedule"],
            row["schedule_name"] or "?", row["detail"],
        ))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Dekking: welke planning heeft een artikel, en welk artikel een planning
# ----------------------------------------------------------------------
def coverage(env):
    """Wie er wél en niet gedekt is door de registry.

    De driftcontrole hierboven kijkt alleen naar wat *in* de registry
    staat. Daarmee blijft de grootste blinde vlek bestaan: een planning
    die nergens in de registry voorkomt, kan per definitie geen drift
    hebben. "Nul drift" is dus pas een uitspraak als je erbij weet hoeveel
    planningen zijn meegeteld.

    Geeft een dict met:

    * ``rows`` — de driftregels (zie :func:`scan`);
    * ``schedules`` — elke actieve planning met ``covered``;
    * ``uncovered`` — actieve planningen zonder registry-artikel;
    * ``orphans`` — artikelen ónder de registry-map die in geen enkele
      koppeling voorkomen: gepubliceerd, maar nergens toegepast;
    * ``dead`` — koppelingen naar een verdwenen artikel of planning;
    * ``inactive`` — koppelingen naar een uitgezette planning.
    """
    rows = scan(env)
    Schedule = env["daadit.ai.agent.schedule"].sudo()
    linked = {row["schedule"] for row in rows if row["schedule"]}
    schedules = []
    for rec in Schedule.search([], order="id"):
        schedules.append({
            "id": rec.id,
            "name": rec.name or "",
            "agent": rec.agent_id.display_name or "",
            "agent_id": rec.agent_id.id,
            "prompt": rec.prompt or "",
            "interval": "%s %s" % (rec.interval_number, rec.interval_type),
            "user": rec.user_id.display_name or "",
            "daily_cap": rec.daily_cost_cap_eur,
            "monthly_cap": rec.monthly_cost_cap_eur,
            "covered": rec.id in linked,
        })
    return {
        "rows": rows,
        "schedules": schedules,
        "uncovered": [s for s in schedules if not s["covered"]],
        "orphans": _orphan_articles(env, rows),
        "dead": [r for r in rows if r["status"] == MISSING],
        "inactive": [r for r in rows if r["status"] == INACTIVE],
    }


def _orphan_articles(env, rows):
    """Registry-artikelen waar geen koppeling naar wijst.

    Waar de registry-map staat, leiden we af uit de artikelen die wél
    gekoppeld zijn: hun gedeelde bovenliggende artikel. Dat scheelt een
    tweede instelling die kan verlopen — verhuist de map, dan verhuist
    deze controle mee.
    """
    if "knowledge.article" not in env:
        return []
    Article = env["knowledge.article"].sudo()
    known = {row["article"] for row in rows}
    linked = Article.browse(sorted(known)).exists()
    parents = {}
    for article in linked:
        if article.parent_id:
            parents[article.parent_id.id] = parents.get(
                article.parent_id.id, 0,
            ) + 1
    if not parents:
        return []
    root = max(parents, key=parents.get)
    orphans = []
    for article in Article.search([("parent_id", "=", root)], order="id"):
        if article.id not in known:
            orphans.append({
                "article": article.id,
                "name": article.display_name or "",
            })
    return orphans


def coverage_report(state):
    """Het dekkingsrapport als platte tekst \u2014 een voorstel, geen wijziging.

    Bewust alleen tekst: wat er in een registry-artikel hoort, is een
    besluit (welk doel, welke bestemming, welk budget) en dat legt een
    mens één keer vast. Automatisch een artikel genereren zou van de
    registry een afgeleide van de database maken, terwijl het net
    andersom hoort te zijn.
    """
    lines = [
        "Dekking van de promptregistry (dry-run \u2014 er is niets gewijzigd)",
        "",
        "Actieve planningen: %d, waarvan %d met registry-artikel." % (
            len(state["schedules"]),
            len(state["schedules"]) - len(state["uncovered"]),
        ),
        "Koppelingen: %d, waarvan %d met drift, %d naar een uitgezette "
        "planning en %d naar iets dat niet meer bestaat." % (
            len(state["rows"]), len(drift_rows(state["rows"])),
            len(state["inactive"]), len(state["dead"]),
        ),
        "",
    ]
    if state["uncovered"]:
        lines.append("Planningen zonder registry-artikel \u2014 voorstel:")
        for item in state["uncovered"]:
            lines.extend(_proposal(item))
        lines.append("")
    else:
        lines.append("Elke actieve planning heeft een registry-artikel.")
        lines.append("")
    if state["orphans"]:
        lines.append(
            "Registry-artikelen zonder koppeling (gepubliceerd, maar de "
            "sync doet er niets mee \u2014 zet ze in de map of haal ze weg):",
        )
        for orphan in state["orphans"]:
            lines.append("  - artikel %d: %s" % (
                orphan["article"], orphan["name"],
            ))
        lines.append("")
    for row in state["inactive"] + state["dead"]:
        lines.append("Koppeling artikel %d \u2192 planning %d: %s" % (
            row["article"], row["schedule"], row["detail"],
        ))
    return "\n".join(lines).strip()


def _proposal(item):
    """Wat er in het ontbrekende artikel zou moeten staan."""
    prompt = item["prompt"].strip()
    excerpt = prompt[:400] + ("\u2026" if len(prompt) > 400 else "")
    return [
        "",
        "  Planning %d \u2014 %s" % (item["id"], item["name"]),
        "    agent:       %s" % (item["agent"] or "geen agent gekoppeld"),
        "    doel:        %s (draait elke %s, als %s)" % (
            item["name"], item["interval"],
            item["user"] or "geen run-as gebruiker",
        ),
        "    bestemming:  nieuw artikel \u201c\u2699\ufe0f Registry: %s\u201d onder de "
        "registry-map, en de koppeling %s:%s:%s in %s" % (
            item["name"], "<artikel>", item["agent_id"] or "<agent>",
            item["id"], REGISTRY_MAP_PARAM,
        ),
        "    budget:      %.2f EUR per dag / %.2f EUR per maand" % (
            item["daily_cap"], item["monthly_cap"],
        ),
        "    blok 2:      de tekst die nu draait "
        "(%d tekens): %s" % (len(prompt), excerpt or "\u2014 leeg \u2014"),
    ]
