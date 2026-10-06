# -*- coding: utf-8 -*-
import hashlib
import secrets
import uuid

from werkzeug.urls import url_encode

from odoo import _, fields, models
from odoo.tools.urls import urljoin as url_join


class SocialMedia(models.Model):
    _inherit = 'social.media'

    _TIKTOK_OAUTH_ENDPOINT = 'https://www.tiktok.com/v2/auth/authorize/'
    _TIKTOK_TOKEN_ENDPOINT = 'https://open.tiktokapis.com/v2/oauth/token/'
    _TIKTOK_API_ENDPOINT = 'https://open.tiktokapis.com/v2/'

    media_type = fields.Selection(selection_add=[('tiktok', 'TikTok')])

    def _action_add_account(self):
        self.ensure_one()

        if self.media_type != 'tiktok':
            return super()._action_add_account()

        tiktok_client_key = self.env['ir.config_parameter'].sudo().get_param(
            'social.tiktok_client_key')
        tiktok_client_secret = self.env['ir.config_parameter'].sudo().get_param(
            'social.tiktok_client_secret')

        if not tiktok_client_key or not tiktok_client_secret:
            # No TikTok developer app configured yet: open a small wizard that
            # asks for the client key/secret and then continues straight to
            # TikTok's consent page (instead of a blocking error).
            return {
                'type': 'ir.actions.act_window',
                'name': _('Connect TikTok'),
                'res_model': 'social.tiktok.credentials.wizard',
                'view_mode': 'form',
                'views': [[False, 'form']],
                'target': 'new',
            }

        return self._tiktok_oauth_redirect(tiktok_client_key)

    def _tiktok_oauth_redirect(self, tiktok_client_key):
        self.ensure_one()
        # Generate a CSRF state token stored temporarily in system parameters
        state = str(uuid.uuid4())
        self.env['ir.config_parameter'].sudo().set_param('social.tiktok_oauth_state', state)

        # TikTok requires PKCE; note that it expects a HEX-encoded SHA256
        # challenge (not the base64url encoding from RFC 7636).
        code_verifier = secrets.token_urlsafe(48)
        self.env['ir.config_parameter'].sudo().set_param(
            'social.tiktok_oauth_code_verifier', code_verifier)
        code_challenge = hashlib.sha256(code_verifier.encode()).hexdigest()

        scopes = ['user.info.basic', 'video.list', 'video.publish']
        params = {
            'client_key': tiktok_client_key,
            'redirect_uri': url_join(self.get_base_url(), 'social_tiktok/callback'),
            'response_type': 'code',
            'scope': ','.join(scopes),
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }

        return {
            'type': 'ir.actions.act_url',
            'url': '%s?%s' % (self._TIKTOK_OAUTH_ENDPOINT, url_encode(params)),
            'target': 'self',
        }
