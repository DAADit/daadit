import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AiRouterProvider(models.Model):
    _name = 'ai.router.provider'
    _description = 'AI Router Provider'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    code = fields.Selection(
        [
            ('mistral', 'Mistral AI'),
            ('openai', 'ChatGPT (OpenAI)'),
            ('google', 'Google Gemini'),
            ('anthropic', 'Anthropic Claude'),
            ('loes', 'Loes.ai (HostYourAI)'),
        ],
        required=True,
        help="Komt overeen met de AI-diensten in Instellingen → AI. De "
             "sleutel wordt daar beheerd; deze module leest hem mee.",
    )
    base_url = fields.Char(
        help="Laat leeg voor de URL uit de centrale AI-instellingen of de "
             "standaard API-URL van de dienst.")
    api_key = fields.Char(
        help="Laat leeg om de sleutel uit de centrale AI-instellingen te "
             "hergebruiken (Instellingen → AI).")
    default_model = fields.Char(
        required=True,
        help="Bijv. mistral-large-latest, gpt-4.1, gemini-2.5-flash.")
    timeout = fields.Integer(default=60)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    note = fields.Text()
    model_ids = fields.One2many(
        'ai.router.model', 'provider_id', string="Modellen")
    service_configured = fields.Boolean(
        compute='_compute_service_configured',
        help="Waar zodra de dienst een sleutel heeft (eigen veld of "
             "Instellingen → AI).")
    models_last_sync = fields.Datetime(
        string="Modellen laatst gesynct", readonly=True,
        help="Wanneer de modellenlijst voor het laatst uit de provider-API "
             "is opgehaald.")
    model_count = fields.Integer(
        compute='_compute_model_count', string="# Modellen")

    def _compute_service_configured(self):
        for provider in self:
            provider.service_configured = bool(provider._get_api_key())

    @api.depends('model_ids', 'model_ids.active')
    def _compute_model_count(self):
        for provider in self:
            provider.model_count = len(provider.model_ids)

    @api.constrains('active')
    def _check_active_configured(self):
        for provider in self.filtered('active'):
            if not provider._get_api_key():
                raise ValidationError(_(
                    "%s is nog niet geconfigureerd. Stel de dienst eerst in "
                    "onder Instellingen → AI, of vul hier een eigen sleutel "
                    "in.", provider.name))

    def action_open_ai_settings(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _("AI-instellingen"),
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'ai_app'},
        }

    # Mistral, OpenAI en Gemini spreken het OpenAI-compatibele
    # chat.completions-schema (Gemini via de openai-compatibiliteitslaag).
    # Anthropic gebruikt zijn eigen Messages-API (/v1/messages) — zie
    # _chat_anthropic; het endpoint hieronder wordt daar rechtstreeks gebruikt.
    _DEFAULT_URLS = {
        'mistral': 'https://api.mistral.ai/v1/chat/completions',
        # Loes draait op de HostYourAI EU-router en spreekt hetzelfde
        # OpenAI-compatibele schema; geen eigen tak nodig.
        'loes': 'https://hostyourai.com/api/v1/chat/completions',
        'openai': 'https://api.openai.com/v1/chat/completions',
        'google': 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
        'anthropic': 'https://api.anthropic.com/v1/messages',
    }

    # Anthropic Messages-API-versie (verplichte header).
    _ANTHROPIC_VERSION = '2023-06-01'

    # Sleutels zoals de centrale AI-instellingen ze opslaan:
    # ai_app (native) voor ChatGPT/Gemini, daadit_ai_mistral voor Mistral.
    # Anthropic heeft (nog) geen native ai_app-sleutel; vul hem op de provider
    # in, of zet de config-parameter daadit_ai_router.anthropic_key.
    _CONFIG_PARAMS = {
        'mistral': {
            'api_key': 'daadit_ai_mistral.mistral_key',
            'enabled': 'daadit_ai_mistral.mistral_key_enabled',
            'base_url': 'daadit_ai_mistral.mistral_base_url',
        },
        'openai': {
            'api_key': 'ai.openai_key',
        },
        'google': {
            'api_key': 'ai.google_key',
        },
        'anthropic': {
            'api_key': 'daadit_ai_router.anthropic_key',
        },
        # Zelfde opzet als Mistral: de sleutel wordt beheerd in
        # Instellingen -> AI door daadit_ai_loes, wij lezen hem mee.
        'loes': {
            'api_key': 'daadit_ai_loes.loes_key',
            'enabled': 'daadit_ai_loes.loes_key_enabled',
            'base_url': 'daadit_ai_loes.loes_base_url',
        },
    }

    def _config_param(self, name):
        self.ensure_one()
        param = self._CONFIG_PARAMS.get(self.code, {}).get(name)
        if not param:
            return False
        return self.env['ir.config_parameter'].sudo().get_param(param) or False

    def _get_api_key(self):
        self.ensure_one()
        if self.api_key:
            return self.api_key
        enabled = self._config_param('enabled')
        if enabled and str(enabled).strip().lower() in ('false', '0'):
            return False
        return self._config_param('api_key')

    def _endpoint(self):
        self.ensure_one()
        url = self.base_url or self._config_param('base_url')
        if not url:
            return self._DEFAULT_URLS[self.code]
        url = url.strip().rstrip('/')
        # Anthropic Messages-API: kaal domein → /v1/messages, anders letterlijk.
        if self.code == 'anthropic':
            if url.endswith('/messages'):
                return url
            if not url.endswith('/v1'):
                url += '/v1'
            return url + '/messages'
        # OpenAI-compatibele diensten: accepteert kale domein-URL, URL t/m /v1
        # (of /openai) of volledig endpoint.
        if url.endswith('/chat/completions'):
            return url
        if not url.endswith(('/v1', '/openai', '/v1beta')):
            url += '/v1'
        return url + '/chat/completions'

    # ------------------------------------------------------------------ #
    # Model sync — haal de beschikbare modellen live uit de provider-API #
    # (GET /models), zodat nieuwe modellen vanzelf verschijnen.          #
    # ------------------------------------------------------------------ #

    # Modellen-endpoints per dienst. OpenAI-compatibele diensten
    # (mistral/openai/google) leveren {"data":[{"id":...}]}; Anthropic
    # levert {"data":[{"id":...,"display_name":...}]}.
    _MODELS_URLS = {
        'mistral': 'https://api.mistral.ai/v1/models',
        'loes': 'https://hostyourai.com/api/v1/models',
        'openai': 'https://api.openai.com/v1/models',
        'google': 'https://generativelanguage.googleapis.com/v1beta/openai/models',
        'anthropic': 'https://api.anthropic.com/v1/models',
    }

    # Model-ids die geen chat-modellen zijn en we niet in de lijst willen.
    _NON_CHAT_MARKERS = (
        'embed', 'whisper', 'tts', 'dall-e', 'dalle', 'moderation',
        'rerank', 'ocr', 'transcribe', 'image-', 'audio-',
    )

    def _models_endpoint(self):
        self.ensure_one()
        base = self.base_url or self._config_param('base_url')
        if base:
            base = base.strip().rstrip('/')
            if self.code == 'anthropic':
                if not base.endswith('/v1'):
                    base += '/v1'
                return base + '/models'
            # Strip een volledig chat-endpoint terug naar de API-root.
            for suffix in ('/chat/completions', '/messages'):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            if not base.endswith(('/v1', '/openai', '/v1beta')):
                base += '/v1'
            return base + '/models'
        return self._MODELS_URLS[self.code]

    def _models_headers(self, api_key):
        if self.code == 'anthropic':
            return {
                'x-api-key': api_key,
                'anthropic-version': self._ANTHROPIC_VERSION,
                'Accept': 'application/json',
            }
        return {
            'Authorization': 'Bearer %s' % api_key,
            'Accept': 'application/json',
        }

    def _sync_models(self):
        """Haal de modellenlijst op en upsert ai.router.model. Retourneert
        het aantal verwerkte modellen. Verheft een fout als de API faalt."""
        self.ensure_one()
        api_key = self._get_api_key()
        if not api_key:
            raise UserError(_(
                "%s heeft geen API-sleutel. Stel hem in onder "
                "Instellingen → AI.", self.name))
        resp = requests.get(
            self._models_endpoint(),
            headers=self._models_headers(api_key),
            timeout=self.timeout or 60,
        )
        if resp.status_code >= 400:
            raise UserError(_(
                "Modellen ophalen bij %(name)s mislukte (HTTP %(status)s): "
                "%(body)s",
                name=self.name, status=resp.status_code,
                body=(resp.text or '')[:300]))
        data = (resp.json() or {}).get('data') or []
        Model = self.env['ai.router.model'].sudo()
        seen = 0
        for seq, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            code = (item.get('id') or '').strip()
            if not code:
                continue
            low = code.lower()
            if any(m in low for m in self._NON_CHAT_MARKERS):
                continue
            label = (item.get('display_name') or item.get('name') or code)
            existing = Model.search([
                ('provider_id', '=', self.id), ('code', '=', code),
            ], limit=1)
            vals = {'name': label, 'sequence': seq}
            if existing:
                existing.write(vals)
            else:
                Model.create(dict(
                    vals, code=code, provider_id=self.id, active=True))
            seen += 1
        self.models_last_sync = fields.Datetime.now()
        _logger.info(
            "ai.router: synced %d modellen voor provider %s", seen, self.name)
        return seen

    def action_sync_models(self):
        total = 0
        for provider in self:
            total += provider._sync_models()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Modellen bijgewerkt"),
                'message': _("%s model(len) opgehaald uit de provider-API.",
                             total),
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    @api.model
    def _cron_sync_all_models(self):
        """Dagelijkse sync voor elke geconfigureerde provider. Faalt nooit —
        een tijdelijke API-fout mag de cron niet laten struikelen."""
        providers = self.search([])
        for provider in providers:
            if not provider._get_api_key():
                continue
            try:
                provider._sync_models()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "ai.router: modellen-sync mislukt voor %s", provider.name)
        return True

    def _chat(self, model_name, messages, max_tokens=1024, temperature=None,
              tools=None):
        """Voer één chat-aanroep uit bij deze dienst.

        :param messages: lijst van {'role': ..., 'content': ...}
        :param tools: optionele tool-definities (bv. Anthropic web_search) —
            momenteel alleen doorgegeven aan de Anthropic-adapter.
        :return: dict {'content': str, 'usage': {'prompt': int, 'completion': int}}
        """
        self.ensure_one()
        api_key = self._get_api_key()
        if not api_key:
            raise UserError(_(
                "Dienst %s heeft geen API-sleutel. Stel hem in onder "
                "Instellingen → AI, of vul het sleutelveld van deze provider "
                "in.", self.name))
        # Anthropic spreekt zijn eigen Messages-schema; de rest OpenAI-compat.
        if self.code == 'anthropic':
            return self._chat_anthropic(
                api_key, model_name, messages, max_tokens, temperature,
                tools=tools)
        payload = {
            'model': model_name or self.default_model,
            'messages': messages,
            'max_tokens': max_tokens,
        }
        if temperature is not None:
            payload['temperature'] = temperature
        response = requests.post(
            self._endpoint(),
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Bearer %s' % api_key,
            },
            json=payload,
            timeout=self.timeout or 60,
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get('usage', {})
        return {
            'content': data['choices'][0]['message']['content'],
            'usage': {
                'prompt': usage.get('prompt_tokens'),
                'completion': usage.get('completion_tokens'),
            },
        }

    def _chat_anthropic(self, api_key, model_name, messages, max_tokens,
                        temperature, tools=None):
        """Chat-aanroep via de Anthropic Messages-API (/v1/messages).

        Wijkt af van OpenAI-compat op drie punten: (1) x-api-key +
        anthropic-version-headers i.p.v. Bearer, (2) system-prompts staan in
        een apart top-level 'system'-veld (niet in de messages-lijst), en
        (3) het antwoord staat in content[]-blokken met usage als
        input_tokens/output_tokens. We normaliseren terug naar hetzelfde
        return-contract als _chat zodat de router-laag agnostisch blijft.
        """
        self.ensure_one()
        system_parts = [
            m.get('content', '') for m in messages if m.get('role') == 'system']
        chat_messages = [
            {'role': m['role'], 'content': m['content']}
            for m in messages if m.get('role') in ('user', 'assistant')]
        payload = {
            'model': model_name or self.default_model,
            'messages': chat_messages,
            'max_tokens': max_tokens,
        }
        if system_parts:
            payload['system'] = '\n\n'.join(p for p in system_parts if p)
        if temperature is not None:
            payload['temperature'] = temperature
        if tools:
            # bv. [{'type': 'web_search_20250305', 'name': 'web_search'}] —
            # Anthropic voert de zoekopdracht server-side uit en levert de
            # tekst met citaties terug in dezelfde response.
            payload['tools'] = tools
        response = requests.post(
            self._endpoint(),
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': self._ANTHROPIC_VERSION,
            },
            json=payload,
            timeout=self.timeout or 60,
        )
        response.raise_for_status()
        data = response.json()
        content = ''.join(
            block.get('text', '')
            for block in data.get('content', [])
            if block.get('type') == 'text')
        usage = data.get('usage', {})
        return {
            'content': content,
            'usage': {
                'prompt': usage.get('input_tokens'),
                'completion': usage.get('output_tokens'),
            },
        }
