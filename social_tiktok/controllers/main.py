# -*- coding: utf-8 -*-
import base64
import logging
import requests

from odoo import http, _
from odoo.http import request
from odoo.tools.urls import urljoin as url_join
from odoo.addons.social.controllers.main import SocialController
from odoo.addons.social.controllers.main import SocialValidationException

_logger = logging.getLogger(__name__)


class SocialTikTokController(SocialController):

    # ── OAuth callback ────────────────────────────────────────────────────────

    @http.route(['/social_tiktok/callback'], type='http', auth='user')
    def social_tiktok_account_callback(
            self, code=None, state=None, scopes=None,
            error=None, error_description=None, **kw):
        """
        TikTok redirects here after the user grants (or denies) permissions.

        Query-string parameters (success):  code, state, scopes
        Query-string parameters (failure):  error, error_description
        """
        if not request.env.user.has_group('social.group_social_manager'):
            return request.render(
                'social.social_http_error_view',
                {'error_message': _('Unauthorized. Please contact your administrator.')})

        if error:
            return request.render(
                'social.social_http_error_view',
                {'error_message': error_description or _('TikTok authorization was denied.')})

        if not code:
            return request.render(
                'social.social_http_error_view',
                {'error_message': _('TikTok did not provide a valid authorization code.')})

        # Validate CSRF state token
        stored_state = request.env['ir.config_parameter'].sudo().get_param(
            'social.tiktok_oauth_state')
        if not state or state != stored_state:
            return request.render(
                'social.social_http_error_view',
                {'error_message': _(
                    'Invalid state parameter. '
                    'This could be a CSRF attack – please try adding the account again.')})

        # Clear the one-time state token
        request.env['ir.config_parameter'].sudo().set_param('social.tiktok_oauth_state', '')

        media = request.env.ref('social_tiktok.social_media_tiktok')
        try:
            self._tiktok_create_accounts(code, media)
        except SocialValidationException as e:
            return request.render(
                'social.social_http_error_view',
                {'error_message': e.get_message(),
                 'documentation_data': e.get_documentation_data()})

        return request.redirect('/odoo/action-social.action_social_stream_post')

    # ── Comments (stub – TikTok standard API does not support comment mgmt) ──

    @http.route('/social_tiktok/get_comments', type='jsonrpc', auth='user')
    def social_tiktok_get_comments(self, stream_post_id, next_records_token=False,
                                   comments_count=20):
        self._get_social_stream_post(stream_post_id, 'tiktok')
        return {
            'comments': [],
            'nextRecordsToken': None,
            'info': _(
                'Comment management is not available through the TikTok standard API. '
                'Please visit TikTok directly to view and manage comments on this video.'
            ),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _tiktok_create_accounts(self, code, media):
        """
        Exchange the authorization code for tokens, fetch user info, then
        create or update the social.account record.
        """
        client_key = request.env['ir.config_parameter'].sudo().get_param('social.tiktok_client_key')
        client_secret = request.env['ir.config_parameter'].sudo().get_param(
            'social.tiktok_client_secret')
        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = url_join(base_url, 'social_tiktok/callback')
        code_verifier = request.env['ir.config_parameter'].sudo().get_param(
            'social.tiktok_oauth_code_verifier')
        request.env['ir.config_parameter'].sudo().set_param('social.tiktok_oauth_code_verifier', '')

        # ── Token exchange ────────────────────────────────────────────────────
        try:
            token_response = requests.post(
                request.env['social.media']._TIKTOK_TOKEN_ENDPOINT,
                data={
                    'client_key': client_key,
                    'client_secret': client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': redirect_uri,
                    'code_verifier': code_verifier or '',
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=15
            )
        except Exception as e:
            raise SocialValidationException(
                _('Could not reach TikTok servers. Please try again. (%s)', str(e)))

        if not token_response.ok:
            raise SocialValidationException(
                _('TikTok token exchange failed: %s', token_response.text))

        # TikTok's v2 token endpoint returns the tokens at top level; the old
        # v1 endpoint wrapped them in a 'data' key. Support both.
        token_json = token_response.json()
        token_data = token_json.get('data') or token_json
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        open_id = token_data.get('open_id')

        if not access_token or not open_id:
            raise SocialValidationException(
                _('TikTok did not return valid credentials. Please try again.'))

        # ── Fetch user profile ────────────────────────────────────────────────
        fields_param = (
            'open_id,union_id,avatar_url,display_name,'
            'follower_count,following_count,likes_count,video_count'
        )
        user_info = {}
        try:
            user_response = requests.get(
                url_join(request.env['social.media']._TIKTOK_API_ENDPOINT, 'user/info/'),
                params={'fields': fields_param},
                headers={'Authorization': 'Bearer %s' % access_token},
                timeout=10
            )
            if user_response.ok:
                user_info = user_response.json().get('data', {}).get('user', {})
        except Exception:
            pass  # non-fatal; proceed with minimal info

        display_name = user_info.get('display_name') or 'TikTok Account'
        avatar_url = user_info.get('avatar_url', '')
        follower_count = user_info.get('follower_count', 0)

        # ── Download avatar ───────────────────────────────────────────────────
        image_data = False
        if avatar_url:
            try:
                img_resp = requests.get(avatar_url, timeout=10)
                if img_resp.ok:
                    image_data = base64.b64encode(img_resp.content)
            except Exception:
                pass

        # ── Create or update social.account ──────────────────────────────────
        existing_account = request.env['social.account'].sudo().with_context(
            active_test=False
        ).search([
            ('media_id', '=', media.id),
            ('tiktok_account_id', '=', open_id),
        ], limit=1)

        account_vals = {
            'active': True,
            'tiktok_access_token': access_token,
            'tiktok_refresh_token': refresh_token,
            'is_media_disconnected': False,
        }

        if existing_account:
            error_message = existing_account._get_multi_company_error_message()
            if error_message:
                raise SocialValidationException(error_message)
            existing_account.write(account_vals)
        else:
            account_vals.update({
                'name': display_name,
                'media_id': media.id,
                'tiktok_account_id': open_id,
                'audience': follower_count,
                'has_trends': False,
            })
            if image_data:
                account_vals['image'] = image_data
            request.env['social.account'].create(account_vals)
