# -*- coding: utf-8 -*-
"""Werkafspraken per bedrijf, klaar voor een prompt.

Een collega heeft één standaardopdracht die voor alle klanten geldt. Wat
een bedrijf anders wil ("pas na 3 werkdagen", "eerst de bank nakijken")
is geen wijziging van die standaard maar een afspraak met dát bedrijf.
Die afspraken staan los van de standaard opgeslagen en komen bij elke
run en elk gesprek in één gemarkeerd blok achter de opdracht. Wie de
standaard bijwerkt, raakt ze dus niet.

Geen ORM hier: dit bestand schoont tekst op en zet het blok, zodat het
zonder database te toetsen is. Een afspraak verandert nooit een recht:
het blok zegt dat met zoveel woorden, en de grenzen van de collega komen
uit zijn skills en de koppeling, niet uit deze tekst.
"""
import re

TOOL_VASTLEGGEN = "werkafspraak_vastleggen"
TOOL_INTREKKEN = "werkafspraak_intrekken"

MAX_AFSPRAAK_CHARS = 500
MAX_ACTIEF = 25

SECTION_START = "----- BEGIN WERKAFSPRAKEN -----"
SECTION_END = "----- EINDE WERKAFSPRAKEN -----"

_SECTION_RE = re.compile(
    r"\n*%s.*?%s\n*" % (re.escape(SECTION_START), re.escape(SECTION_END)),
    re.DOTALL,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_MARKER_RE = re.compile(r"-{3,}")


def clean(text):
    """Eén afspraak als één korte regel platte tekst, of ``""``."""
    text = _CONTROL_RE.sub(" ", text or "")
    text = _MARKER_RE.sub("-", text)
    text = " ".join(text.split())
    if len(text) > MAX_AFSPRAAK_CHARS:
        text = text[:MAX_AFSPRAAK_CHARS - 1].rstrip() + "…"
    return text


def intro(bedrijf):
    """Wat het blok is en hoe de collega er zelf een afspraak in zet."""
    return (
        "Dit zijn de werkafspraken die %(bedrijf)s met je heeft gemaakt. "
        "Ze komen bovenop je standaardwerkwijze en gaan daarvoor waar die "
        "botst. Ze geven je geen extra rechten of toegang: kun je een "
        "afspraak niet uitvoeren omdat iets buiten je toegang valt, zeg "
        "dat dan in gewone taal en zoek geen omweg.\n"
        "Vraagt iemand van %(bedrijf)s je in een gesprek om iets voortaan "
        "anders, wel of niet meer te doen, leg dat dan vast met "
        "%(vastleggen)s: één afspraak per keer, kort en in je eigen "
        "woorden. Vervangt het een eerdere afspraak, trek die dan in met "
        "%(intrekken)s. Bevestig daarna in gewone taal wat je hebt "
        "onthouden. Een vraag voor één keer is geen werkafspraak."
    ) % {
        "bedrijf": bedrijf or "deze klant",
        "vastleggen": TOOL_VASTLEGGEN,
        "intrekken": TOOL_INTREKKEN,
    }


def render_section(bedrijf, afspraken):
    """Het blok met de lopende afspraken.

    ``afspraken`` is een lijst ``(nummer, tekst, sinds, van)``; ``sinds``
    en ``van`` mogen leeg zijn. Zonder afspraken staat alleen de uitleg
    erin, zodat de collega weet dat hij er een kan vastleggen.
    """
    lines = [SECTION_START, intro(bedrijf)]
    if afspraken:
        lines.append("Lopende afspraken:")
        for nummer, tekst, sinds, van in afspraken:
            herkomst = ", ".join(part for part in (sinds, van) if part)
            lines.append("> #%s %s%s" % (
                nummer, clean(tekst),
                " (%s)" % herkomst if herkomst else "",
            ))
    else:
        lines.append("Er zijn nog geen afspraken.")
    lines.append(SECTION_END)
    return "\n".join(lines)


def strip_section(prompt):
    return _SECTION_RE.sub("\n\n", prompt or "").strip()


def apply_section(prompt, section):
    """Zet ``section`` achter ``prompt``; een bestaand blok wordt
    vervangen, dus twee keer aanroepen geeft hetzelfde."""
    base = strip_section(prompt)
    if not section:
        return base
    return "\n\n".join(part for part in (base, section) if part)
