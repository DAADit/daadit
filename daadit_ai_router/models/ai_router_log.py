from odoo import fields, models


class AiRouterLog(models.Model):
    _name = 'ai.router.log'
    _description = 'AI Router Log'
    _order = 'create_date desc, id desc'

    purpose = fields.Char(index=True)
    route_id = fields.Many2one('ai.router.route', ondelete='set null')
    provider_id = fields.Many2one('ai.router.provider', ondelete='set null')
    model_name = fields.Char()
    caller = fields.Char(help="Aanroepende module of context (vrij veld).")
    routing = fields.Char(
        string="Routering",
        help="Hoe het model gekozen is, bijv. 'auto/light' of 'vast'.")
    status = fields.Selection(
        [
            ('ok', 'OK'),
            ('fallback', 'OK via fallback'),
            ('error', 'Fout'),
        ],
        default='ok', index=True,
    )
    latency_ms = fields.Integer(string="Latency (ms)")
    tokens_prompt = fields.Integer()
    tokens_completion = fields.Integer()
    error = fields.Text()
