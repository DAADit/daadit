# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools.urls import urljoin as url_join


class SocialTikTokCredentialsWizard(models.TransientModel):
    _name = 'social.tiktok.credentials.wizard'
    _description = 'Connect a TikTok Developer App'

    client_key = fields.Char('Client Key', required=True)
    client_secret = fields.Char('Client Secret', required=True)
    redirect_uri = fields.Char(
        'Redirect URI', compute='_compute_redirect_uri',
        help="Register this exact URI as 'Redirect URI' in your "
             "TikTok developer app.")

    @api.depends_context('uid')
    def _compute_redirect_uri(self):
        base_url = self.env['social.media'].get_base_url()
        for wizard in self:
            wizard.redirect_uri = url_join(base_url, 'social_tiktok/callback')

    def action_connect(self):
        """ Save the credentials, then continue straight to TikTok's consent page. """
        self.ensure_one()
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('social.tiktok_client_key', (self.client_key or '').strip())
        icp.set_param('social.tiktok_client_secret', (self.client_secret or '').strip())
        media = self.env.ref('social_tiktok.social_media_tiktok')
        return media._tiktok_oauth_redirect((self.client_key or '').strip())
