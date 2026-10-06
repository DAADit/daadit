# -*- coding: utf-8 -*-
import logging
import requests

from datetime import datetime

from odoo import models
from odoo.tools.urls import urljoin as url_join

_logger = logging.getLogger(__name__)

# Fields to request from TikTok's video list API
_TIKTOK_VIDEO_FIELDS = (
    'id,title,cover_image_url,like_count,comment_count,'
    'share_count,view_count,create_time,embed_link'
)


class SocialStream(models.Model):
    _inherit = 'social.stream'

    def _apply_default_name(self):
        tiktok_streams = self.filtered(lambda s: s.media_id.media_type == 'tiktok')
        super(SocialStream, (self - tiktok_streams))._apply_default_name()

        for stream in tiktok_streams:
            stream.write({'name': '%s: %s' % (stream.stream_type_id.name, stream.account_id.name)})

    def _fetch_stream_data(self):
        if self.media_id.media_type != 'tiktok':
            return super()._fetch_stream_data()

        if self.stream_type_id.stream_type == 'tiktok_user_videos':
            return self._fetch_tiktok_user_videos()

    def _fetch_tiktok_user_videos(self):
        self.ensure_one()
        account = self.account_id

        try:
            response = requests.post(
                url_join(self.env['social.media']._TIKTOK_API_ENDPOINT, 'video/list/'),
                params={'fields': _TIKTOK_VIDEO_FIELDS},
                json={'max_count': 20},
                headers={
                    'Authorization': 'Bearer %s' % account.tiktok_access_token,
                    'Content-Type': 'application/json',
                },
                timeout=15
            )
        except Exception as e:
            _logger.warning(
                "Social TikTok: Network error fetching videos for '%s': %s",
                account.name, e)
            return False

        if not response.ok:
            _logger.warning(
                "Social TikTok: Failed to fetch video list for '%s': %s",
                account.name, response.text)
            account._action_disconnect_accounts(response.json())
            return False

        videos = response.json().get('data', {}).get('videos', [])
        if not videos:
            return False

        tiktok_video_ids = [v.get('id') for v in videos if v.get('id')]
        existing_posts = self.env['social.stream.post'].search([
            ('stream_id', '=', self.id),
            ('tiktok_video_id', 'in', tiktok_video_ids),
        ])
        existing_by_video_id = {p.tiktok_video_id: p for p in existing_posts}

        posts_to_create = []
        for video in videos:
            video_id = video.get('id')
            if not video_id:
                continue

            cover_url = video.get('cover_image_url', '')
            create_time = video.get('create_time')
            published_date = None
            if create_time:
                try:
                    published_date = datetime.utcfromtimestamp(int(create_time))
                except (ValueError, TypeError):
                    pass

            values = {
                'stream_id': self.id,
                'message': video.get('title', ''),
                'author_name': account.name,
                'published_date': published_date,
                'tiktok_video_id': video_id,
                'tiktok_likes_count': video.get('like_count', 0),
                'tiktok_comments_count': video.get('comment_count', 0),
                'tiktok_shares_count': video.get('share_count', 0),
                'tiktok_views_count': video.get('view_count', 0),
            }

            # Store cover image as link_image_url for the kanban thumbnail
            if cover_url and 'tiktokcdn' in cover_url:
                values['link_image_url'] = cover_url

            existing_post = existing_by_video_id.get(video_id)
            if existing_post:
                existing_post.sudo().write(values)
            else:
                if values.get('message') or values.get('link_image_url'):
                    posts_to_create.append(values)

        stream_posts = self.env['social.stream.post'].sudo().create(posts_to_create)
        return any(
            sp.stream_id.create_uid.id == self.env.uid
            for sp in stream_posts
        )
