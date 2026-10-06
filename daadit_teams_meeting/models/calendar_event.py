# -*- coding: utf-8 -*-
"""Hook into ``calendar.event`` to maintain a Microsoft Teams online meeting.

Logic
-----
* If the organiser has Teams connected AND either
    - ``use_teams_meeting`` was set on the event, or
    - the organiser has ``ms_teams_use_by_default`` enabled,
  then we create a Teams meeting at create time and store
    - the ``joinWebUrl`` in ``videocall_location`` (Odoo's standard field used
      in invites and reminders),
    - the meeting ``id`` in ``ms_teams_meeting_id`` so we can patch / delete it
      later.
* On write that changes subject/start/stop, we PATCH the meeting once per
  unique meeting id (deduped across recurrence siblings).
* On unlink, we DELETE the meeting on Microsoft side only when no other
  calendar.event still references that meeting id (so deleting a single
  occurrence of a recurring series does NOT kill the meeting for the rest).

Recurring events
----------------
Odoo expands a recurring event into one ``calendar.event`` per occurrence,
all sharing ``recurrence_id``. We attach a single Microsoft Teams meeting
to the whole series: the first occurrence to be processed creates it on
Graph; subsequent occurrences detect the sibling and copy its meeting id
and join URL without making another Graph call. Patches and deletes are
deduped on ``ms_teams_meeting_id`` so we never hammer Graph N times for
the same conceptual meeting.
"""
import logging

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _as_utc(dt):
    """Return a timezone-aware UTC datetime."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return pytz.UTC.localize(dt)
    return dt.astimezone(pytz.UTC)


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    use_teams_meeting = fields.Boolean(
        string="Microsoft Teams Meeting",
        help="If checked, this event will have a Microsoft Teams meeting attached.",
    )
    ms_teams_meeting_id = fields.Char(
        string="Teams Meeting ID",
        readonly=True,
        copy=False,
    )
    ms_teams_join_url = fields.Char(
        string="Teams Join URL",
        readonly=True,
        copy=False,
    )
    # Display-only mirror of ``videocall_location`` so we can render the
    # Teams join URL with a friendly label ("Open Microsoft Teams-vergadering")
    # instead of the long raw URL, without disturbing how Odoo renders
    # non-Teams video links.
    ms_teams_pretty_link = fields.Char(
        string="Videolink",
        related="videocall_location",
        readonly=True,
    )
    # Stored flag that catches both:
    #   - events created through this module (ms_teams_meeting_id populated)
    #   - legacy events whose videocall_location is a teams.microsoft.com URL
    #     but was set before this module existed
    # Using a stored compute makes the view's invisible expression cheap
    # and reliable on the client side.
    is_teams_videocall = fields.Boolean(
        string="Is Microsoft Teams videocall",
        compute="_compute_is_teams_videocall",
        store=True,
        readonly=True,
    )

    @api.depends("ms_teams_meeting_id", "videocall_location")
    def _compute_is_teams_videocall(self):
        for ev in self:
            ev.is_teams_videocall = bool(
                ev.ms_teams_meeting_id
                or (ev.videocall_location and "teams.microsoft.com" in ev.videocall_location)
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _teams_organiser(self):
        """User whose Microsoft account hosts the meeting."""
        self.ensure_one()
        return self.user_id or self.env.user

    def _teams_recurring_sibling_with_meeting(self):
        """Find another occurrence of the same recurrence that already has
        a Teams meeting attached. Returns an empty recordset if none."""
        self.ensure_one()
        if not self.recurrence_id:
            return self.browse()
        return self.sudo().search([
            ("recurrence_id", "=", self.recurrence_id.id),
            ("ms_teams_meeting_id", "!=", False),
            ("id", "!=", self.id or 0),
        ], limit=1)

    def _teams_other_events_with_same_meeting(self):
        """Return other calendar.event records (any recurrence or none) that
        still reference our ``ms_teams_meeting_id``. Used to decide whether
        deleting THIS event should also delete the meeting on Microsoft."""
        self.ensure_one()
        if not self.ms_teams_meeting_id:
            return self.browse()
        return self.sudo().search([
            ("ms_teams_meeting_id", "=", self.ms_teams_meeting_id),
            ("id", "!=", self.id or 0),
        ])

    def _teams_should_create(self, vals=None):
        """Decide whether to create a Teams meeting for this event."""
        self.ensure_one()
        if self.ms_teams_meeting_id:
            return False
        organiser = self._teams_organiser().sudo()
        if not organiser.ms_teams_access_token:
            return False
        cfg = self.env["res.config.settings"].sudo().get_teams_config()
        if not cfg["enabled"]:
            return False
        if self.use_teams_meeting:
            return True
        # Default-on if the organiser has the preference and the event was created
        # without an explicit video provider already set.
        if organiser.ms_teams_use_by_default and not self.videocall_location:
            return True
        return False

    def _teams_create_meeting(self):
        self.ensure_one()
        # Recurring series: reuse the meeting from an earlier-processed
        # occurrence instead of creating a new one per occurrence.
        sibling = self._teams_recurring_sibling_with_meeting()
        if sibling:
            self.sudo().write({
                "use_teams_meeting": True,
                "ms_teams_meeting_id": sibling.ms_teams_meeting_id,
                "ms_teams_join_url": sibling.ms_teams_join_url,
                "videocall_location": sibling.videocall_location or self.videocall_location,
            })
            return
        organiser = self._teams_organiser()
        graph = self.env["daadit_teams.graph"].sudo()
        try:
            meeting = graph.create_online_meeting(
                organiser,
                subject=self.name or _("Meeting"),
                start=_as_utc(self.start),
                end=_as_utc(self.stop),
            )
        except UserError as exc:
            # Surface but don't block calendar event creation
            _logger.warning("Teams meeting not created for event %s: %s", self.id, exc)
            self.message_post(body=_(
                "Could not create Microsoft Teams meeting: %s"
            ) % exc.args[0] if exc.args else _("Unknown error."))
            return
        join_url = meeting.get("joinWebUrl") or meeting.get("joinUrl")
        self.sudo().write({
            "use_teams_meeting": True,
            "ms_teams_meeting_id": meeting.get("id"),
            "ms_teams_join_url": join_url,
            "videocall_location": join_url or self.videocall_location,
        })

    def _teams_patch_meeting(self, changed_vals):
        self.ensure_one()
        if not self.ms_teams_meeting_id:
            return
        organiser = self._teams_organiser()
        graph = self.env["daadit_teams.graph"].sudo()
        kwargs = {}
        if "name" in changed_vals:
            kwargs["subject"] = self.name
        if "start" in changed_vals:
            kwargs["start"] = _as_utc(self.start)
        if "stop" in changed_vals:
            kwargs["end"] = _as_utc(self.stop)
        if not kwargs:
            return
        try:
            graph.update_online_meeting(organiser, self.ms_teams_meeting_id, **kwargs)
        except Exception as exc:
            _logger.warning("Teams meeting patch failed for event %s: %s", self.id, exc)

    def _teams_delete_meeting(self):
        self.ensure_one()
        if not self.ms_teams_meeting_id:
            return
        # If any other event (typically another occurrence of the same
        # recurrence) still references this meeting id, the meeting must
        # stay alive on Microsoft side. Only the LAST event removing the
        # link is allowed to issue the Graph DELETE.
        if self._teams_other_events_with_same_meeting():
            _logger.info(
                "Teams meeting %s kept alive — other events still reference it",
                self.ms_teams_meeting_id,
            )
            return
        organiser = self._teams_organiser()
        graph = self.env["daadit_teams.graph"].sudo()
        try:
            graph.delete_online_meeting(organiser, self.ms_teams_meeting_id)
        except Exception as exc:
            _logger.warning("Teams meeting delete failed for event %s: %s", self.id, exc)

    # ------------------------------------------------------------------
    # User-facing action: "+ Teams vergadering" button
    # ------------------------------------------------------------------
    def action_set_teams_videocall_location(self):
        """Create a Teams meeting on demand and attach the join URL.

        Mirrors Odoo's native ``set_discuss_videocall_location`` button.
        If the organiser hasn't connected Microsoft yet, redirect them to
        the OAuth flow instead of failing silently.
        """
        self.ensure_one()
        organiser = self._teams_organiser().sudo()
        if not organiser.ms_teams_access_token:
            return {
                "type": "ir.actions.act_url",
                "url": f"/daadit_teams/oauth/authorize?user_id={organiser.id}",
                "target": "self",
            }
        if not self.id:
            raise UserError(_("Please save the event first before adding a Teams meeting."))
        self.sudo().write({"use_teams_meeting": True})
        if not self.ms_teams_meeting_id:
            self._teams_create_meeting()
        # Re-read after the sudo() write so the in-memory record reflects DB.
        self.invalidate_recordset(["videocall_location", "ms_teams_join_url",
                                    "ms_teams_meeting_id", "use_teams_meeting"])
        # Notify + reload the form so the join URL becomes visible immediately.
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Microsoft Teams meeting created"),
                "message": self.videocall_location or _("Meeting scheduled."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_open_teams_meeting(self):
        """Open the Teams join URL in a new browser tab."""
        self.ensure_one()
        if not self.videocall_location:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": self.videocall_location,
            "target": "new",
        }

    def action_remove_teams_meeting(self):
        """Detach the Teams meeting from the event and delete it on the
        Microsoft side (best-effort)."""
        self.ensure_one()
        if self.ms_teams_meeting_id:
            self._teams_delete_meeting()
        self.sudo().write({
            "ms_teams_meeting_id": False,
            "ms_teams_join_url": False,
            "use_teams_meeting": False,
            "videocall_location": False,
        })
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        events = super().create(vals_list)
        for ev in events:
            if ev._teams_should_create():
                ev._teams_create_meeting()
        return events

    def write(self, vals):
        track_fields = {"name", "start", "stop"}
        changed = track_fields & set(vals.keys())
        res = super().write(vals)
        if changed:
            # Dedupe: patch each unique Microsoft meeting at most once per
            # write call, even when the same write hits N occurrences of a
            # recurring series.
            seen_meeting_ids = set()
            for ev in self:
                mid = ev.ms_teams_meeting_id
                if mid and mid not in seen_meeting_ids:
                    ev._teams_patch_meeting(changed)
                    seen_meeting_ids.add(mid)
        # User toggled use_teams_meeting on, but no meeting yet
        if vals.get("use_teams_meeting"):
            for ev in self:
                if not ev.ms_teams_meeting_id and ev._teams_should_create():
                    ev._teams_create_meeting()
        return res

    def unlink(self):
        for ev in self:
            ev._teams_delete_meeting()
        return super().unlink()
