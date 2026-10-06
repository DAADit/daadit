# -*- coding: utf-8 -*-
"""Post-install / post-upgrade hook: patch Odoo's stock calendar email
templates so that when a calendar event has an attached Microsoft Teams
meeting, the email shows a friendly link label instead of the very long
``teams.microsoft.com/l/meetup-join/19%3a…`` URL.

We do a targeted ``str.replace`` on the ``t-out`` value of the videocall
link rather than rewriting the whole ``body_html`` — that way the patch is
idempotent, survives Odoo upgrades that change *other* parts of the
template, and is easy to inspect / revert.

Affected templates (XML IDs):
- calendar.calendar_template_meeting_invitation
- calendar.calendar_template_meeting_changedate
- calendar.calendar_template_meeting_reminder
- calendar.calendar_template_meeting_update
"""
import logging

_logger = logging.getLogger(__name__)


# Templates 17/18/19 are sent against ``calendar.attendee``, so the event
# lives at ``object.event_id``. Template 20 is sent against ``calendar.event``
# directly, so the same field lives at ``object``.
ATTENDEE_TEMPLATES = (
    "calendar.calendar_template_meeting_invitation",
    "calendar.calendar_template_meeting_changedate",
    "calendar.calendar_template_meeting_reminder",
)
EVENT_TEMPLATE = "calendar.calendar_template_meeting_update"

# Original snippet present in all four stock templates (with the appropriate
# object prefix substituted in).
def _old_snippet(prefix):
    return (
        f't-out="{prefix}.videocall_location or \'\'"'
    )

def _new_snippet(prefix):
    # If the event has a Microsoft Teams meeting attached, show a friendly
    # label; otherwise fall back to the original URL display.
    return (
        f't-out="\'Microsoft Teams meeting\' '
        f'if {prefix}.ms_teams_meeting_id '
        f'else ({prefix}.videocall_location or \'\')"'
    )


def _patch_calendar_mail_templates(env):
    """Run after install / upgrade — patch the four templates in place."""
    for xmlid in ATTENDEE_TEMPLATES:
        _patch_template(env, xmlid, prefix="object.event_id")
    _patch_template(env, EVENT_TEMPLATE, prefix="object")


def _patch_template(env, xmlid, *, prefix):
    tpl = env.ref(xmlid, raise_if_not_found=False)
    if not tpl:
        _logger.info("Teams email patch: template %s not found, skipping", xmlid)
        return
    body = tpl.body_html or ""
    old = _old_snippet(prefix)
    new = _new_snippet(prefix)
    if old not in body:
        if new in body:
            # Already patched. Nothing to do.
            _logger.info("Teams email patch: %s already patched", xmlid)
            return
        _logger.warning(
            "Teams email patch: didn't find expected snippet in %s — "
            "stock template may have changed. Skipping (manual patch needed).",
            xmlid,
        )
        return
    tpl.body_html = body.replace(old, new)
    _logger.info("Teams email patch: applied to %s", xmlid)
