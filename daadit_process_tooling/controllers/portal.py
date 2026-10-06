# -*- coding: utf-8 -*-
"""Portal + serve layer for per-customer process tooling.

Primary rendering is NATIVE (fasen -> stappen, validated in Odoo). An optional
external HTML tool (e.g. YoMoRo-generated) is supported as a fallback.

Access model (mirrors daadit_project_dashboard's portal-safe approach):
  * Each process belongs to a *company* (res.partner).
  * A portal user only reaches processes of their own company, resolved via
    ``commercial_partner_id``, and only when the company master switch
    ``process_tooling_active`` is on and the process is ``portal_visible``.
  * Internal users preview via dedicated internal-only routes.
"""
import base64
import binascii

import werkzeug.exceptions

from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


def _user_company():
    partner = request.env.user.partner_id
    if not partner:
        return None
    return (partner.commercial_partner_id or partner).sudo()


def _has_content(proc):
    return bool(proc.phase_ids or proc.tool_html)


def _accessible_processes():
    company = _user_company()
    Process = request.env["daadit.process"].sudo()
    if not company or not company.process_tooling_active:
        return company, Process.browse()
    procs = Process.search(
        [
            ("partner_id.commercial_partner_id", "=", company.id),
            ("portal_visible", "=", True),
        ],
        order="sequence, name, id",
    )
    procs = procs.filtered(_has_content)
    return company, procs


def _portal_process_or_404(process_id):
    company = _user_company()
    if not company or not company.process_tooling_active:
        raise werkzeug.exceptions.NotFound()
    proc = request.env["daadit.process"].sudo().browse(process_id).exists()
    if (
        not proc
        or not proc.portal_visible
        or not proc.partner_id
        or proc.partner_id.commercial_partner_id.id != company.id
        or not _has_content(proc)
    ):
        raise werkzeug.exceptions.NotFound()
    return proc


def _serve_tool(proc):
    data = proc.sudo().tool_html
    if not data:
        raise werkzeug.exceptions.NotFound()
    try:
        raw = base64.b64decode(data)
    except (binascii.Error, ValueError):
        raise werkzeug.exceptions.NotFound()
    return request.make_response(raw, headers=[
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(raw))),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Content-Security-Policy", "frame-ancestors 'self'"),
        ("Cache-Control", "private, max-age=60"),
    ])


class ProcessToolingPortal(CustomerPortal):

    def _prepare_portal_layout_values(self):
        values = super()._prepare_portal_layout_values()
        _company, procs = _accessible_processes()
        values["has_process_tooling"] = bool(procs)
        return values

    # ------------------------------------------------------------------ portal
    @http.route(["/my/processen"], type="http", auth="user", website=True)
    def portal_my_processes(self, **kw):
        _company, procs = _accessible_processes()
        return request.render(
            "daadit_process_tooling.portal_my_processes",
            {"page_name": "process_tooling", "processes": procs},
        )

    @http.route(["/my/processen/<int:process_id>"], type="http", auth="user", website=True)
    def portal_my_process(self, process_id, **kw):
        proc = _portal_process_or_404(process_id)
        return request.render(
            "daadit_process_tooling.portal_my_process",
            {"page_name": "process_tooling", "process": proc, "preview": False},
        )

    @http.route(
        ["/my/processen/<int:process_id>/step/<int:step_id>/validate"],
        type="http", auth="user", methods=["POST"], website=True,
    )
    def portal_validate_step(self, process_id, step_id, **post):
        proc = _portal_process_or_404(process_id)
        if not proc.allow_validation:
            raise werkzeug.exceptions.Forbidden()
        step = request.env["daadit.process.step"].sudo().browse(step_id).exists()
        if not step or step.process_id.id != proc.id:
            raise werkzeug.exceptions.NotFound()
        step.set_validation(post.get("state"), post.get("note"))
        return request.redirect("/my/processen/%s#step-%s" % (process_id, step_id))

    @http.route(["/my/processen/<int:process_id>/tool"], type="http", auth="user", sitemap=False)
    def portal_my_process_tool(self, process_id, **kw):
        proc = _portal_process_or_404(process_id)
        return _serve_tool(proc)

    # ---------------------------------------------------------------- internal
    @http.route(
        ["/process-tooling/process/<int:process_id>/preview"],
        type="http", auth="user", website=True, sitemap=False,
    )
    def internal_process_preview(self, process_id, **kw):
        """Internal-only preview of the native portal rendering."""
        if request.env.user.share:
            raise werkzeug.exceptions.Forbidden()
        proc = request.env["daadit.process"].sudo().browse(process_id).exists()
        if not proc:
            raise werkzeug.exceptions.NotFound()
        return request.render(
            "daadit_process_tooling.portal_my_process",
            {"page_name": "process_tooling", "process": proc, "preview": True},
        )

    @http.route(
        ["/process-tooling/process/<int:process_id>/tool"],
        type="http", auth="user", sitemap=False,
    )
    def internal_process_tool(self, process_id, **kw):
        """Internal-only preview of the external HTML tool."""
        if request.env.user.share:
            raise werkzeug.exceptions.Forbidden()
        proc = request.env["daadit.process"].sudo().browse(process_id).exists()
        if not proc or not proc.tool_html:
            raise werkzeug.exceptions.NotFound()
        return _serve_tool(proc)
