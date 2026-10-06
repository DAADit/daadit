{
    'name': 'DAADit — AI Router',
    'version': '19.0.4.3.0',
    'summary': 'Centrale routering van al het AI-verkeer: één punt dat elke '
               'AI-aanroep naar het juiste model stuurt, met fallback en logging.',
    'description': """
DAADit AI Router
================

Fase 1, model-router: alle AI-aanroepen vanuit Odoo lopen via
env['ai.router'].run(purpose, ...). Instelbare routes bepalen per doel welke
provider en welk model gebruikt wordt, met automatische fallback en volledige
logging (latency, tokens, status) per aanroep.

Fase 2, werk-router: werkregels classificeren binnenkomende werkitems
(CRM-leads) via de gateway en leggen een concept-voorstel bij de juiste
collega. Draft-only, idempotent en gethrottled.
""",
    'category': 'Productivity',
    'author': 'DAADit Group',
    'website': 'https://www.daadit.group',
    'license': 'LGPL-3',
    'depends': ['base', 'ai_app', 'mail', 'crm'],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_router_provider_views.xml',
        'views/ai_router_route_views.xml',
        'views/ai_router_dispatch_views.xml',
        'views/ai_router_log_views.xml',
        'views/ai_router_menus.xml',
        'data/ai_router_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
