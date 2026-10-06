Social TikTok — Technical Reference
=====================================


Architecture Overview
---------------------

``social_tiktok`` follows the standard Odoo social media integration pattern:
extend the base ``social`` module models using ``_inherit``, implement platform-specific
API calls, and register a controller for the OAuth callback.

.. code-block:: text

   social_tiktok/
   ├── models/
   │   ├── social_media.py          # Adds media_type='tiktok'; builds OAuth URL
   │   ├── social_account.py        # Stores tokens; computes stats; creates default stream
   │   ├── social_live_post.py      # Video publish flow (init + upload)
   │   ├── social_post.py           # Adds tiktok_video_ids / tiktok_privacy_level to social.post
   │   ├── social_post_template.py  # Adds TikTok tab fields; maps message/images fields
   │   ├── social_stream.py         # Fetches user's video list from TikTok API
   │   ├── social_stream_post.py    # TikTok-specific stream post fields and computed links
   │   └── res_config_settings.py   # Exposes client_key / client_secret in Settings
   ├── controllers/
   │   └── main.py                  # OAuth callback; stub comment endpoint
   ├── data/
   │   └── social_media_data.xml    # social.media record + stream type seed data
   ├── views/
   │   ├── res_config_settings_views.xml   # Settings form injection
   │   ├── social_tiktok_templates.xml     # QWeb preview template (server-side)
   │   ├── social_post_template_views.xml  # TikTok tab in post composer
   │   └── social_stream_post_views.xml    # Kanban card stats injection
   └── static/src/
       ├── js/stream_post_kanban_record.js  # Patches kanban record (comments click)
       ├── scss/social_tiktok.scss          # Brand colours + stats bar styles
       └── xml/social_tiktok_templates.xml  # OWL/client-side template stubs


TikTok API Endpoints
--------------------

.. list-table::
   :header-rows: 1
   :widths: 30 10 60

   * - Purpose
     - Method
     - Endpoint
   * - OAuth authorization
     - GET redirect
     - ``https://www.tiktok.com/v2/auth/authorize/``
   * - Token exchange / refresh
     - POST
     - ``https://open.tiktokapis.com/v2/oauth/token/``
   * - User info
     - GET
     - ``https://open.tiktokapis.com/v2/user/info/``
   * - Video list (stream)
     - POST
     - ``https://open.tiktokapis.com/v2/video/list/``
   * - Publish init
     - POST
     - ``https://open.tiktokapis.com/v2/post/publish/video/init/``

All API calls to ``open.tiktokapis.com`` use ``Authorization: Bearer <access_token>``.


OAuth 2.0 Flow
--------------

TikTok uses the **authorization code** grant type (unlike Facebook which uses the
implicit/fragment flow).

.. code-block:: text

   User clicks "Add Account"
           │
           ▼
   SocialMedia._action_add_account()
     Reads client_key from ir.config_parameter
     Generates UUID state → stored in ir.config_parameter (CSRF guard)
     Redirects to TikTok with ?response_type=code&scope=...&state=<uuid>
           │
           ▼  (user grants permission on TikTok)
           │
   GET /social_tiktok/callback?code=<CODE>&state=<UUID>
           │
   SocialTikTokController.social_tiktok_account_callback()
     Validates state vs stored value
     Calls _tiktok_create_accounts(code, media)
       │
       ├─ POST /v2/oauth/token/ → access_token, refresh_token, open_id
       ├─ GET  /v2/user/info/   → display_name, avatar_url, follower_count
       └─ Creates / updates social.account record
           │
           ▼
   Redirects to /odoo/action-social.action_social_stream_post

**CSRF protection:** A UUID is stored in ``ir.config_parameter`` under key
``social.tiktok_oauth_state`` before the redirect. The callback validates the
returned ``state`` parameter against this value, then clears it.


Models
------

``social.media`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Addition
     - Type
     - Notes
   * - ``media_type``
     - Selection add
     - Adds ``('tiktok', 'TikTok')``
   * - ``_TIKTOK_OAUTH_ENDPOINT``
     - class attr
     - Auth URL
   * - ``_TIKTOK_TOKEN_ENDPOINT``
     - class attr
     - Token exchange URL
   * - ``_TIKTOK_API_ENDPOINT``
     - class attr
     - Base API URL

``social.account`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Field
     - Type
     - Notes
   * - ``tiktok_account_id``
     - Char
     - TikTok ``open_id``
   * - ``tiktok_access_token``
     - Char
     - Bearer token (24 h TTL)
   * - ``tiktok_refresh_token``
     - Char
     - Refresh token (1 year TTL)

Overrides:

- ``_compute_stats_link()`` — links to ``tiktok.com/@<name>``
- ``_compute_statistics()`` — reads ``follower_count`` → ``audience``, ``likes_count`` → ``engagement``
- ``create()`` — auto-creates a *User Videos* stream after account creation

Helper methods:

- ``_tiktok_fetch_user_info()`` — GET ``/v2/user/info/``
- ``_tiktok_refresh_access_token()`` — POST ``/v2/oauth/token/`` with ``grant_type=refresh_token``

``social.live.post`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Field
     - Type
     - Notes
   * - ``tiktok_video_id``
     - Char
     - Set after stream post match
   * - ``tiktok_publish_id``
     - Char
     - Returned by TikTok on publish init

``_post_tiktok()`` publish flow:

.. code-block:: text

   1. Validate video attachment present (tiktok_video_ids[0])
   2. POST /v2/post/publish/video/init/
      payload: { post_info: {title, privacy_level, ...},
                 source_info: {source:"FILE_UPLOAD", video_size, chunk_size:video_size, total_chunk_count:1} }
      response: { publish_id, upload_url }
   3. PUT <upload_url>
      headers: Content-Type, Content-Length, Content-Range: bytes 0-<N>/<N>
      body: raw video bytes
   4. Write state='posted', tiktok_publish_id=publish_id

TikTok processes the upload asynchronously — the video may not be publicly visible
immediately after the PUT completes.

``social.post`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Field
     - Type
     - Notes
   * - ``tiktok_video_ids``
     - Many2many ``ir.attachment``
     - Relation: ``tiktok_post_video_ids_rel``
   * - ``tiktok_privacy_level``
     - Selection
     - Default: ``PUBLIC_TO_EVERYONE``

``social.post.template`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Field
     - Type
     - Notes
   * - ``tiktok_title``
     - Text
     - Computed from global message via ``_message_fields()``
   * - ``tiktok_video_ids``
     - Many2many ``ir.attachment``
     - Relation: ``template_tiktok_video_ids_rel``
   * - ``tiktok_privacy_level``
     - Selection
     - Default: ``PUBLIC_TO_EVERYONE``
   * - ``has_tiktok_account``
     - Boolean (compute)
     - True when any selected account is TikTok

``_message_fields()`` returns ``{'tiktok': 'tiktok_title'}`` so the global message
populates the TikTok caption when *Split per media* is disabled.

``social.stream`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``_fetch_tiktok_user_videos()``:

.. code-block:: text

   POST /v2/video/list/?fields=id,title,cover_image_url,like_count,...
   body: { max_count: 20 }

   → upsert social.stream.post records with TikTok-specific fields

``social.stream.post`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Field
     - Type
     - Notes
   * - ``tiktok_video_id``
     - Char (indexed)
     - TikTok video ID
   * - ``tiktok_likes_count``
     - Integer
     -
   * - ``tiktok_comments_count``
     - Integer
     -
   * - ``tiktok_shares_count``
     - Integer
     -
   * - ``tiktok_views_count``
     - Integer
     -

Computed overrides: ``_compute_author_link``, ``_compute_post_link``,
``_compute_is_author``, ``_fetch_matching_post``.

``res.config.settings`` (extension)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Field
     - ``ir.config_parameter`` key
   * - ``tiktok_client_key``
     - ``social.tiktok_client_key``
   * - ``tiktok_client_secret``
     - ``social.tiktok_client_secret``

Both fields are hidden from non-social-manager users.


Controller Routes
-----------------

.. list-table::
   :header-rows: 1
   :widths: 35 10 10 45

   * - Route
     - Type
     - Auth
     - Description
   * - ``GET /social_tiktok/callback``
     - http
     - user
     - OAuth callback; exchanges code for tokens
   * - ``POST /social_tiktok/get_comments``
     - jsonrpc
     - user
     - Stub — returns empty list with info message


Frontend (OWL / JS)
--------------------

``stream_post_kanban_record.js`` patches ``StreamPostKanbanRecord``:

- Attaches a click listener to ``.o_social_tiktok_comments``
- On click: opens the video on ``tiktok.com`` in a new tab (no in-app comments modal)

No custom OWL components are required since TikTok comment management is not
available through the standard API.


Data Records
------------

Seeded via ``data/social_media_data.xml`` (``noupdate="1"``):

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Model
     - External ID
     - Description
   * - ``social.media``
     - ``social_tiktok.social_media_tiktok``
     - TikTok platform record
   * - ``social.stream.type``
     - ``social_tiktok.stream_type_user_videos``
     - "User Videos" stream type


Security & Token Handling
--------------------------

- Access tokens are stored in ``tiktok_access_token`` (plain text in the database,
  consistent with how Facebook/Instagram tokens are stored in the base modules).
- The CSRF state token is a one-time UUID stored in ``ir.config_parameter`` and
  cleared immediately after the callback is validated.
- All credential reads are wrapped in ``.sudo()`` and gated behind
  ``social.group_social_manager``.


Extending This Module
----------------------

To add a new stream type (e.g. liked videos):

1. Add a new ``social.stream.type`` record in ``data/social_media_data.xml``.
2. Add a branch in ``SocialStream._fetch_stream_data()``:

   .. code-block:: python

      elif self.stream_type_id.stream_type == 'tiktok_liked_videos':
          return self._fetch_tiktok_liked_videos()

3. Implement ``_fetch_tiktok_liked_videos()`` following the same upsert pattern
   as ``_fetch_tiktok_user_videos()``.
