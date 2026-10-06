# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)
_logger.info("daadit_ai_agent_schedule: __init__ loading (v19.0.2.1.0)")

from . import services
from . import models
from . import controllers

# Arm the chat-turn observer as soon as both modules are in memory.
# Import order between add-ons is not guaranteed, so the installer is
# tolerant: it returns False when the provider is not loaded yet and the
# first scheduled run re-tries it.
from .services import run_capture as _run_capture

_run_capture.install_turn_observer()
_run_capture.install_delegation_observer()

_logger.info("daadit_ai_agent_schedule: __init__ loaded")
