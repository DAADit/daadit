# DAADit Teams Meeting

Custom Odoo 19 module suite that brings Microsoft Teams into Odoo via
Microsoft Graph (delegated auth, per organiser).

Two modules ship in this folder:

| Module | Auto-install | Purpose |
|---|---|---|
| `daadit_teams_meeting` | manual | Core: calendar meetings, OAuth, settings, click-to-call |
| `daadit_teams_meeting_appointment` | yes, when `appointment` is installed | Bridge: adds Microsoft Teams to the Online Appointments videoconferencing dropdown |

## Features

### Calendar meetings
- `+ Teams vergadering` button next to `+ Odoo vergadering` on every calendar event
- Auto-create / patch / delete of Teams online meetings through Graph
  (`/me/onlineMeetings`) on event create / write / unlink
- Join URL stored in the standard `videocall_location` field — flows
  naturally into invitation emails, calendar reminders and the calendar
  widget
- Clean rendering on the form: "Open Microsoft Teams-vergadering" link +
  remove-X instead of the 600-character raw join URL
- Per-user preference *Use Microsoft Teams for new meetings* (auto-on
  default when the organiser has a connected account)

### Recurring events
- One meeting per `recurrence_id`, shared across all occurrences
- Renaming or rescheduling the series patches the meeting once on Graph
  (not N times)
- Deleting a single occurrence keeps the meeting alive for the rest of
  the series; deleting the last occurrence cleans up on Graph

### Online Appointments (bridge module)
- `appointment.type.event_videocall_source` extended with
  *Microsoft Teams*
- Bookings under a Teams-configured appointment type get a join URL
  automatically through the calendar create-hook
- Loads only when both `daadit_teams_meeting` and `appointment` are
  installed — never causes load failures on lean deployments

### Email templates
- Stock calendar mail templates (invitation, changedate, reminder,
  update) are patched in-place via post-install hook + migration script
  to show a friendly "Microsoft Teams meeting" link label instead of the
  raw URL
- Idempotent — safe to run on every upgrade

### Click-to-call
- "Call via Teams" button on the partner form (next to phone)
- Opens the Teams PSTN deeplink
  (`https://teams.microsoft.com/l/call/0/0?users=4:+...`) in a new tab
- Works with Teams Phone for actual calling, or opens the Teams app for
  manual handling without it
- Normalises phone numbers (strips whitespace/parens, converts leading
  `00` to `+` for E.164)

### Diagnostics & hardening
- *Test connection* button in Settings — verifies tenant credentials via
  the `client_credentials` grant, then a delegated `/me` call for the
  current user
- Microsoft Graph wrapper retries on 429 / 502 / 503 / 504 with
  exponential backoff + jitter, honouring `Retry-After` headers
- Background cron refreshes user tokens every 6 hours before they expire
- Module icon + Settings header use the official Microsoft Teams brand
  asset (provided by the org, see Branding below)

### Tests
- `tests/test_calendar_event.py` — create-attaches-meeting, legacy URL
  detection, recurrence delete-dedupe (with mocked Graph)
- `tests/test_microsoft_graph.py` — retry / give-up behaviour of the
  HTTP wrapper
- Tagged `post_install`; safe to run with `--test-enable`

## Azure AD App Registration

In the Microsoft Entra admin center (`https://entra.microsoft.com`):

1. **App registrations → New registration**
   - Name: `Odoo — DAADit Teams (staging)` (separate app per
     environment — see Production below)
   - Supported account types: *Accounts in this organizational
     directory only*
   - Redirect URI: **Web** → `https://<your-odoo-host>/daadit_teams/oauth/callback`
2. **Certificates & secrets → New client secret**, copy the value
   immediately
3. **API permissions → Add → Microsoft Graph → Delegated**:
   - `openid`
   - `profile`
   - `offline_access`
   - `User.Read`
   - `OnlineMeetings.ReadWrite`
4. Click **Grant admin consent for &lt;tenant&gt;** if you want users to
   skip the consent screen on first connect
5. In Odoo at **Settings → Microsoft Teams**, fill in:
   - Tenant ID, Client ID, Client secret value, Redirect URI
   - Tick *Enable Microsoft Teams*
6. Click **Test connection** in the same panel to validate end-to-end

## Connecting a user

Each user opens **Preferences → Microsoft Teams → Connect Microsoft
Account**, signs in with their work account, and consents to the scopes
once. Tokens are stored on `res.users` (visible to system admins only)
and refreshed automatically every 6 hours.

## Production deployment notes

When promoting the module to production:

- Create a **separate Azure AD App Registration** for production with
  its own redirect URI on the production host (`https://daadit.group/...`
  or wherever production runs). Don't share secrets between
  environments.
- Re-validate scopes and grant admin consent on the production tenant.
- All users must reconnect their Microsoft account against the new app —
  Odoo holds tokens scoped to the staging app and won't accept them on
  prod.
- Consider replacing the client secret with a **certificate** for
  better audit + no rotation gymnastics. See "Roadmap" below.

## Branding

`static/description/icon.png` (140 × 140) and `icon_settings.png`
(64 × 64) hold the Microsoft Teams logo. The asset is provided by the
organisation and must be obtained through legitimate channels:
- Microsoft Teams Developer Portal (`dev.teams.microsoft.com`) → App
  assets
- Microsoft Partner Network branding kit
- Microsoft Brand Central

To refresh, drop a new PNG (transparent background, square) at the same
path, run a quick resize for the 64 × 64 variant, and bump the module
version.

## Limits and known gaps

- **Delegated only**: meetings are created under the organiser's
  account. Events with no `user_id` (background jobs, cron-created
  records) won't get a meeting unless the organiser is a connected
  user.
- **Tokens stored in plain text** on `res.users` (same pattern as
  Odoo's own `microsoft_calendar`). Restrict the System Administrator
  group accordingly. Consider switching to encrypted storage if your
  threat model requires it.
- **No Outlook two-way sync** — events created in Outlook aren't
  imported into Odoo, and vice versa. See Roadmap.
- **Per-organiser meetings**: a meeting created by user A cannot be
  patched by user B even if B is also a connected user. The meeting's
  Microsoft Graph identity is the organiser.

## Roadmap

Items below are NOT in this module yet. Each is a meaningful chunk of
work in its own right.

### Telephony

- **Call logging → CRM**. Endpoint:
  `/communications/callRecords`. Requires `CallRecords.Read.All` —
  **application-only**, admin consent needed. Auto-link calls to
  lead / opportunity / partner via phone number match, post activity
  on the CRM timeline. Estimate: 3-5 days.
- **Teams presence on user avatars**. Endpoint:
  `/users/{id}/presence` or `/communications/getPresencesByUserId`.
  Requires `Presence.Read.All` — admin consent. Estimate: 1-2 days
  including the avatar widget + cache layer.

### Office 365 integration

- **Approval flows via Adaptive Cards**. PO, expense, leave-request
  approvals delivered to Teams chats with Approve / Reject buttons.
  Webhook-driven on the Teams side, mirroring back to Odoo. Estimate:
  5-7 days.
- **Meeting transcripts → opportunity timeline**. Pull
  `/me/onlineMeetings/{id}/transcripts` after the meeting ends,
  attach to the linked CRM opportunity, optionally run a summary
  prompt. Requires `OnlineMeetingTranscript.Read.All` — admin
  consent. Estimate: 3-4 days plus LLM cost.
- **Outlook calendar two-way sync**. Build on
  `Calendars.ReadWrite` (already an optional scope in this module).
  Watch Odoo's own `microsoft_calendar` Enterprise module for
  collision risk. Estimate: 4-6 days.
- **SharePoint attachment integration**. Mirror Odoo attachments to
  SharePoint document libraries so they're reachable from Teams chats
  and channels. Requires `Sites.ReadWrite.All` — admin consent.
  Estimate: 3-5 days.

### Productie / hardening

- **Certificate-based auth** instead of the rotating client secret.
  Generates a private key in Odoo, registers the public key on the
  app registration, signs the token request with JWT. No secret to
  rotate. Estimate: 1-2 days.
- **Separate Azure App per environment**, plus a settings field for
  "environment label" so users can tell which app they're connecting
  to. Estimate: half a day.

## File layout

```
daadit_teams_meeting/
├── __manifest__.py
├── hooks.py                         # post-install hook for email patches
├── controllers/main.py              # OAuth authorize + callback
├── models/
│   ├── microsoft_graph.py           # Graph wrapper, retry / refresh
│   ├── res_config_settings.py       # Settings + test_connection
│   ├── res_users.py                 # Per-user tokens, cron, connect button
│   ├── calendar_event.py            # Hook: create / write / unlink
│   └── res_partner.py               # Click-to-call action
├── views/                           # Forms, settings, partner button
├── data/ir_cron.xml                 # 6-hour token refresh
├── security/ir.model.access.csv
├── migrations/19.0.1.7.0/post-migrate.py   # email template patcher
├── static/
│   ├── description/icon*.png
│   └── src/scss/calendar_event.scss
└── tests/

daadit_teams_meeting_appointment/
├── __manifest__.py                  # auto_install: True
└── models/
    ├── appointment_type.py          # selection_add for Teams
    └── calendar_event.py            # bridge create-hook
```

## Version history

- `19.1.1.0.0` Click-to-call via Teams deeplinks
- `19.1.0.0.0` Test-connection diagnostic, Graph retry/backoff, tests
- `19.0.2.0.1` Migration signature fix
- `19.0.2.0.0` Recurring events fix + appointment bridge module
- `19.0.1.7.0` Email template patcher via migration
- `19.0.1.6.x` Replace-based videolink view + CSS belt-and-braces +
  button-based open
- `19.0.1.5.x` Pretty link display, remove-meeting button
- `19.0.1.4.0` Auto-refresh after meeting creation
- `19.0.1.3.x` Teams meeting button on form, module icon
- `19.0.1.2.x` OAuth callback fix, appointment-type revert
- `19.0.1.1.0` Teams meeting button on calendar event form
- `19.0.1.0.0` Initial module — manifest, OAuth, calendar hook
