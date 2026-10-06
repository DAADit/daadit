# -*- coding: utf-8 -*-
{
    "name": "DAADit AI — Collega reageert op records",
    "summary": "Een automatiseringsregel schakelt een AI-collega in wanneer "
               "een record wordt aangemaakt of gewijzigd",
    "description": """
DAADit AI — Collega reageert op records
=======================================
Voegt aan de standaard Automatiseringsregels een actie toe:
**AI-collega inschakelen**. Kies de planning van de collega (die bepaalt
agent, tools, leesscope en budget), schrijf de opdracht met recordvelden
(``{{ object.name }}``) en kies of de uitkomst als notitie in de chatter
of als activiteit bij de verantwoordelijke komt.

* Elke gebeurtenis loopt via hetzelfde runpad als een geplande run: budget,
  feitenregel (claims), schrijfgrenzen en audit gelden ongewijzigd.
* Hoogstens één run per record, regel en periode; herhaald opslaan
  stapelt geen runs.
* Een betwiste run (beweerde handeling zonder geslaagde tool-actie)
  plaatst niets op het record.
* De actie zelf wijzigt geen veld van het record.
""",
    "version": "19.0.1.1.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": ["daadit_ai_agent_schedule", "ai_app", "base_automation", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/automation_event_views.xml",
        "views/ir_actions_server_views.xml",
    ],
    "installable": True,
    "application": False,
}
