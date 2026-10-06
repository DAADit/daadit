# -*- coding: utf-8 -*-
"""Fix AI tool code for Odoo 19 safe_eval builtins.

``except NameError`` is invalid inside safe_eval: NameError is not among
the allowed builtins, so a missing optional argument crashed the tool
with ``NameError("name 'NameError' is not defined")``.
"""

_HAAL_CODE = """# safe_eval: optional args may be missing; builtins have Exception only.
try:
    _url = project_url
except Exception:
    _url = None
ai['result'] = record._daadit_claude_design_fetch(project_url=_url)"""

_VISUAL_CODE = (
    "aid = record.id\n"
    "if aid not in (10, 15):\n"
    "    ai['result'] = {\n"
    "        'ok': False,\n"
    "        'error': (\n"
    "            'SCOPE-GUARD: deze tool is voor Penny (content) '\n"
    "            'en Mark (marketing).'\n"
    "        ),\n"
    "    }\n"
    "else:\n"
    "    try:\n"
    "        _tpl = template\n"
    "    except Exception:\n"
    "        _tpl = ''\n"
    "    try:\n"
    "        _sub = subline\n"
    "    except Exception:\n"
    "        _sub = ''\n"
    "    try:\n"
    "        _pid = post_id\n"
    "    except Exception:\n"
    "        _pid = 0\n"
    "    ai['result'] = record._daadit_render_template_visual(\n"
    "        headline=headline,\n"
    "        subline=_sub or '',\n"
    "        post_id=_pid or 0,\n"
    "        template=_tpl or '',\n"
    "    )"
)


def migrate(cr, version):
    for xmlid, code in (
        ("ir_actions_server_haal_claude_design", _HAAL_CODE),
        ("ir_actions_server_maak_social_visual", _VISUAL_CODE),
    ):
        cr.execute(
            """
            UPDATE ir_act_server
               SET code = %s
             WHERE id IN (
                SELECT res_id FROM ir_model_data
                 WHERE module = 'daadit_claude_design'
                   AND name = %s
                   AND model = 'ir.actions.server'
             )
            """,
            (code, xmlid),
        )
