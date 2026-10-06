# -*- coding: utf-8 -*-
"""Fix haal_claude_design tool body: safe_eval has no ``locals()``."""

_CODE = """# safe_eval has no locals(); optional args may be missing entirely.
try:
    _url = project_url
except NameError:
    _url = None
ai['result'] = record._daadit_claude_design_fetch(project_url=_url)"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_act_server
           SET code = %s
         WHERE id IN (
            SELECT res_id FROM ir_model_data
             WHERE module = 'daadit_claude_design'
               AND name = 'ir_actions_server_haal_claude_design'
               AND model = 'ir.actions.server'
         )
        """,
        (_CODE,),
    )
