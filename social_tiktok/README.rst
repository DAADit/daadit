Social TikTok
=============

Integrate TikTok into Odoo's **Social Marketing** app. Connect your TikTok creator
or business account, publish video posts on a schedule, and monitor video performance
(likes, comments, shares, views) directly from your Odoo feed.


Features
--------

- **Account linking** — connect one or more TikTok accounts via OAuth 2.0
- **Video publishing** — upload and post videos to TikTok from the Social Marketing composer
- **Privacy control** — choose Public, Friends, Followers, or Private per post
- **Feed / Stream** — pull your latest published videos into the Social Marketing feed view
- **Engagement stats** — display likes, comments, shares, and views on each stream card
- **Account statistics** — sync follower count and total likes to the Accounts dashboard
- **Settings UI** — store your TikTok app credentials securely in Odoo system parameters


Requirements
------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Requirement
     - Details
   * - Odoo version
     - 19.0
   * - Odoo modules
     - ``social`` (Social Marketing)
   * - TikTok account
     - Creator or Business account
   * - TikTok app
     - Registered at `developers.tiktok.com <https://developers.tiktok.com>`_
       with **Content Posting API** enabled


Installation
------------

1. Copy the ``social_tiktok`` folder into your custom addons path (e.g. ``Projects/UFS``).
2. Ensure the path is listed in ``addons_path`` in your Odoo configuration file.
3. Restart the Odoo server.
4. Go to **Apps**, search for **Social TikTok**, and click **Install**.


Configuration
-------------

1 — Register a TikTok App
~~~~~~~~~~~~~~~~~~~~~~~~~

1. Log in to `developers.tiktok.com <https://developers.tiktok.com>`_ and create a new app.
2. Under **Products**, add **Login Kit** and **Content Posting API**.
3. In **App settings → Redirect URI**, add:

   .. code-block:: text

      https://<your-odoo-domain>/social_tiktok/callback

4. Note your **Client Key** and **Client Secret**.

2 — Enter Credentials in Odoo
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Go to **Settings → Social Marketing** (scroll to the *Developer Accounts* section).
2. Enter your **TikTok Client Key** and **TikTok Client Secret**.
3. Save.

3 — Connect a TikTok Account
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Open **Social Marketing → Accounts**.
2. Click the **TikTok** media card → **Add Account**.
3. You will be redirected to TikTok to authorise the app.
4. After approval you are redirected back to Odoo and the account appears in the list.


Usage
-----

Publishing a post
~~~~~~~~~~~~~~~~~

1. Go to **Social Marketing → Posts → New**.
2. Select your TikTok account in *Accounts*.
3. If posting different content per platform, enable **Split per media** and open the **TikTok** tab.
4. Enter a **Caption / Title** (max 150 characters) and attach a **video file**.
5. Choose a **Privacy** level.
6. Schedule or publish immediately.

Viewing the feed
~~~~~~~~~~~~~~~~

Open **Social Marketing → Feed**. TikTok video cards appear in the kanban board showing
the video thumbnail, caption, and engagement stats. Clicking the comments icon opens the
video on TikTok.com (comment management requires the TikTok Research API, which is not
part of the standard Content API).


Known Limitations
-----------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Limitation
     - Reason
   * - Video-only posts
     - TikTok's Content Posting API does not support text-only or image posts
   * - No in-app comment management
     - Comment read/write requires the TikTok Research API (separate approval)
   * - No engagement trends
     - Trend calculation requires historical data not available in the standard API
   * - Access token expiry
     - Tokens expire after 24 hours; refresh tokens last 1 year.
       Re-authorise the account when prompted


License
-------

LGPL-3
