# -*- coding: utf-8 -*-
{
    "name": "DAADit AI — Agent-activiteit",
    "summary": "Live dashboard: wat elke AI-agent nu doet, vandaag deed "
               "en later nog gaat doen",
    "description": """
DAADit AI — Agent-activiteit
============================
Eén live scherm in de AI-app dat de hele agent-organisatie volgt:

* **Nu bezig** — lopende geplande runs, met verstreken tijd.
* **Agent-overzicht** — per agent de laatste runstatus, runs en
  chat-activiteit van vandaag en openstaande @-vermeldingen.
* **Vandaag gedaan** — afgeronde runs met duur, tokens, kosten en
  status; klik door naar de volledige run.
* **Gepland** — de eerstvolgende starttijd van elk actief schedule,
  vandaag en de komende week.
* **Live feed** — run gestart/klaar/mislukt en opgepakte
  @-vermeldingen, direct gepusht via de Odoo-bus (met een
  poll-fallback van één minuut).
* **Filters** — per afdeling (organogram-manager), per agent en per
  status; alles client-side, dus zonder herladen.

Geen nieuwe datamodellen: het scherm leest de bestaande
schedule-/run-/mention-/usage-records. De enige schrijfkant is een
bus-notificatie wanneer een run van status wisselt.
""",
    "version": "19.0.1.2.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": [
        "ai_app",
        "daadit_ai_agent_schedule",
        "bus",
    ],
    "data": [
        "views/activity_client_action.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "daadit_agent_activity/static/src/js/agent_activity_action.js",
            "daadit_agent_activity/static/src/xml/agent_activity_action.xml",
            "daadit_agent_activity/static/src/scss/agent_activity.scss",
        ],
        # Dark mode is in Odoo 19 een apart bundle dat web.assets_web
        # opnieuw compileert met donkere --bs-*-waarden. De basis-scss
        # volgt die variabelen al; dit bestand zet alleen het blauwe
        # accent om, dat als lichte tint is gedefinieerd.
        "web.assets_web_dark": [
            "daadit_agent_activity/static/src/scss/agent_activity.dark.scss",
        ],
    },
    "installable": True,
    "application": False,
}
