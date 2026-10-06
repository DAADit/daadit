# -*- coding: utf-8 -*-
{
    "name": "DAADit AI — Praten met je agents",
    "summary": "Spreek je AI-agents aan met je stem en laat ze hardop antwoorden",
    "description": """
DAADit AI — Praten met je agents
=================================
Voegt spraak toe aan de AI-chat van Odoo 19:

* **Microfoonknop** in het chatvenster — je spreekt, je woorden gaan als
  bericht naar de agent.
* **De agent antwoordt hardop**, met een stem die je per agent instelt
  (mannelijk / vrouwelijk / een specifieke stem uit je browser).
* **Handsfree-stand** — na het antwoord gaat de microfoon vanzelf weer
  aan, zodat je een doorlopend gesprek voert.

Opname via MediaRecorder (alle moderne browsers); uitschrijven en
voorlezen bij voorkeur via ElevenLabs (server-side), met terugval op
de Web Speech API in Chrome/Edge wanneer er geen sleutel of tegoed is.
""",
    "version": "19.0.12.0.0",
    "category": "Productivity/Discuss",
    "author": "DAADit",
    "website": "https://daadit.group",
    "license": "LGPL-3",
    "depends": ["ai", "mail"],
    "data": [
        "views/ai_agent_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "daadit_agent_voice/static/src/audio_recorder.js",
            "daadit_agent_voice/static/src/wav_encode.js",
            "daadit_agent_voice/static/src/realtime_stt.js",
            "daadit_agent_voice/static/src/voice_service.js",
            "daadit_agent_voice/static/src/composer_voice_action.js",
            "daadit_agent_voice/static/src/message_speak_patch.js",
            "daadit_agent_voice/static/src/agent_steps.js",
            "daadit_agent_voice/static/src/agent_steps.xml",
            "daadit_agent_voice/static/src/open_agent_chat.js",
            "daadit_agent_voice/static/src/voice_picker_field.js",
            "daadit_agent_voice/static/src/elevenlabs_picker_field.js",
            "daadit_agent_voice/static/src/elevenlabs_picker_field.xml",
            "daadit_agent_voice/static/src/voice_picker_field.xml",
            "daadit_agent_voice/static/src/voice.scss",
        ],
    },
    "installable": True,
    "application": False,
}
