# -*- coding: utf-8 -*-
"""Een run verwijst naar zijn verbruiksregel via usage_model + usage_row_id.

De oude koppeling ``usage_id`` wees alleen naar de Mistral-tabel. Waar
alleen die koppeling gevuld was, gaat de verwijzing mee naar de
provider-onafhankelijke velden, zodat de kosten van oude runs blijven
kloppen nadat de kolom verdwijnt.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = 'daadit_ai_agent_schedule_run'"
        " AND column_name = 'usage_id'"
    )
    if not cr.fetchone():
        return
    cr.execute(
        "UPDATE daadit_ai_agent_schedule_run"
        " SET usage_model = 'daadit_ai_mistral.usage',"
        "     usage_row_id = usage_id"
        " WHERE usage_id IS NOT NULL"
        " AND (usage_row_id IS NULL OR usage_row_id = 0"
        "      OR usage_model IS NULL OR usage_model = '')"
    )
