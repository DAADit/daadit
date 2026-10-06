# -*- coding: utf-8 -*-
"""Speech credentials, kept server-side.

Keys live in ``ir.config_parameter`` and never reach the browser: the
client asks *our* endpoints, and those endpoints talk to the providers.
So a colleague can speak and be spoken to without ever being able to
read — or spend — a key.
"""

from odoo import api, fields, models

PARAM_KEY = "daadit_agent_voice.elevenlabs_api_key"
PARAM_MODEL = "daadit_agent_voice.elevenlabs_model"
PARAM_STT_PROVIDER = "daadit_agent_voice.stt_provider"
PARAM_WISPR_KEY = "daadit_agent_voice.wispr_api_key"

# Flash is the cheap, near-instant model — and audibly the flattest of
# the three. It is the right default for a quick back-and-forth, but if
# the voice sounds robotic this is the first thing to change.
DEFAULT_MODEL = "eleven_flash_v2_5"
DEFAULT_STT_PROVIDER = "elevenlabs"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # --- Luisteren (spraak → tekst) -----------------------------------
    daadit_stt_provider = fields.Selection(
        selection=[
            ("elevenlabs", "ElevenLabs Scribe"),
            ("wispr", "Wispr Flow"),
            (
                "elevenlabs_realtime",
                "ElevenLabs Scribe v2 Realtime — schrijft mee terwijl je praat",
            ),
        ],
        string="Spraakherkenning via",
        config_parameter=PARAM_STT_PROVIDER,
        default=DEFAULT_STT_PROVIDER,
        help="Wispr Flow schrijft je woorden uit zoals je ze bedoelde: "
             "stopwoorden eruit, interpunctie erin, en namen van "
             "collega's correct gespeld. Valt automatisch terug op "
             "Scribe als Wispr onbereikbaar is. Realtime schrijft mee tijdens "
             "het praten en valt automatisch terug op de gewone Scribe-route "
             "als de verbinding niet lukt.",
    )
    daadit_wispr_api_key = fields.Char(
        string="Wispr Flow API-sleutel",
        config_parameter=PARAM_WISPR_KEY,
        help="Aan te maken op het Wispr Flow Developer Platform. "
             "Zonder sleutel blijft de spraakherkenning op Scribe staan.",
    )

    # --- Spreken (tekst → spraak) -------------------------------------
    daadit_elevenlabs_api_key = fields.Char(
        string="ElevenLabs API-sleutel",
        config_parameter=PARAM_KEY,
        help="Te vinden in je ElevenLabs-account onder Profile → API Key. "
             "Laat leeg om de ingebouwde browserstemmen te blijven "
             "gebruiken.",
    )
    daadit_elevenlabs_model = fields.Selection(
        selection=[
            ("eleven_flash_v2_5", "Flash v2.5 — snelst, vlakste stem"),
            ("eleven_turbo_v2_5", "Turbo v2.5 — balans tussen beide"),
            ("eleven_multilingual_v2", "Multilingual v2 — natuurlijkst, trager"),
            ("eleven_v3", "v3 — meest expressief (nieuwste)"),
        ],
        string="Stemmodel",
        config_parameter=PARAM_MODEL,
        default=DEFAULT_MODEL,
        help="Klinkt de agent robotachtig? Dan staat dit waarschijnlijk "
             "op Flash. Multilingual v2 klinkt duidelijk natuurlijker, "
             "maar begint een halve seconde later met praten.",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        params = self.env["ir.config_parameter"].sudo()
        # A value written before this field became a selection (or an
        # unknown model id) must not blank the field — fall back to the
        # default so the settings page still renders.
        stored_model = params.get_param(PARAM_MODEL) or DEFAULT_MODEL
        known = dict(
            self._fields["daadit_elevenlabs_model"].selection
        )
        res["daadit_elevenlabs_model"] = (
            stored_model if stored_model in known else DEFAULT_MODEL
        )
        res["daadit_stt_provider"] = (
            params.get_param(PARAM_STT_PROVIDER) or DEFAULT_STT_PROVIDER
        )
        return res
