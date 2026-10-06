# -*- coding: utf-8 -*-
"""Render a square social visual in the live DAADit house style.

A written post without an image reads as a placeholder on LinkedIn and
cannot be published at all on Instagram, so the content agents needed a
way to produce one. The brand values come from the same Claude Design
project the agents already consult for tone and colour, which means a
change there lands in the next visual without anyone editing code here.

Everything is drawn with Pillow, which Odoo already depends on. No
headless browser, no wkhtmltoimage — neither is guaranteed on Odoo.sh,
and an image is the one deliverable that must not fail silently.
"""
import base64
import io
import logging
import os

from odoo import _, models

_logger = logging.getLogger(__name__)

SIZE = 1080
MARGIN = 96

# The live design system is the source of truth; these are its current
# values, kept as a fallback so a visual still comes out in the right
# colours when Claude Design is unreachable.
FALLBACK = {
    "ink": "#17253A",
    "ink_2": "#1F3055",
    "primary": "#0277F5",
    "cyan": "#6DE7F5",
    "muted": "#CFD5DE",
}

# Mulish is the brand face; the rest are ordered by how close they sit
# to it. DejaVu ships with practically every Linux image and is the
# guaranteed floor.
FONT_CANDIDATES = {
    "bold": [
        "Mulish-Bold.ttf", "Mulish-ExtraBold.ttf",
        "NunitoSans-Bold.ttf", "Inter-Bold.ttf",
        "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
    ],
    "regular": [
        "Mulish-Regular.ttf", "NunitoSans-Regular.ttf",
        "Inter-Regular.ttf",
        "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
    ],
}
FONT_DIRS = [
    "/usr/share/fonts", "/usr/local/share/fonts",
    "/usr/lib/python3/dist-packages/odoo/addons/web/static/fonts",
]


def _hex_to_rgb(value, fallback=(23, 37, 58)):
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _find_font(names):
    """First existing file for these font names, or None."""
    for directory in FONT_DIRS:
        if not os.path.isdir(directory):
            continue
        for root, _dirs, files in os.walk(directory):
            lookup = {f.lower(): f for f in files}
            for name in names:
                hit = lookup.get(name.lower())
                if hit:
                    return os.path.join(root, hit)
    return None


class AiAgent(models.Model):
    _inherit = "ai.agent"

    # ------------------------------------------------------------------
    # Brand values
    # ------------------------------------------------------------------
    def _daadit_brand_palette(self):
        """Colours for the visual: live where possible, fallback else."""
        palette = dict(FALLBACK)
        try:
            design = self._daadit_claude_design_fetch()
        except Exception:  # noqa: BLE001
            _logger.info("social visual: design fetch failed, using fallback")
            return palette
        if not isinstance(design, dict) or not design.get("ok"):
            return palette
        variables = ((design.get("tokens") or {}).get("css_variables") or [])
        mapping = {
            "--bg-hero": "ink",
            "--bg-hero-2": "ink_2",
            "--brand-primary": "primary",
            "--brand-accent-cyan": "cyan",
        }
        for entry in variables:
            if not isinstance(entry, str) or ":" not in entry:
                continue
            name, _sep, value = entry.partition(":")
            key = mapping.get(name.strip())
            if key:
                palette[key] = value.strip().strip("'\"")
        return palette

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _daadit_render_social_visual(self, headline, subline="", post_id=0):
        """Draw the visual, store it, and optionally attach it to a post."""
        self.ensure_one()
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return {"ok": False, "error": _(
                "Pillow is niet beschikbaar op deze server, dus ik kan geen "
                "afbeelding maken."
            )}

        headline = (headline or "").strip()
        subline = (subline or "").strip()
        if not headline:
            return {"ok": False, "error": _("Geef een kop op.")}

        palette = self._daadit_brand_palette()
        ink = _hex_to_rgb(palette.get("ink"), (23, 37, 58))
        ink_2 = _hex_to_rgb(palette.get("ink_2"), (31, 48, 85))
        primary = _hex_to_rgb(palette.get("primary"), (2, 119, 245))
        cyan = _hex_to_rgb(palette.get("cyan"), (109, 231, 245))
        muted = _hex_to_rgb(palette.get("muted"), (207, 213, 222))

        canvas = Image.new("RGB", (SIZE, SIZE), ink)
        draw = ImageDraw.Draw(canvas)

        # Vertical gradient between the two hero tones, drawn line by
        # line — cheap, and it matches the site's hero section.
        for y in range(SIZE):
            ratio = y / float(SIZE - 1)
            draw.line(
                [(0, y), (SIZE, y)],
                fill=tuple(
                    int(ink[i] + (ink_2[i] - ink[i]) * ratio) for i in range(3)
                ),
            )

        # Accent rule under the wordmark and a soft primary glow bottom
        # right, so the frame reads as ours at a glance.
        draw.rectangle(
            [MARGIN, MARGIN + 54, MARGIN + 96, MARGIN + 60], fill=cyan,
        )
        glow = Image.new("RGB", (SIZE, SIZE), ink)
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse(
            [SIZE - 340, SIZE - 340, SIZE + 160, SIZE + 160], fill=primary,
        )
        canvas = Image.blend(canvas, glow, 0.14)
        draw = ImageDraw.Draw(canvas)

        bold_path = _find_font(FONT_CANDIDATES["bold"])
        regular_path = _find_font(FONT_CANDIDATES["regular"])

        def font(path, size):
            if path:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:  # noqa: BLE001
                    pass
            return ImageFont.load_default()

        def wrap(text, font_obj, max_width):
            words, lines, current = text.split(), [], ""
            for word in words:
                probe = (current + " " + word).strip()
                if draw.textlength(probe, font=font_obj) <= max_width:
                    current = probe
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
            return lines

        usable = SIZE - 2 * MARGIN

        # Headline: step the size down until it fits in four lines, so a
        # long sentence stays inside the frame instead of overflowing.
        size = 92
        lines = []
        while size >= 46:
            head_font = font(bold_path, size)
            lines = wrap(headline, head_font, usable)
            if len(lines) <= 4:
                break
            size -= 8
        head_font = font(bold_path, size)
        line_height = int(size * 1.22)

        sub_font = font(regular_path, 40)
        sub_lines = wrap(subline, sub_font, usable)[:3] if subline else []
        sub_height = len(sub_lines) * 54 + (28 if sub_lines else 0)

        block = len(lines) * line_height + sub_height
        y = max(MARGIN + 150, (SIZE - block) // 2)

        for line in lines:
            draw.text((MARGIN, y), line, font=head_font, fill=(255, 255, 255))
            y += line_height
        if sub_lines:
            y += 28
            for line in sub_lines:
                draw.text((MARGIN, y), line, font=sub_font, fill=muted)
                y += 54

        # Wordmark top left, url bottom left.
        mark_font = font(bold_path, 40)
        draw.text((MARGIN, MARGIN - 6), "DAADit", font=mark_font,
                  fill=(255, 255, 255))
        url_font = font(regular_path, 32)
        draw.text((MARGIN, SIZE - MARGIN - 22), "daadit.group",
                  font=url_font, fill=muted)

        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=88, optimize=True)
        payload = base64.b64encode(buffer.getvalue())

        slug = "".join(
            c if c.isalnum() else "-" for c in headline.lower()
        ).strip("-")[:40] or "visual"
        attachment = self.env["ir.attachment"].sudo().create({
            "name": "daadit-%s.jpg" % slug,
            "datas": payload,
            "mimetype": "image/jpeg",
            "res_model": "social.post" if post_id else False,
            "res_id": post_id or 0,
        })

        linked = False
        if post_id:
            # Social Marketing is not a hard dependency: the visual is
            # useful on its own, and this module should stay installable
            # in a database without that app.
            # ``is not None``, niet ``if Post``: env.get geeft een LEGE
            # recordset terug en die is falsy, waardoor de koppeling
            # stilzwijgend oversloeg terwijl het model gewoon bestond.
            Post = self.env.get("social.post")
            post = (
                Post.sudo().browse(post_id).exists()
                if Post is not None else None
            )
            if post:
                post.write({"image_ids": [(4, attachment.id)]})
                linked = True
            else:
                _logger.info(
                    "daadit_claude_design: no social.post %s to attach "
                    "visual %s to", post_id, attachment.id,
                )

        _logger.info(
            "daadit_claude_design: social visual %s rendered by %s "
            "(post=%s, fonts=%s)",
            attachment.id, self.name, post_id or "-",
            os.path.basename(bold_path or "default"),
        )
        return {
            "ok": True,
            "attachment_id": attachment.id,
            "linked_to_post": linked,
            "post_id": post_id or 0,
            "size": "%sx%s" % (SIZE, SIZE),
            "colors_used": palette,
            "font": os.path.basename(bold_path) if bold_path
            else "systeemfont (merkfont niet op de server)",
            "note": _(
                "Visual gemaakt in de huisstijl uit Claude Design. "
                "Controleer hem in de post voordat je publiceert."
            ),
        }
