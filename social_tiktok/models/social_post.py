# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.fields import Domain


class SocialPost(models.Model):
    _inherit = 'social.post'

    # Redefine with a unique relation table (same name as template field but different table)
    tiktok_video_ids = fields.Many2many(
        'ir.attachment',
        relation='tiktok_post_video_ids_rel',
        string='TikTok Videos',
        help='Video file(s) to post on TikTok.',
    )
    tiktok_privacy_level = fields.Selection([
        ('PUBLIC_TO_EVERYONE', 'Public'),
        ('MUTUAL_FOLLOW_FRIENDS', 'Friends'),
        ('FOLLOWER_OF_CREATOR', 'Followers'),
        ('SELF_ONLY', 'Private'),
    ], string='TikTok Privacy', default='PUBLIC_TO_EVERYONE')

    @api.depends('live_post_ids.tiktok_video_id')
    def _compute_stream_posts_count(self):
        super()._compute_stream_posts_count()

    def _get_stream_post_domain(self):
        domain = super()._get_stream_post_domain()
        tiktok_video_ids = [
            vid for vid in self.live_post_ids.mapped('tiktok_video_id') if vid
        ]
        if tiktok_video_ids:
            return Domain.OR([domain, [('tiktok_video_id', 'in', tiktok_video_ids)]])
        return domain
