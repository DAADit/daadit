# -*- coding: utf-8 -*-
from odoo import fields, models


class SocialStreamPost(models.Model):
    _inherit = 'social.stream.post'

    tiktok_video_id = fields.Char('TikTok Video ID', index=True)
    tiktok_likes_count = fields.Integer('TikTok Likes')
    tiktok_comments_count = fields.Integer('TikTok Comments')
    tiktok_shares_count = fields.Integer('TikTok Shares')
    tiktok_views_count = fields.Integer('TikTok Views')

    def _compute_author_link(self):
        tiktok_posts = self._filter_by_media_types(['tiktok'])
        super(SocialStreamPost, (self - tiktok_posts))._compute_author_link()

        for post in tiktok_posts:
            post.author_link = (
                'https://www.tiktok.com/@%s' % post.author_name
                if post.author_name else False
            )

    def _compute_post_link(self):
        tiktok_posts = self._filter_by_media_types(['tiktok'])
        super(SocialStreamPost, (self - tiktok_posts))._compute_post_link()

        for post in tiktok_posts:
            post.post_link = (
                'https://www.tiktok.com/@%s/video/%s' % (post.author_name, post.tiktok_video_id)
                if post.tiktok_video_id else False
            )

    def _compute_is_author(self):
        tiktok_posts = self._filter_by_media_types(['tiktok'])
        super(SocialStreamPost, (self - tiktok_posts))._compute_is_author()

        # All videos in the TikTok user stream belong to the linked account owner
        for post in tiktok_posts:
            post.is_author = bool(post.account_id.tiktok_account_id)

    def _fetch_matching_post(self):
        self.ensure_one()

        if self.account_id.media_type == 'tiktok' and self.tiktok_video_id:
            return self.env['social.live.post'].search(
                [('tiktok_video_id', '=', self.tiktok_video_id)], limit=1
            ).post_id
        return super()._fetch_matching_post()
