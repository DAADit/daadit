# -*- coding: utf-8 -*-
"""Zet niet-JPEG-afbeeldingen om vóór de Instagram-validatie draait.

Stock (``social_instagram``) weigert bewust elke conversie ("what the
user uploads … is as close as possible to what they will get") en
blokkeert daarmee elke PNG met een ValidationError. In de praktijk is
vrijwel elk aangeleverd beeld PNG, dus de gebruiker strandt telkens op
"The following images are not in the correct format (jpg/jpeg)".

De override zit op ``_get_instagram_image_error`` — het ene punt
waar élke aanroeper (handmatige post, geplande post, template-check)
doorheen komt — en converteert eerst, zodat stock's validatie daarna
gewoon slaagt. Mislukt een conversie (geen leesbaar beeld), dan laten
we het bestand met rust en geeft stock zijn normale foutmelding.
"""
import logging
import re

from odoo import models
from odoo.tools.image import image_process

_logger = logging.getLogger(__name__)

JPEG_QUALITY = 90


class SocialPostTemplate(models.Model):
    _inherit = "social.post.template"

    def _get_instagram_image_error(self):
        self._daadit_convert_images_to_jpeg()
        return super()._get_instagram_image_error()

    def _daadit_convert_images_to_jpeg(self):
        """Converteer niet-JPEG-bijlagen van deze post naar JPEG, in situ.

        In situ (dezelfde ``ir.attachment``) zodat elke verwijzing —
        ook ``image_ids`` van andere kanalen op dezelfde post — het
        geconverteerde bestand ziet; JPEG is voor alle platformen
        geldig. Fail-open per afbeelding: een bestand dat niet als
        afbeelding te lezen is, blijft ongemoeid en valt daarna op
        stock's eigen validatie.
        """
        for template in self:
            candidates = template.instagram_image_ids.filtered(
                lambda att: att.type == "binary"
                and att.mimetype != "image/jpeg"
            )
            for att in candidates:
                try:
                    # v19: image_process werkt op rauwe bytes, niet base64.
                    source = att.raw
                    if not source:
                        continue
                    converted = image_process(
                        source, output_format="JPEG", quality=JPEG_QUALITY,
                    )
                except Exception:  # noqa: BLE001
                    _logger.info(
                        "daadit_social_jpeg: %r (id %s) is not a "
                        "convertible image — leaving it to stock "
                        "validation", att.name, att.id,
                    )
                    continue
                new_name = re.sub(
                    r"\.(png|gif|bmp|webp|tiff?)$", ".jpg",
                    att.name or "afbeelding.jpg", flags=re.IGNORECASE,
                )
                if not new_name.lower().endswith((".jpg", ".jpeg")):
                    new_name += ".jpg"
                att.write({
                    "raw": converted,
                    "mimetype": "image/jpeg",
                    "name": new_name,
                })
                _logger.info(
                    "daadit_social_jpeg: converted attachment %s to JPEG "
                    "(%s)", att.id, new_name,
                )
