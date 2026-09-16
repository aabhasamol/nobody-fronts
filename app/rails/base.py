"""The three rails, as interfaces.

The engine only ever talks to these. Swap `MockPayments` for `PineLabsPayments`
(and so on) in `app/rails/__init__.py` or via env, nothing else changes.

Vocabulary is deliberately the competition's: voice / payments / logistics.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from ..models import Member, Leg, Stay, VerificationRecord, Authorisation


class VoiceRail(ABC):
    """Gnani. Outbound calls that return a structured answer."""

    @abstractmethod
    def verify_property(self, stay: Stay, language: str, questions: list[str]) -> VerificationRecord:
        """Phone the property, ask the checklist, return disposition + answers + transcript."""

    @abstractmethod
    def ask_member_by_call(self, member: Member, questions: list[str]) -> dict[str, str]:
        """For members who won't fill forms (parents): capture constraints by call."""


class PaymentsRail(ABC):
    """Pine Labs. Block-now, debit-at-quorum, release-otherwise.

    Today this is orchestrated over N single-payer UPI one-time mandates.
    The ask to Pine Labs (see docs/rails.md) is to make `capture_all` atomic
    at the order level so a 4-of-5 partial capture cannot happen.
    """

    @abstractmethod
    def create_block(self, member: Member, amount: int, validity_days: int, reference: str) -> Authorisation:
        """Create a one-time mandate for `amount` INR. Returns PENDING until the member approves."""

    @abstractmethod
    def refresh(self, auth: Authorisation) -> Authorisation:
        """Poll the rail for the mandate's current status (PENDING → BLOCKED)."""

    @abstractmethod
    def capture(self, auth: Authorisation, amount: int) -> Authorisation:
        """Execute the debit against a BLOCKED mandate. amount ≤ auth.amount."""

    @abstractmethod
    def release(self, auth: Authorisation) -> Authorisation:
        """Cancel the mandate / let the block lapse. Nothing is charged."""


class LogisticsRail(ABC):
    """Intercity legs and stays. No parcels — Delhivery is not touched in this opening."""

    @abstractmethod
    def search_legs(self, origin: str, destination: str, on: date) -> list[Leg]:
        """Quote outbound (or return) options for one traveller, cheapest first."""

    @abstractmethod
    def search_stays(self, city: str, check_in: date, nights: int, must_haves: list[str]) -> list[Stay]:
        """Quote stays for the group, cheapest first."""

    @abstractmethod
    def book_leg(self, leg: Leg, member: Member) -> Leg:
        """Ticket one leg for one member. Returns the leg with a PNR."""

    @abstractmethod
    def book_stay(self, stay: Stay, members: list[Member]) -> Stay:
        """Confirm the stay for everyone who committed."""

    @abstractmethod
    def cancel_leg(self, leg: Leg) -> Leg:
        """Simulate or perform a cancellation (disruption path)."""
