# -*- coding: utf-8 -*-
{
    "name": "DAADit \u2014 Vestigingsblueprint",
    "summary": "Exporteer en importeer de agentconfiguratie van een "
               "vestiging, zonder sleutels of tokens",
    "description": """
DAADit \u2014 Vestigingsblueprint
============================
Een nieuwe vestiging of een nieuw label (uitzendkracht.ai,
uitzendwerk.ai) hoort te starten vanaf een bekende, werkende
configuratie \u2014 niet vanaf iemand die het uit het hoofd naklikt.

Deze module leest de configuratie uit als JSON: welke collega's er zijn,
op welk model ze staan, welke onderwerpen en tools ze hebben, welke
planningen er lopen en op welke plan- en fair-use-instellingen de
omgeving staat.

Een blueprint draagt ook de grenzen, niet alleen het gedrag: de
leesscope, activiteitscope en het reparatiebereik per collega, en per
klantomgeving (``mcp.instance``) de omgevingssoort (dev/staging/
productie), de capabilities, de model-allowlist en -blocklist en de
limieten. Zonder die regels zou een tweede omgeving er hetzelfde
uitzien en ruimer mogen.

Wat er NIET in staat: sleutels, tokens, wachtwoorden. Alleen parameters
op een expliciete allowlist gaan mee, en elke kandidaat moet daarnaast
langs een naamcontrole op woorden als ``key``, ``token`` en ``secret``.
Een instelling die niet op de lijst staat ontbreekt dus in de export \u2014
dat is de bedoeling: een vergeten instelling is een ongemak, een
gelekte sleutel is een incident.

Bij importeren worden records op naam gematcht, niet op id: ids uit de
ene database betekenen niets in de andere. Onbekende collega's worden
gemeld en niet aangemaakt \u2014 een collega zonder zijn serveracties lijkt
te werken en doet niets. Een klantomgeving wordt nooit aangemaakt
(endpoint, database en sleutel horen bij de omgeving) en een planning
komt er altijd uitgeschakeld in: een agent die door een import begint te
draaien is een agent die niemand heeft aangezet.

Een export kan bewaard worden als **configuratiepakket**: een oplopend
nummer per vestiging, met tijdstip, maker en de JSON. Terugrollen is dan
het pakket van gisteren opnieuw toepassen — via dezelfde importroute, dus
met dezelfde grenzen: eerst een proef die bewaard wordt, daarna een
verslag van wat er feitelijk veranderde, en planningen blijven uit. Bij
het bewaren gaat de inhoud nog eens langs dezelfde twee netten, zodat ook
een pakket dat via de ORM ontstaat geen sleutel kan dragen.

Het importverslag is bewust twee blokken: wat er nu staat, en wat een
mens nog moet zetten. Dat tweede blok is een checklist — providersleutel,
endpoint en database van de klant-Odoo, ontbrekende modules, tools en
collega's — want een geïmporteerde vestiging is nog geen werkende
vestiging. Dezelfde procedure staat in ``docs/RUNBOOK.md``.

De MCP- en planningsmodules zijn geen harde afhankelijkheid. Staan ze
hier niet, dan beschrijft de blueprint minder in plaats van te falen \u2014
zo blijft de module installeerbaar in een klantomgeving die alleen chat.
""",
    "version": "19.0.4.0.6",
    "category": "Productivity",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": ["base", "ai"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/tenant_blueprint_views.xml",
        "views/config_snapshot_views.xml",
    ],
    "installable": True,
    "application": False,
}
