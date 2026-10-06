# -*- coding: utf-8 -*-
{
    "name": "DAADit AI — Agents reageren op vermeldingen",
    "summary": "Tag een AI-collega in de chatter en zij pakt het op",
    "description": """
DAADit AI — Agents reageren op vermeldingen
============================================
Noem een AI-collega met ``@`` in de chatter van welk record dan ook — een
blogpost, een lead, een ticket — en zij leest mee en reageert, net als
een menselijke collega.

De agent krijgt de context van het record en het gesprek eronder mee, en
antwoordt in dezelfde chatter. Dat maakt de agents bereikbaar op de plek
waar het werk ligt, in plaats van alleen in een apart chatvenster.

Verwerking loopt via een wachtrij en een cron, zodat het opslaan van uw
notitie nooit hoeft te wachten op het antwoord.
""",
    "version": "19.0.1.4.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": ["ai", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/daadit_agent_mention_views.xml",
    ],
    "installable": True,
    "application": False,
}
