Changelog
=========

All notable changes to ``social_tiktok`` will be documented here.

Format follows `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_.
Version numbers follow ``<odoo_major>.<odoo_minor>.<major>.<minor>.<patch>``.


[19.0.1.0.0] — 2026-03-24
--------------------------

Added
~~~~~

- Initial release of TikTok integration for Odoo 19 Social Marketing.
- **OAuth 2.0 account linking** using TikTok's authorization code flow.

  - CSRF state token generated per session and validated on callback.
  - Access token and refresh token stored securely per ``social.account``.

- **Video publishing** via TikTok Content Posting API (FILE_UPLOAD method).

  - 2-step flow: publish init → binary upload via PUT.
  - Supports privacy levels: Public, Friends, Followers, Private.
  - Title / caption field with 150-character enforcement.

- **Feed stream** — fetches up to 20 of the account's latest published videos
  via ``/v2/video/list/``, displayed in the Social Marketing kanban feed.
- **Engagement stats** on each kanban card: likes, comments, shares, views.
- **Account statistics** — syncs follower count (``audience``) and total likes
  (``engagement``) to the Accounts dashboard.
- **Settings UI** — TikTok Client Key and Client Secret fields injected into
  the Social Marketing developer settings block.
- **Auto stream creation** — a *User Videos* stream is created automatically
  when a TikTok account is first linked.
- **Token refresh helper** (``_tiktok_refresh_access_token``) for programmatic
  token renewal using the stored refresh token.
- Static assets: TikTok brand icon (SVG), SCSS styles, JS kanban patch.
- Seed data: ``social.media`` record and ``social.stream.type`` (User Videos).
