# -*- coding: utf-8 -*-
"""Expose the agent's voice preferences to the browser.

The chat UI needs to know *how* to speak before it can speak. Reading
``ai.agent`` straight from JS would need read access on the agent model
for every chat user, so we go through the channel instead: the record
rules on ``discuss.channel`` already decide who may see this
conversation, and we only ever hand back the six presentation fields —
never the prompt, the tools or the model configuration.
"""

from datetime import timedelta

from odoo import api, fields, models


SPOKEN_MODE_TTL_MINUTES = 20


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    daadit_voice_spoken_until = fields.Datetime(
        string="Spraakmodus tot",
        copy=False,
    )

    @api.model
    def daadit_voice_config(self, channel_id):
        """Return the voice settings for the agent behind ``channel_id``.

        :return: dict with the voice preferences, or ``{'enabled': False}``
                 when this channel has no agent or voice is switched off.
        """
        channel = self.browse(int(channel_id)).exists()
        # Read as the current user first: this raises if they may not
        # see the channel, which is exactly the check we want.
        if not channel:
            return {"enabled": False}
        channel.check_access("read")
        agent = channel.sudo().ai_agent_id
        if not agent or not agent.daadit_voice_enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lang": agent.daadit_voice_lang or "nl-NL",
            "gender": agent.daadit_voice_gender or "any",
            "voice_name": agent.daadit_voice_name or "",
            "rate": agent.daadit_voice_rate or 1.0,
            "pitch": agent.daadit_voice_pitch or 1.0,
            "handsfree": bool(agent.daadit_voice_handsfree),
            # Which engine to try first. We deliberately do not reveal
            # whether a key is configured: the client simply asks the
            # endpoint and falls back when it gets nothing back.
            "provider": agent.daadit_voice_provider or "browser",
            "ptt_hotkey": (
                self.env.user.daadit_voice_ptt_hotkey or "alt+shift"
            ),
        }

    @api.model
    def daadit_voice_set_spoken(self, channel_id, active, minutes=None):
        """Markeer een kanaal tijdelijk als een gesprek dat wordt voorgelezen.

        De vervaltijd voorkomt dat een gesloten tabblad of gecrashte browser
        elk later antwoord blijvend als gesproken en dus extra kort markeert.
        """
        channel = self.browse(int(channel_id)).exists()
        if not channel:
            return False
        channel.check_access("read")
        if active:
            try:
                minutes = int(minutes) if minutes is not None else (
                    SPOKEN_MODE_TTL_MINUTES
                )
            except (TypeError, ValueError):
                minutes = SPOKEN_MODE_TTL_MINUTES
            minutes = max(1, min(minutes, 60))
            channel.sudo().daadit_voice_spoken_until = (
                fields.Datetime.now() + timedelta(minutes=minutes)
            )
        else:
            channel.sudo().daadit_voice_spoken_until = False
        return True

    def daadit_voice_spoken_mode(self):
        """Return whether this channel is still within its spoken-mode TTL.

        We use a timestamp instead of a boolean so a closed tab or crashed
        browser cannot leave every agent answer terse forever.
        """
        self.ensure_one()
        until = self.sudo().daadit_voice_spoken_until
        return bool(until and until >= fields.Datetime.now())
