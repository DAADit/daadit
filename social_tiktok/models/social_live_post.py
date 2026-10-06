# -*- coding: utf-8 -*-
import logging
import requests

from odoo import _, fields, models
from odoo.tools.urls import urljoin as url_join

_logger = logging.getLogger(__name__)


class SocialLivePost(models.Model):
    _inherit = 'social.live.post'

    tiktok_video_id = fields.Char('TikTok Video ID')
    tiktok_publish_id = fields.Char(
        'TikTok Publish ID',
        help="Returned by TikTok after video upload initialisation.")

    def _compute_live_post_link(self):
        tiktok_live_posts = self._filter_by_media_types(['tiktok']).filtered(
            lambda post: post.state == 'posted'
        )
        super(SocialLivePost, (self - tiktok_live_posts))._compute_live_post_link()

        for post in tiktok_live_posts:
            post.live_post_link = 'https://www.tiktok.com/@%s/video/%s' % (
                post.account_id.name, post.tiktok_video_id
            ) if post.tiktok_video_id else 'https://www.tiktok.com/'

    def _post(self):
        tiktok_live_posts = self._filter_by_media_types(['tiktok'])
        super(SocialLivePost, (self - tiktok_live_posts))._post()

        for live_post in tiktok_live_posts:
            live_post._post_tiktok()

    def _post_tiktok(self):
        """
        Publish a video to TikTok using the Content Posting API (FILE_UPLOAD).

        Flow:
          1. POST /v2/post/publish/video/init/  →  get upload_url + publish_id
          2. PUT <upload_url>                   →  upload the video binary
          3. TikTok processes the video asynchronously; publish_id is stored for reference.
        """
        self.ensure_one()
        account = self.account_id
        post = self.post_id

        # TikTok requires a video – abort early with a clear message if none attached
        video_ids = getattr(post, 'tiktok_video_ids', False)
        if not video_ids:
            self.write({
                'state': 'failed',
                'failure_reason': _(
                    'TikTok posts require a video file. '
                    'Please attach a video in the TikTok tab of your post.'
                ),
            })
            return

        video = video_ids[0]
        video_data = video.with_context(bin_size=False).raw
        video_size = len(video_data)

        # Build post metadata
        title = (self.message or '')[:150]  # TikTok title cap
        privacy_level = getattr(post, 'tiktok_privacy_level', None) or 'PUBLIC_TO_EVERYONE'

        # ── Step 1: initialise the upload ────────────────────────────────────
        init_payload = {
            'post_info': {
                'title': title,
                'privacy_level': privacy_level,
                'disable_duet': False,
                'disable_comment': False,
                'disable_stitch': False,
            },
            'source_info': {
                'source': 'FILE_UPLOAD',
                'video_size': video_size,
                'chunk_size': video_size,
                'total_chunk_count': 1,
            },
        }

        try:
            init_response = requests.post(
                url_join(self.env['social.media']._TIKTOK_API_ENDPOINT, 'post/publish/video/init/'),
                json=init_payload,
                headers={
                    'Authorization': 'Bearer %s' % account.tiktok_access_token,
                    'Content-Type': 'application/json; charset=UTF-8',
                },
                timeout=30
            )
        except Exception as e:
            self.write({'state': 'failed', 'failure_reason': str(e)})
            return

        if not init_response.ok:
            error_msg = ''
            try:
                error_msg = init_response.json().get('error', {}).get('message', '')
            except Exception:
                pass
            self.write({
                'state': 'failed',
                'failure_reason': _(
                    'TikTok publish initialisation failed: %s',
                    error_msg or init_response.text),
            })
            return

        init_data = init_response.json().get('data', {})
        publish_id = init_data.get('publish_id')
        upload_url = init_data.get('upload_url')

        if not upload_url or not publish_id:
            self.write({
                'state': 'failed',
                'failure_reason': _('TikTok did not return a valid upload URL.'),
            })
            return

        # ── Step 2: upload the video binary ──────────────────────────────────
        try:
            upload_response = requests.put(
                upload_url,
                data=video_data,
                headers={
                    'Content-Type': video.mimetype or 'video/mp4',
                    'Content-Length': str(video_size),
                    'Content-Range': 'bytes 0-%d/%d' % (video_size - 1, video_size),
                },
                timeout=180  # large videos can take time
            )
        except Exception as e:
            self.write({'state': 'failed', 'failure_reason': str(e)})
            return

        if upload_response.status_code not in (200, 201, 206):
            self.write({
                'state': 'failed',
                'failure_reason': _(
                    'TikTok video upload failed (HTTP %s).', upload_response.status_code
                ),
            })
            return

        # TikTok processes the video asynchronously – mark as posted and store publish_id
        self.write({
            'tiktok_publish_id': publish_id,
            'state': 'posted',
            'failure_reason': False,
        })

    def _refresh_statistics(self):
        super()._refresh_statistics()
        # Per-post engagement stats are refreshed through the stream fetch (_fetch_stream_data)
        # rather than a separate call here, as TikTok's standard API does not expose a
        # single-video metrics endpoint for non-Research API credentials.
