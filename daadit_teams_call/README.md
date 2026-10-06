# DAADit Teams Call

Standalone click-to-call functionality for Odoo using Microsoft Teams
deeplinks. Independent of `daadit_teams_meeting` and Odoo's `voip` app.

## What it does

Adds a small **Call via Teams** button next to phone fields on:

| Surface | Module | Field used |
|---|---|---|
| Contact form (`res.partner`) | `daadit_teams_call` (core) | `phone`, fallback `mobile` |
| Helpdesk ticket | `daadit_teams_call_helpdesk` (auto-install) | `partner_phone`, fallback `partner_id.phone`/`mobile` |
| CRM lead / opportunity | `daadit_teams_call_crm` (auto-install) | `phone`, `mobile`, fallback `partner_id.*` |

Clicking the button opens in a new browser tab:

```
https://teams.microsoft.com/l/call/0/0?users=4:<phone>
```

The Teams desktop client picks it up and:

- **With Teams Phone (PSTN) licence** — places the call immediately
- **Without Teams Phone** — opens Teams and asks how you'd like to call

Phone numbers are normalised before being put into the deeplink:
whitespace, parentheses and dashes are stripped, and a leading `00`
(common in NL phone formatting) is rewritten to `+` so Teams treats it
as E.164.

## Module layout

```
daadit_teams_call/                       core, partner form button
├── models/res_partner.py                action_call_via_teams + helpers
└── views/res_partner_views.xml          button next to <field name="phone">

daadit_teams_call_helpdesk/              auto-install bridge
├── models/helpdesk_ticket.py            action_call_via_teams on helpdesk.ticket
└── views/helpdesk_ticket_views.xml      button next to partner_phone

daadit_teams_call_crm/                   auto-install bridge
├── models/crm_lead.py                   action_call_via_teams on crm.lead
└── views/crm_lead_views.xml             button next to phone on lead form
```

The bridge modules only install if BOTH their dependencies are present,
so customers without `helpdesk` or `crm` see no breakage.

## Relationship to Odoo VoIP

Odoo's Enterprise `voip` app provides an in-browser softphone using SIP /
WebRTC against your PBX. `daadit_teams_call` is **complementary**, not a
replacement:

| Scenario | Recommended |
|---|---|
| Tenant has Teams Phone (PSTN), no PBX | Just use this module |
| Tenant has a PBX, no Teams Phone | Use `voip` (this module doesn't replace it) |
| Tenant has both | Install both — users see two buttons next to the phone field and pick |
| User is on mobile / not at their desk | Teams deeplink works on mobile (opens Teams mobile app); `voip` softphone does not |

There is intentionally **no** auto-switching between the two: the user
choice (PBX vs Teams) belongs to the moment of the call, not to a global
setting.

If you do want to default everyone to one or the other, that's a future
extension — likely a per-user preference plus a JS hook on Odoo's phone
widget. Not built in v19.0.1.x.

## Deployment

The core module + both bridges are auto-discovered on the next
`Apps → Update Apps List`. Bridges auto-install when their dependencies
are met.

No Azure AD configuration is needed for the deeplinks themselves — the
URL is handled entirely client-side by the Teams app. Setting up Teams
Phone (PSTN calling) is a Microsoft 365 licence + admin-portal step that
sits outside Odoo.

## Phone number formatting

The normaliser is intentionally minimal:

```
"(+31) 20 123-4567" → "+31201234567"
"0031 20 123 4567"  → "+31201234567"
"020 123 4567"      → "0201234567"   (Teams will treat as local)
```

For consistent international dialing, store partner phones in E.164
(`+31...`) on the partner record. The bridge modules will copy that
format into the Teams deeplink unchanged.

## Roadmap

- Per-user "Default to Teams for click-to-call" preference (when `voip`
  is also installed)
- JS override of Odoo's `phone` widget so existing tel: links route
  through Teams without needing the extra button
- Call-history view (would require Teams call records via Graph
  `/communications/callRecords` — application-only permission, admin
  consent required)
