# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo import _


class SocialPostTemplate(models.Model):
    _inherit = 'social.post.template'

    tiktok_title = fields.Text(
        'TikTok Title',
        compute='_compute_message_by_media',
        store=True, readonly=False,
        help='Video caption / title shown on TikTok (max 150 characters).',
    )
    tiktok_video_ids = fields.Many2many(
        'ir.attachment',
        'template_tiktok_video_ids_rel',
        string='TikTok Videos',
        help='Video file(s) to upload and post on TikTok.',
        bypass_search_access=True,
    )
    tiktok_privacy_level = fields.Selection([
        ('PUBLIC_TO_EVERYONE', 'Public'),
        ('MUTUAL_FOLLOW_FRIENDS', 'Friends'),
        ('FOLLOWER_OF_CREATOR', 'Followers'),
        ('SELF_ONLY', 'Private'),
    ], string='TikTok Privacy', default='PUBLIC_TO_EVERYONE')

    has_tiktok_account = fields.Boolean(
        'Has TikTok Account', compute='_compute_has_tiktok_account')

    @api.constrains('tiktok_title', 'tiktok_video_ids')
    def _check_tiktok_content(self):
        for post in self:
            if post.has_tiktok_account and not post.tiktok_title and not post.tiktok_video_ids:
                raise UserError(_(
                    "Please specify a TikTok Title or attach a video for your TikTok post."
                ))

    @api.depends('account_ids.media_id.media_type')
    def _compute_has_tiktok_account(self):
        for post in self:
            post.has_tiktok_account = 'tiktok' in post.account_ids.media_id.mapped('media_type')

    @api.model
    def _message_fields(self):
        """Map TikTok media type to the tiktok_title field."""
        return {**super()._message_fields(), 'tiktok': 'tiktok_title'}
