# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    tiktok_client_key = fields.Char(
        'TikTok Client Key',
        compute='_compute_tiktok_client_key',
        inverse='_inverse_tiktok_client_key',
    )
    tiktok_client_secret = fields.Char(
        'TikTok Client Secret',
        compute='_compute_tiktok_client_secret',
        inverse='_inverse_tiktok_client_secret',
    )

    @api.depends('tiktok_client_key')
    def _compute_tiktok_client_key(self):
        for record in self:
            if self.env.user.has_group('social.group_social_manager'):
                record.tiktok_client_key = self.env['ir.config_parameter'].sudo().get_param(
                    'social.tiktok_client_key')
            else:
                record.tiktok_client_key = None

    def _inverse_tiktok_client_key(self):
        for record in self:
            if self.env.user.has_group('social.group_social_manager'):
                self.env['ir.config_parameter'].sudo().set_param(
                    'social.tiktok_client_key', record.tiktok_client_key)

    @api.depends('tiktok_client_secret')
    def _compute_tiktok_client_secret(self):
        for record in self:
            if self.env.user.has_group('social.group_social_manager'):
                record.tiktok_client_secret = self.env['ir.config_parameter'].sudo().get_param(
                    'social.tiktok_client_secret')
            else:
                record.tiktok_client_secret = None

    def _inverse_tiktok_client_secret(self):
        for record in self:
            if self.env.user.has_group('social.group_social_manager'):
                self.env['ir.config_parameter'].sudo().set_param(
                    'social.tiktok_client_secret', record.tiktok_client_secret)
