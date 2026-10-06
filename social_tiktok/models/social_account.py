# -*- coding: utf-8 -*-
import logging
import requests

from odoo import api, fields, models
from odoo.tools.urls import urljoin as url_join

_logger = logging.getLogger(__name__)


class SocialAccount(models.Model):
    _inherit = 'social.account'

    tiktok_account_id = fields.Char(
        'TikTok Open ID', readonly=True,
        help="TikTok user open_id provided by the TikTok API. Do not set manually.")
    tiktok_access_token = fields.Char(
        'TikTok Access Token', readonly=True,
        help="TikTok access token used to authenticate API requests.")
    tiktok_refresh_token = fields.Char(
        'TikTok Refresh Token', readonly=True,
        help="TikTok refresh token used to renew expired access tokens.")

    def _compute_stats_link(self):
        tiktok_accounts = self._filter_by_media_types(['tiktok'])
        super(SocialAccount, (self - tiktok_accounts))._compute_stats_link()

        for account in tiktok_accounts:
            account.stats_link = (
                'https://www.tiktok.com/@%s' % account.name
                if account.name else False
            )

    def _compute_statistics(self):
        tiktok_accounts = self._filter_by_media_types(['tiktok'])
        super(SocialAccount, (self - tiktok_accounts))._compute_statistics()

        for account in tiktok_accounts.filtered('tiktok_access_token'):
            user_info = account._tiktok_fetch_user_info()
            if user_info:
                account.write({
                    'audience': user_info.get('follower_count', 0),
                    'engagement': user_info.get('likes_count', 0),
                })

    def _tiktok_fetch_user_info(self):
        """Fetch basic user info from TikTok API."""
        self.ensure_one()
        fields_param = (
            'open_id,union_id,avatar_url,display_name,'
            'follower_count,following_count,likes_count,video_count'
        )
        try:
            response = requests.get(
                url_join(self.env['social.media']._TIKTOK_API_ENDPOINT, 'user/info/'),
                params={'fields': fields_param},
                headers={'Authorization': 'Bearer %s' % self.tiktok_access_token},
                timeout=10
            )
            if response.ok:
                return response.json().get('data', {}).get('user', {})
            _logger.warning(
                "Social TikTok: Failed to fetch user info for '%s': %s",
                self.name, response.text)
        except Exception as e:
            _logger.warning("Social TikTok: Error fetching user info for '%s': %s", self.name, e)
        return {}

    def _tiktok_refresh_access_token(self):
        """Attempt to renew the access token using the stored refresh token."""
        self.ensure_one()
        client_key = self.env['ir.config_parameter'].sudo().get_param('social.tiktok_client_key')
        client_secret = self.env['ir.config_parameter'].sudo().get_param(
            'social.tiktok_client_secret')

        try:
            response = requests.post(
                self.env['social.media']._TIKTOK_TOKEN_ENDPOINT,
                data={
                    'client_key': client_key,
                    'client_secret': client_secret,
                    'grant_type': 'refresh_token',
                    'refresh_token': self.tiktok_refresh_token,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=15
            )
            if response.ok:
                data = response.json().get('data', {})
                self.write({
                    'tiktok_access_token': data.get('access_token'),
                    'tiktok_refresh_token': data.get('refresh_token', self.tiktok_refresh_token),
                })
                return True
            _logger.warning(
                "Social TikTok: Token refresh failed for '%s': %s",
                self.name, response.text)
        except Exception as e:
            _logger.warning("Social TikTok: Error refreshing token for '%s': %s", self.name, e)
        return False

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        res.filtered(lambda account: account.media_type == 'tiktok')._create_default_stream_tiktok()
        return res

    def _create_default_stream_tiktok(self):
        """Create a default 'User Videos' stream when a TikTok account is first added."""
        if not self:
            return

        user_videos_stream_type = self.env.ref('social_tiktok.stream_type_user_videos')
        streams_to_create = [{
            'media_id': account.media_id.id,
            'stream_type_id': user_videos_stream_type.id,
            'account_id': account.id,
        } for account in self]
        self.env['social.stream'].create(streams_to_create)
