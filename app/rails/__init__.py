"""Pick rails from the environment. Everything defaults to mock so the demo runs offline.

    QUORUM_VOICE=mock|gnani      QUORUM_PAYMENTS=mock|pinelabs      (logistics is mock-only for now)
"""
from __future__ import annotations
import os
from .base import VoiceRail, PaymentsRail, LogisticsRail
from .mock import MockVoice, MockPayments, MockLogistics


def build_rails() -> tuple[VoiceRail, PaymentsRail, LogisticsRail]:
    voice: VoiceRail = MockVoice()
    payments: PaymentsRail = MockPayments()
    logistics: LogisticsRail = MockLogistics()
    if os.environ.get("QUORUM_VOICE") == "gnani":
        from .gnani import GnaniVoice
        voice = GnaniVoice()
    if os.environ.get("QUORUM_PAYMENTS") == "pinelabs":
        from .pinelabs import PineLabsPayments
        payments = PineLabsPayments()
    return voice, payments, logistics
