# -*- coding: utf-8 -*-
"""Bereken "inmiddels opgelost" eenmalig over het laatste kwartaal (OAS).

``resolved_by_run_id`` / ``resolved_on`` / ``is_resolved`` zijn met deze
versie opgeslagen velden geworden. Een upgrade vult een nieuw opgeslagen
berekend veld wel, maar deze berekening kijkt naar *andere* records
(de latere runs van dezelfde planning), en dan is de volgorde waarin
Odoo de rijen langsgaat niet iets om op te bouwen: een run kan berekend
worden voordat zijn oplosser bestaat in de cache.

Daarom hier expliciet, voor de runs waar iemand nog naar kijkt — de
restlijst van Argus kijkt niet verder terug dan een kwartaal.
"""
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_WINDOW_DAYS = 90
_CHUNK = 200


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Run = env["daadit.ai.agent.schedule.run"]
    since = fields.Datetime.now() - timedelta(days=_WINDOW_DAYS)
    runs = Run.search(
        [("start_date", ">=", since)], order="start_date desc, id desc",
    )
    for index in range(0, len(runs), _CHUNK):
        chunk = runs[index:index + _CHUNK]
        chunk._compute_resolution()
        env.flush_all()
        env.invalidate_all()
