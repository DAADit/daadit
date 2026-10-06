import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AiRouterModel(models.Model):
    _name = 'ai.router.model'
    _description = 'AI Router Model'
    _order = 'provider_id, sequence, id'

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        help="Technische modelnaam zoals de API die verwacht, "
             "bijv. gpt-4.1 of mistral-large-latest.")
    provider_id = fields.Many2one(
        'ai.router.provider', required=True, ondelete='cascade')
    active = fields.Boolean(
        default=True,
        help="Uitgeschakeld = niet selecteerbaar in routes en uitgesloten "
             "van automatische routering.")
    sequence = fields.Integer(default=10)
    note = fields.Char()
    tier = fields.Selection(
        [('light', 'Licht'), ('standard', 'Standaard'), ('heavy', 'Zwaar')],
        required=True, default='standard',
        help="Capaciteitsklasse. De automatische routering bepaalt per "
             "aanroep de vereiste klasse en kiest daarbinnen het goedkoopste "
             "actieve model; bij falen escaleert hij naar een hogere klasse.")
    cost_in_usd = fields.Float(
        string="Kosten in ($/1M tok)", digits=(12, 4),
        help="Indicatieve prijs per 1 miljoen input-tokens; gebruikt om "
             "kandidaten op kosten te sorteren.")
    cost_out_usd = fields.Float(
        string="Kosten uit ($/1M tok)", digits=(12, 4),
        help="Indicatieve prijs per 1 miljoen output-tokens.")

    rotation_excluded = fields.Boolean(
        string="Uit rotatie",
        help="Volgt de opbouw uit de instructies niet (kwaliteitsmeting "
             "per model). Blijft actief en kiesbaar voor een vaste route "
             "of collega, maar de automatische routering slaat het over.")
    instruction_verdict = fields.Selection(
        [('pass', 'Geslaagd'), ('fail', 'Gezakt'),
         ('untested', 'Niet te beoordelen')],
        string="Instructie-opvolging", readonly=True)
    instruction_checked_at = fields.Datetime(
        string="Gemeten op", readonly=True)
    instruction_detail = fields.Char(string="Uitkomst meting", readonly=True)

    _provider_code_uniq = models.Constraint(
        'unique (provider_id, code)',
        "Dit model bestaat al voor deze provider.",
    )

    # Providerregisters die deze lijst voeden. De providermodules beheren
    # hun eigen modellen; de router spiegelt ze zodat "Providers &
    # modellen" een enkel beeld geeft. Per register meerdere kandidaat-
    # providercodes, omdat de Claude-provider in bestaande databases als
    # 'claude' is aangelegd en in de selectielijst 'anthropic' heet.
    _REGISTRY_SOURCES = {
        'daadit.ai.mistral.model': ('mistral',),
        'daadit.ai.claude.model': ('claude', 'anthropic'),
        'daadit.ai.loes.model': ('loes',),
    }

    @api.model
    def _sync_from_registries(self):
        """Spiegel de providerregisters naar deze lijst; aantal rijen terug.

        De providermodules roepen dit aan na elke wijziging in hun eigen
        register (API-sync, handmatige edit, archivering). Ontbreekt een
        register of de bijbehorende provider, dan blijft die kant leeg in
        plaats van een fout. Tier en kosten blijven van de beheerder:
        alleen naam, volgorde en actief worden gespiegeld.
        """
        Provider = self.env['ai.router.provider'].sudo()
        Model = self.sudo().with_context(active_test=False)
        touched = 0
        for registry, provider_codes in self._REGISTRY_SOURCES.items():
            if registry not in self.env:
                continue
            provider = Provider.search(
                [('code', 'in', list(provider_codes))], limit=1)
            if not provider:
                continue
            rows = self.env[registry].sudo().with_context(
                active_test=False).search([])
            for row in rows:
                code = (row.technical_name or '').strip()
                if not code:
                    continue
                vals = {
                    'name': row.label or code,
                    'sequence': row.sequence,
                    'active': row.active,
                }
                existing = Model.search([
                    ('provider_id', '=', provider.id),
                    ('code', '=', code),
                ], limit=1)
                if existing:
                    existing.write(vals)
                else:
                    Model.create(dict(
                        vals, code=code, provider_id=provider.id))
                touched += 1
        _logger.info(
            "ai.router: %d modellen gespiegeld uit de providerregisters",
            touched)
        return touched
