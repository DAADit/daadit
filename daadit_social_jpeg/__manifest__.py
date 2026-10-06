# -*- coding: utf-8 -*-
{
    "name": "DAADit Social — afbeeldingen automatisch naar JPEG",
    "summary": "Zet PNG's automatisch om naar JPEG zodra een post naar "
               "Instagram gaat, in plaats van de publicatie te blokkeren",
    "description": """
DAADit Social — afbeeldingen automatisch naar JPEG
==================================================
Instagram accepteert alleen jpg/jpeg. Odoo's social-app blokkeert
daarom elke post met een PNG-afbeelding met een "Validation Error" —
terwijl vrijwel alle gegenereerde beelden (schermafbeeldingen,
AI-visuals) PNG zijn.

Deze module haakt in op de Instagram-validatie en zet niet-JPEG-
afbeeldingen ter plekke om naar JPEG (kwaliteit 90, transparantie op
witte achtergrond) vóór de controle draait. Wat de gebruiker uploadt
werkt dus gewoon; alleen bestanden die geen leesbare afbeelding zijn
blijven bij de normale foutmelding.
""",
    "version": "19.0.1.0.0",
    "category": "Marketing/Social Marketing",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": ["social_instagram"],
    "installable": True,
    "application": False,
    "auto_install": True,
}
