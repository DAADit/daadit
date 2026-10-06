from odoo import fields, models


class AiRouterRoute(models.Model):
    _name = 'ai.router.route'
    _description = 'AI Router Route'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    purpose = fields.Char(
        required=True, index=True,
        help="Sleutel waarmee aanroepende code deze route kiest, "
             "bijv. 'classify', 'draft' of 'default'.")
    routing_mode = fields.Selection(
        [('auto', 'Automatisch (kostenefficiënt)'), ('fixed', 'Vast model')],
        required=True, default='auto',
        help="Automatisch: de router bepaalt per aanroep de vereiste "
             "capaciteitsklasse (op prompt- en outputlengte) en kiest het "
             "goedkoopste actieve model; bij falen escaleert hij. "
             "Vast: altijd de hieronder gekozen provider en model.")
    provider_id = fields.Many2one('ai.router.provider', required=True)
    model_id = fields.Many2one(
        'ai.router.model',
        domain="[('provider_id', '=', provider_id)]",
        help="Leeg = standaardmodel van de provider. Alleen actieve "
             "modellen zijn selecteerbaar.")
    fallback_provider_id = fields.Many2one('ai.router.provider')
    fallback_model_id = fields.Many2one(
        'ai.router.model',
        domain="[('provider_id', '=', fallback_provider_id)]")
    max_tokens = fields.Integer(default=1024)
    temperature = fields.Float(
        default=-1, help="-1 = standaardwaarde van de provider.")
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
