# -*- coding: utf-8 -*-
"""
project.task — single-holder guard.

The dashboard stores its configuration in a special task whose name starts
with `__dashboard_cfg__`. Multiple views in the dashboard JS call
`search_read(..., limit=1)` to find this holder and `create(...)` if not
found. A subtle race / bad search-order results in views creating duplicate
holder tasks per project — over time, dozens accumulate. The active holder
that the UI shows can then differ from where the data was last written,
making team members appear to vanish.

This guard intercepts `project.task.create` and, for any create call whose
name contains `__dashboard_cfg__`, returns the EXISTING holder for that
project instead of creating a new one. The caller is none the wiser — it
gets back the id it expected, and any subsequent .write on it lands on the
real holder.
"""
import base64
import json
import re
import logging

from odoo import models, api
from odoo.http import request

_logger = logging.getLogger(__name__)


def _decode_cfg(description):
    """Decode a holder description (<p>base64(json)</p>) into a dict, or None."""
    if not description:
        return None
    b = re.sub(r'<[^>]*>', '', description)
    b = re.sub(r'&[a-zA-Z#0-9]+;', '', b)
    b = re.sub(r'[^A-Za-z0-9+/=]', '', b)
    if not b:
        return None
    try:
        return json.loads(base64.b64decode(b).decode('utf-8'))
    except Exception:
        return None


def _encode_cfg(cfg):
    raw = json.dumps(cfg, ensure_ascii=False)
    return '<p>' + base64.b64encode(raw.encode('utf-8')).decode('ascii') + '</p>'


def _deep_merge(old, new):
    """Recursively merge ``new`` into ``old``. For dicts: keys present only in
    ``old`` are preserved, overlapping keys recurse, keys only in ``new`` are
    added. For anything else (lists, scalars): ``new`` wins. This makes every
    holder write additive — a view can update its own slice but can never wipe
    another view's slice by saving a partial config."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = dict(old)
        for k, v in new.items():
            out[k] = _deep_merge(out[k], v) if k in out else v
        return out
    return new


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def write(self, vals):
        # Defend the dashboard config holder against accidental data loss.
        # Every dashboard view does a read-modify-write of the SAME base64 blob
        # in the holder's `description`. If any view momentarily fails to decode
        # the blob and then saves only its own slice, it would wipe everything
        # else (team, capacity, budget, ...). Here we merge any incoming holder
        # config with what's already stored, so writes are additive.
        if not vals.get('description') or not self:
            return super().write(vals)
        holders = self.filtered(lambda t: '__dashboard_cfg__' in (t.name or ''))
        if not holders:
            return super().write(vals)
        new_cfg = _decode_cfg(vals.get('description'))
        if not isinstance(new_cfg, dict):
            # Not a recognizable config payload — leave it to the default write.
            return super().write(vals)
        others = self - holders
        res = True
        if others:
            res = super(ProjectTask, others).write(vals)
        for h in holders:
            old_cfg = _decode_cfg(h.description or '')
            if isinstance(old_cfg, dict) and old_cfg:
                merged = _deep_merge(old_cfg, new_cfg)
                # P0-8: a portal (share) user may not change access/structure
                # keys via the holder blob (prevents Team-tab self-escalation).
                # Use the REAL request user — self.env.user is SUPERUSER under
                # the portal sudo-write path. Falls through (no protection) when
                # there is no active request (e.g. cron) or the user is internal.
                _portal = False
                try:
                    _portal = bool(request.env.user.share)
                except Exception:
                    _portal = False
                if _portal:
                    for _pk in ('team', 'roles', 'roleDocs'):
                        if _pk in old_cfg:
                            merged[_pk] = old_cfg[_pk]
                        else:
                            merged.pop(_pk, None)
                if merged != new_cfg:
                    _logger.info(
                        "dashboard holder merge-guard: preserved keys on task #%s "
                        "(incoming had %d top-level keys, merged has %d)",
                        h.id, len(new_cfg), len(merged),
                    )
                v2 = dict(vals)
                v2['description'] = _encode_cfg(merged)
                super(ProjectTask, h).write(v2)
            else:
                super(ProjectTask, h).write(vals)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        # Inspect each vals dict for the "__dashboard_cfg__" naming pattern.
        # If we find an existing holder for the same project, redirect: return
        # the existing record's id INSTEAD of creating a new one. For mixed
        # batches (some holders, some normal tasks), normal tasks still go
        # through standard create.
        if not vals_list:
            return super().create(vals_list)

        records = self.browse()
        normal_vals = []
        normal_positions = []
        for i, vals in enumerate(vals_list):
            name = (vals.get('name') or '')
            project_id = vals.get('project_id')
            if project_id and '__dashboard_cfg__' in name:
                existing = self.with_context(active_test=False).search([
                    ('project_id', '=', project_id),
                    ('name', 'ilike', '__dashboard_cfg__'),
                ], order='id asc')
                if existing:
                    # Pick the one with the longest description (= most data)
                    target = max(existing, key=lambda r: len(r.description or ''))
                    # If create wanted to set a description, merge it into the
                    # target. Otherwise leave target's description as-is.
                    if vals.get('description'):
                        target.with_context(mail_notrack=True).write({
                            'description': vals['description'],
                            'active': vals.get('active', target.active),
                        })
                    _logger.info(
                        "dashboard holder de-dup: redirecting create on project %s "
                        "to existing holder #%s (was about to be #N+1)",
                        project_id, target.id,
                    )
                    records |= target
                    continue
            normal_vals.append(vals)
            normal_positions.append(i)

        if normal_vals:
            created = super().create(normal_vals)
            records |= created

        return records
