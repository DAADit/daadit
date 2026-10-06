# -*- coding: utf-8 -*-
{
    "name": "DAADit AI — Agentic System",
    "summary": "De agentlaag van DAADit: één tool-afhandeling voor elke provider",
    "description": """
DAADit AI — Agentic System
==========================
De agentlogica die voor elke provider gelijk is. De providermodules
(Mistral, Claude, Loes) verzorgen alleen de verbinding met hun model en
geven elke toolaanroep hier door.

Stap 1: de tool-afhandeling. Toolnaam herstellen, argumenten
normaliseren, model- en leesgrenzen, het privacyfilter op velden,
resultaatbegrenzing en logging staan hier één keer, met één
routerstatus voor delegatie tussen collega's.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": [
        "base",
        "ai",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
