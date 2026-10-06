# -*- coding: utf-8 -*-
"""Render a Claude Design template to an image.

The design system carries finished templates — "LinkedIn Post —
Cinematic" (1200×1200, dark canvas, blue→purple gradient, DAADit logo
and Odoo Silver Partner badge) among them. Those are the layouts the
agents are supposed to publish in; drawing an approximation next to them
is exactly what we do not want.

The templates are HTML, so rendering needs a browser engine.
``wkhtmltoimage`` ships with the same package Odoo already uses for PDF
reports and is the first choice. When it is absent we say so plainly and
fall back to the built-in painter — a visual in the right colours beats
no visual, but the caller is told which one it got, because "looks like
our template" and "is our template" are not the same claim.
"""
import base64
import logging
import os
import re
import subprocess
import tempfile

from odoo import _, models

_logger = logging.getLogger(__name__)

RENDER_TIMEOUT = 90
DEFAULT_TEMPLATE = "LinkedIn Post"


def _find_renderer():
    """Path to wkhtmltoimage, or None."""
    from odoo.tools.misc import find_in_path
    for name in ("wkhtmltoimage", "wkhtmltoimage-amd64"):
        try:
            return find_in_path(name)
        except (IOError, OSError):
            continue
    return None


class AiAgent(models.Model):
    _inherit = "ai.agent"

    # ------------------------------------------------------------------
    # Template discovery
    # ------------------------------------------------------------------
    def _daadit_design_templates(self):
        """List the templates declared in the design system."""
        self.ensure_one()
        from .claude_design_client import ClaudeDesignClient
        return ClaudeDesignClient(self.env).list_templates()

    def _daadit_pick_template(self, wanted):
        """Find a template by (partial, case-insensitive) name."""
        listing = self._daadit_design_templates()
        if not listing.get("ok"):
            return None, listing
        wanted_low = (wanted or DEFAULT_TEMPLATE).lower()
        entries = listing.get("templates") or []
        for entry in entries:
            if wanted_low in (entry.get("name") or "").lower():
                return entry, None
        names = ", ".join(e.get("name") or "?" for e in entries)
        return None, {
            "ok": False,
            "error": _(
                "Geen template gevonden voor %(wanted)s. Beschikbaar: "
                "%(names)s",
                wanted=wanted, names=names or "(geen)",
            ),
        }

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _daadit_fill_template(self, html, css, headline, subline):
        """Inline the stylesheets and swap in the supplied copy.

        Templates carry example copy rather than placeholders, so the
        text is replaced by element: the first heading becomes the
        headline, the paragraph that follows it the subline. Anything we
        cannot identify is left exactly as designed.
        """
        for name, text in (css or {}).items():
            # Both <link href="styles.css"> and @import forms appear.
            html = re.sub(
                r'<link[^>]+href=["\'][^"\']*%s["\'][^>]*>' % re.escape(name),
                "<style>%s</style>" % text,
                html,
                flags=re.IGNORECASE,
            )
        if css and "<style>" not in html.lower():
            joined = "\n".join(css.values())
            html = html.replace(
                "</head>", "<style>%s</style></head>" % joined, 1,
            )

        def swap_first(pattern, replacement, source):
            if not replacement:
                return source, False
            new, count = re.subn(
                pattern,
                lambda m: m.group(1) + replacement + m.group(3),
                source,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
            return new, bool(count)

        html, did_head = swap_first(
            r"(<h1[^>]*>)(.*?)(</h1>)", headline, html,
        )
        if not did_head:
            html, did_head = swap_first(
                r"(<h2[^>]*>)(.*?)(</h2>)", headline, html,
            )
        html, _did_sub = swap_first(r"(<p[^>]*>)(.*?)(</p>)", subline, html)
        return html, did_head

    def _daadit_render_html_image(self, html, width, height):
        """HTML → JPEG bytes via wkhtmltoimage, or None when absent."""
        renderer = _find_renderer()
        if not renderer:
            return None, "no_renderer"
        tmp_dir = tempfile.mkdtemp(prefix="daadit-visual-")
        html_path = os.path.join(tmp_dir, "in.html")
        out_path = os.path.join(tmp_dir, "out.jpg")
        try:
            with open(html_path, "w") as handle:
                handle.write(html)
            command = [
                renderer, "--quiet", "--enable-local-file-access",
                "--format", "jpg", "--quality", "90",
                "--width", str(width), "--height", str(height),
                # Templates animate their entrance; without a pause the
                # capture lands on the first, still-transparent frame.
                "--javascript-delay", "1200",
                html_path, out_path,
            ]
            subprocess.run(
                command, timeout=RENDER_TIMEOUT, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            with open(out_path, "rb") as handle:
                return handle.read(), None
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except Exception as exc:  # noqa: BLE001
            _logger.warning("daadit_claude_design: render failed: %s", exc)
            return None, "render_failed"
        finally:
            for path in (html_path, out_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Tool entry point
    # ------------------------------------------------------------------
    def _daadit_render_template_visual(self, headline, subline="",
                                       post_id=0, template=""):
        """Render a design-system template and attach it to a post."""
        self.ensure_one()
        headline = (headline or "").strip()
        subline = (subline or "").strip()
        if not headline:
            return {"ok": False, "error": _("Geef een kop op.")}

        entry, problem = self._daadit_pick_template(template or DEFAULT_TEMPLATE)
        if problem:
            return problem

        from .claude_design_client import ClaudeDesignClient
        fetched = ClaudeDesignClient(self.env).fetch_template(
            entry.get("entry_path"),
        )
        if not fetched.get("ok"):
            return fetched

        # The declared size lives in the description ("1200×1200").
        size = re.search(
            r"(\d{3,4})\s*[x×]\s*(\d{3,4})", entry.get("description") or "",
        )
        width = int(size.group(1)) if size else 1200
        height = int(size.group(2)) if size else 1200

        html, replaced = self._daadit_fill_template(
            fetched["html"], fetched.get("css"), headline, subline,
        )
        image, failure = self._daadit_render_html_image(html, width, height)

        if image is None:
            # Be explicit about what the caller is getting instead.
            fallback = self._daadit_render_social_visual(
                headline=headline, subline=subline, post_id=post_id,
            )
            if isinstance(fallback, dict) and fallback.get("ok"):
                fallback["template_used"] = False
                fallback["template_name"] = entry.get("name")
                fallback["warning"] = _(
                    "De template '%(name)s' kon niet worden gerenderd "
                    "(%(reason)s), dus dit is de eenvoudige variant in de "
                    "huisstijlkleuren — NIET de template zelf. Meld dat "
                    "erbij.",
                    name=entry.get("name"),
                    reason={
                        "no_renderer": _("wkhtmltoimage ontbreekt op de server"),
                        "timeout": _("renderen duurde te lang"),
                    }.get(failure, _("renderfout")),
                )
            return fallback

        attachment = self.env["ir.attachment"].sudo().create({
            "name": "daadit-linkedin-%s.jpg" % (
                re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:40]
                or "post"
            ),
            "datas": base64.b64encode(image),
            "mimetype": "image/jpeg",
            "res_model": "social.post" if post_id else False,
            "res_id": post_id or 0,
        })

        linked = False
        if post_id:
            Post = self.env.get("social.post")
            post = (
                Post.sudo().browse(post_id).exists()
                if Post is not None else None
            )
            if post:
                post.write({"image_ids": [(4, attachment.id)]})
                linked = True

        _logger.info(
            "daadit_claude_design: rendered template %r (%sx%s) as "
            "attachment %s for %s",
            entry.get("name"), width, height, attachment.id, self.name,
        )
        return {
            "ok": True,
            "template_used": True,
            "template_name": entry.get("name"),
            "attachment_id": attachment.id,
            "linked_to_post": linked,
            "post_id": post_id or 0,
            "size": "%sx%s" % (width, height),
            "headline_replaced": replaced,
            "note": _(
                "Visual gerenderd uit de Claude Design-template "
                "'%(name)s'. Controleer hem in de post voordat je "
                "publiceert.", name=entry.get("name"),
            ),
        }
