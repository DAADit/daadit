# -*- coding: utf-8 -*-
{
    "name": "DAADit — Procescanvas",
    "version": "19.0.2.0.0",
    "summary": "Visueel procescanvas: Odoo-apps als knooppunten, inzoomen op de "
               "handelingen erbinnen, input en output als lijnen.",
    "description": """
DAADit — Procescanvas
=====================
Legt een visuele laag over ``daadit_process_tooling``. Bewust een aparte module:
de processtooling is door YoMoRo geleverd, en die blijft zo vrij om te updaten
zonder dat onze weergave in de weg zit.

Twee niveaus
------------
* **Overzicht** — elke fase is een Odoo-app, getekend met het echte app-icoon.
  Het icoon komt van ``/{module}/static/description/icon.png``; dat pad is ook
  voor portaalgebruikers bereikbaar, dus er hoeft niets ingesloten te worden.
* **Ingezoomd** — de handelingen binnen die fase, elk met een in- en uitgang.

Lijnen tussen apps worden **afgeleid** uit verbindingen tussen handelingen die
een fasegrens oversteken. Ze worden dus niet apart vastgelegd: zo kunnen het
overzicht en de ingezoomde weergave nooit uit elkaar lopen.

Geen externe bibliotheek. Het canvas gebruikt de opzet van React Flow —
HTML-knooppunten in een pan/zoom-laag met een SVG-laag eronder voor de lijnen —
maar zonder React: ``react@19`` levert alleen CommonJS en Odoo-addons hebben
geen bundler, dus React zou een tweede bouwketen in de repo betekenen.

Geen automatische tests, om dezelfde reden als de processtooling: een falende
test kan hier alle odoo.sh-prod-builds blokkeren.
""",
    "category": "Services",
    "author": "DAADit",
    "website": "https://www.daadit.group",
    "license": "LGPL-3",
    "depends": [
        "daadit_process_tooling",
        # Levert de fase-rollen en de instelling waar bevindingen binnenkomen
        # (daadit_intake_target_stage_id), en trekt 'project' mee.
        "daadit_project_framework",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/process_canvas_views.xml",
        "views/portal_canvas_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "daadit_process_canvas/static/src/scss/process_canvas.scss",
            "daadit_process_canvas/static/src/js/process_canvas.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
