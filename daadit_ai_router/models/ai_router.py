import logging
import time

from odoo import SUPERUSER_ID, _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Escalatievolgorde van capaciteitsklassen.
TIERS = ['light', 'standard', 'heavy']

# Maximaal aantal modellen dat één aanroep achtereenvolgens probeert.
MAX_ATTEMPTS = 4


class AiRouter(models.AbstractModel):
    """Centrale ingang voor alle AI-aanroepen binnen Odoo.

    Gebruik vanuit elke module::

        result = env['ai.router'].run('classify', "Welke afdeling ...")
        result['content']  # het antwoord van het model

    Standaard routeert de router automatisch en kostenefficiënt: hij bepaalt
    per aanroep de vereiste capaciteitsklasse (licht/standaard/zwaar) op
    prompt- en outputlengte, en kiest daarbinnen het goedkoopste actieve
    model. Faalt een model, dan escaleert hij naar de volgende kandidaat.
    Routes met 'Vast model' behouden het oude gedrag. Elke aanroep wordt
    gelogd in ai.router.log, inclusief de routeringskeuze.
    """

    _name = 'ai.router'
    _description = 'AI Router Service'

    @api.model
    def run(self, purpose, messages, caller=None, **opts):
        """Routeer één AI-aanroep naar het beste beschikbare model.

        :param purpose: routesleutel; onbekend doel valt terug op 'default'
        :param messages: string (wordt user-message) of lijst chat-messages
        :param caller: vrije aanduiding van de aanroeper, voor het logboek
        :param opts: optioneel: max_tokens, temperature, tier
            ('light'/'standard'/'heavy' om de klassekeuze te forceren)
        :return: dict {'content', 'provider', 'model', 'usage', 'status',
            'routing', 'log_id'}
        """
        if isinstance(messages, str):
            messages = [{'role': 'user', 'content': messages}]

        Route = self.env['ai.router.route'].sudo()
        route = (Route.search([('purpose', '=', purpose)], limit=1)
                 or Route.search([('purpose', '=', 'default')], limit=1))
        if not route:
            raise UserError(_(
                "Geen AI-route gevonden voor '%s' en geen 'default'-route "
                "ingesteld.", purpose))

        temperature = route.temperature if route.temperature >= 0 else None
        if 'temperature' in opts:
            temperature = opts['temperature']
        max_tokens = opts.get('max_tokens') or route.max_tokens

        if route.routing_mode == 'fixed':
            routing = 'vast'
            attempts = [(route.provider_id, route.model_id.code)]
            if route.fallback_provider_id:
                attempts.append(
                    (route.fallback_provider_id,
                     route.fallback_model_id.code))
        else:
            tier = opts.get('tier') or self._required_tier(
                messages, max_tokens)
            routing = 'auto/%s' % tier
            candidates = self._candidate_models(tier, messages, max_tokens)
            attempts = [(m.provider_id, m.code) for m in candidates]
            if not attempts:
                raise UserError(_(
                    "Geen actief model beschikbaar voor automatische "
                    "routering (klasse '%s'). Activeer een provider en "
                    "bijpassende modellen.", tier))

        last_error = None
        for index, (provider, model_name) in enumerate(attempts):
            provider = provider.sudo()
            if not provider.active:
                continue
            started = time.monotonic()
            try:
                result = provider._chat(
                    model_name, messages,
                    max_tokens=max_tokens, temperature=temperature,
                    tools=opts.get('tools'))
            except Exception as exc:  # elke fout mag naar de volgende kandidaat
                last_error = exc
                _logger.warning(
                    "AI-route %s (%s): model %s bij %s faalde (%s)",
                    route.purpose, routing, model_name, provider.name, exc)
                continue
            status = 'fallback' if index else 'ok'
            log = self._log(
                route, provider, model_name or provider.default_model,
                caller, status, started, result['usage'], routing=routing)
            return {
                'content': result['content'],
                'provider': provider.code,
                'model': model_name or provider.default_model,
                'usage': result['usage'],
                'status': status,
                'routing': routing,
                'log_id': log.id,
            }

        self._log_isolated(self._log_vals(
            route, route.provider_id,
            route.model_id.code or route.provider_id.default_model,
            caller, 'error', time.monotonic(), {}, error=str(last_error),
            routing=routing))
        raise UserError(_(
            "Alle modellen voor route '%s' faalden. Laatste fout: %s",
            route.purpose, last_error))

    @api.model
    def _required_tier(self, messages, max_tokens):
        """Bepaal de vereiste capaciteitsklasse op meetbare kenmerken.

        Deterministisch en gratis: promptlengte en gevraagde outputlengte.
        Aanroepers kunnen de klasse overrulen via opts['tier']."""
        text_len = sum(len(str(m.get('content') or '')) for m in messages)
        if text_len > 8000 or max_tokens > 2000:
            return 'heavy'
        if text_len <= 2500 and max_tokens <= 600:
            return 'light'
        return 'standard'

    @api.model
    def _candidate_models(self, tier, messages, max_tokens):
        """Actieve modellen vanaf de vereiste klasse, goedkoopste eerst.

        Binnen elke klasse gesorteerd op geschatte kosten voor déze aanroep
        (inputlengte × inputprijs + max_tokens × outputprijs); daarna
        escaleert de lijst naar hogere klassen. Gemaximeerd op MAX_ATTEMPTS.
        """
        escalation = TIERS[TIERS.index(tier):]
        text_len = sum(len(str(m.get('content') or '')) for m in messages)
        est_in = max(text_len // 4, 1)

        def est_cost(model):
            cost = (model.cost_in_usd * est_in
                    + model.cost_out_usd * max_tokens)
            # Modellen zonder prijsdata achteraan binnen hun klasse.
            return cost if cost > 0 else float('inf')

        Model = self.env['ai.router.model'].sudo()
        candidates = []
        for level in escalation:
            models = Model.search([
                ('tier', '=', level),
                ('provider_id.active', '=', True),
                ('rotation_excluded', '=', False),
            ]).filtered(lambda m: m.provider_id._get_api_key())
            candidates += sorted(models, key=est_cost)
        return candidates[:MAX_ATTEMPTS]

    @api.model
    def _log_vals(self, route, provider, model_name, caller, status, started,
                  usage, error=None, routing=None):
        return {
            'purpose': route.purpose,
            'route_id': route.id,
            'provider_id': provider.id,
            'model_name': model_name,
            'caller': caller,
            'routing': routing,
            'status': status,
            'latency_ms': int((time.monotonic() - started) * 1000),
            'tokens_prompt': usage.get('prompt') or 0,
            'tokens_completion': usage.get('completion') or 0,
            'error': error,
        }

    @api.model
    def _log(self, route, provider, model_name, caller, status, started,
             usage, error=None, routing=None):
        return self.env['ai.router.log'].sudo().create(self._log_vals(
            route, provider, model_name, caller, status, started, usage,
            error=error, routing=routing))

    @api.model
    def _log_isolated(self, vals):
        """Schrijf een foutregel in een eigen cursor, zodat hij de rollback
        van de mislukte aanroep overleeft en zichtbaar blijft in het logboek."""
        try:
            with self.env.registry.cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})['ai.router.log'] \
                    .create(vals)
        except Exception:
            _logger.exception(
                "AI-router: foutlog kon niet worden weggeschreven")
