# -*- coding: utf-8 -*-
"""Per-agent voice settings.

The actual speaking happens in the browser (Web Speech API), so what we
store here is a *preference*, not a file: which language to speak, which
gender to prefer, and optionally one exact voice name. The exact voice
list differs per operating system and browser, so ``voice_name`` is a
best-effort hint — the client falls back to language + gender when the
named voice is not installed on the listener's machine.
"""

from odoo import fields, models


class AIAgent(models.Model):
    _inherit = "ai.agent"

    daadit_voice_enabled = fields.Boolean(
        string="Praten met deze agent",
        default=True,
        help="Toont de microfoonknop in de chat en laat de agent zijn "
             "antwoord hardop voorlezen.",
    )
    daadit_voice_lang = fields.Selection(
        selection=[
            ("nl-NL", "Nederlands"),
            ("nl-BE", "Nederlands (België)"),
            ("en-GB", "Engels (VK)"),
            ("en-US", "Engels (VS)"),
            ("de-DE", "Duits"),
            ("fr-FR", "Frans"),
        ],
        string="Taal",
        default="nl-NL",
        help="Taal voor zowel het luisteren als het voorlezen.",
    )
    daadit_voice_gender = fields.Selection(
        selection=[
            ("female", "Vrouwelijk"),
            ("male", "Mannelijk"),
            ("any", "Maakt niet uit"),
        ],
        string="Stemtype",
        default="any",
        help="De browser krijgt geen geslacht mee bij een stem; we "
             "leiden het af uit de stemnaam. Kies hieronder een "
             "specifieke stem als je het exact wilt vastleggen.",
    )
    daadit_voice_name = fields.Char(
        string="Specifieke stem",
        help="Exacte naam van een stem die op jouw computer staat. "
             "Leeg laten = automatisch kiezen op taal en stemtype.",
    )
    daadit_voice_rate = fields.Float(
        string="Spreeksnelheid",
        default=1.0,
        help="1,0 is normaal. Lager is trager, hoger is sneller "
             "(0,5 tot 2,0).",
    )
    daadit_voice_pitch = fields.Float(
        string="Toonhoogte",
        default=1.0,
        help="1,0 is normaal. Lager klinkt zwaarder, hoger lichter "
             "(0,5 tot 2,0).",
    )
    daadit_voice_handsfree = fields.Boolean(
        string="Handsfree gesprek",
        default=False,
        help="Zet de microfoon automatisch weer aan zodra de agent "
             "klaar is met praten, zodat je door kunt praten zonder "
             "te klikken.",
    )
    daadit_voice_provider = fields.Selection(
        selection=[
            ("browser", "Browserstem (gratis)"),
            ("elevenlabs", "ElevenLabs (natuurlijk, betaald)"),
        ],
        string="Stemmotor",
        default="browser",
        help="ElevenLabs klinkt aanzienlijk natuurlijker maar kost "
             "credits per gesproken teken. Zonder ingestelde sleutel "
             "valt hij automatisch terug op de browserstem.",
    )
    daadit_elevenlabs_voice_id = fields.Char(
        string="ElevenLabs-stem",
        help="Stem-ID uit je ElevenLabs-bibliotheek. Kies er een uit de "
             "lijst hieronder — die wordt live opgehaald zodra de "
             "sleutel is ingesteld.",
    )
    daadit_elevenlabs_stability = fields.Float(
        string="Stabiliteit",
        default=0.5,
        help="Lager = meer expressie en variatie, hoger = vlakker maar "
             "voorspelbaarder. 0,5 is een goed startpunt.",
    )
    daadit_elevenlabs_similarity = fields.Float(
        string="Gelijkenis met origineel",
        default=0.75,
        help="Hoe strak de stem bij het origineel blijft. Rond 0,75 "
             "klinkt meestal het natuurlijkst.",
    )
