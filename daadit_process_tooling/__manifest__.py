# -*- coding: utf-8 -*-
{
    "name": "DAADit — Processtooling (YoMoRo)",
    "version": "19.0.3.0.0",
    "summary": "Native processen per klant (fasen/stappen, valideren in de portal), "
               "per-proces configuratie, template-bibliotheek en optionele "
               "externe HTML-tool.",
    "description": """
DAADit — Processtooling (YoMoRo)
================================
Verankert de door YoMoRo geleverde, self-contained HTML-processtools per klant
in Odoo, met een configureerbaar proces-model en herbruikbare templates.

* **Model ``daadit.process``:** een klant kan meerdere processen hebben, elk
  met eigen HTML-tool, versie en **per-proces configuratie**
  (portal-zichtbaarheid, valideren/bewerken toegestaan, volgorde, actief).
* **Templates:** processen met ``is_template=True`` zijn klant-onafhankelijke
  sjablonen. Bij een nieuw klantproces vult een gekozen template naam,
  omschrijving en tool voor — zo start een klant zonder ingericht proces niet
  vanaf nul.
* **Contacts (Company-niveau):** een hoofdschakelaar ``process_tooling_active``
  en een tab **Processtooling** met de processenlijst van de klant.
* **Intern menu "Processtooling":** *Klantprocessen* en *Templates*.
* **Portal ("Mijn account"):** klantmedewerkers zien een kaart **Processen** en
  een overzicht van de portal-zichtbare processen van hún bedrijf; elk proces
  opent in een sandboxed iframe. Toegang strikt via ``commercial_partner_id`` —
  uitsluitend processen van het eigen bedrijf, en alleen als de hoofdschakelaar
  aan staat.

Geen automatische tests: op deze codebase kan een falende test alle
odoo.sh-prod-builds blokkeren, dus dit module houdt zich testvrij.

Bron: YoMoRo AI (Wytse), mail "Procesmodel" 9 juli 2026 — vertrouwelijk.
""",
    "category": "Services",
    "author": "DAADit",
    "website": "https://www.daadit.group",
    "license": "LGPL-3",
    "depends": [
        "contacts",   # res.partner form + Contacts app
        "portal",     # CustomerPortal + portal_my_home (website=True on the
                      # routes is honoured when website is present, but is not
                      # a hard dependency — same as Odoo's own portal).
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/process_views.xml",
        "views/res_partner_views.xml",
        "views/process_tooling_menus.xml",
        "views/portal_templates.xml",
        "data/demo_daadit_process.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
