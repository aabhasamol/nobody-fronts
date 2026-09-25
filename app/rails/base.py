"""The three rails, as interfaces.

The engine only ever talks to these. Swap `MockPayments` for `PineLabsPayments`
(and so on) in `app/rails/__init__.py` or via env, nothing else changes.

Vocabulary is deliberately the competition's: voice / payments / logistics.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date, datetime
from ..models import Member, Leg, Stay, CallRecord, Authorisation, Payout


class CaptureFailed(RuntimeError):
    """A presentation against a blocked mandate did not go through (bank timeout, revoked, insufficient funds)."""


class VoiceRail(ABC):
    """Gnani. Outbound calls that come back as fields, not prose.

    Voice has two jobs, both where no other channel works:
      1. supplier calls to stays that exist only on the phone (availability, group rate, missing facts, a hold;
         later, a late-arrival notice);
      2. an escalation call to a member when a live disruption needs their choice within minutes and the text
         went unanswered.
    Members are never called to chase a reply, and listed stays are never called to check the listing.
    """

    @abstractmethod
    def call_supplier(self, stay: Stay, party_size: int, check_in: date, nights: int, language: str) -> CallRecord:
        """Ask a phone-only stay: rooms for the party on these dates, rate per twin room per night, twin sharing,
        refund terms, and a 48-hour hold. Disposition CONFIRMED / UNAVAILABLE / NO_ANSWER."""

    @abstractmethod
    def notify_late_arrival(self, stay: Stay, member: Member, eta: datetime) -> CallRecord:
        """Tell a phone-only stay that one guest now arrives late, so the room is not given away."""

    @abstractmethod
    def escalate_member(self, member: Member, question: str, options: list[str]) -> CallRecord:
        """Call a member who has not answered a time-critical text. Returns the option they chose (1-based)."""


class PaymentsRail(ABC):
    """Pine Labs. Block-now, debit-at-booking, release-otherwise — and the pool.

    Quorum is the merchant of record for the trip. Members' mandates are created by Quorum and settle into
    Quorum's merchant account: that account is the pool. Quorum then pays each supplier from it (a B2B
    travel wallet for flights and listed hotels, UPI or a bank transfer for a homestay). The organiser is
    never in the money path. The pool can never go negative: Quorum does not front either.

    Today the collection side is N single-payer UPI one-time mandates. The ask to Pine Labs
    (see docs/rails.md) is a group order with a shared expiry and an atomic capture into escrow.
    """

    @abstractmethod
    def create_block(self, member: Member, amount: int, validity_days: int, reference: str) -> Authorisation:
        """Create a one-time mandate for `amount` INR. Returns PENDING until the member approves."""

    @abstractmethod
    def refresh(self, auth: Authorisation) -> Authorisation:
        """Poll the rail for the block's current status (PENDING → BLOCKED, or REVOKED / EXPIRED)."""

    @abstractmethod
    def emi_offers(self, amount: int) -> list[tuple[int, int]]:
        """(months, monthly INR) tenures a credit card could pay this amount in. Pine Labs: Offer Discovery."""

    @abstractmethod
    def capture(self, auth: Authorisation, amount: int) -> Authorisation:
        """Debit `amount` against a block: a UPI presentation, or a card capture. Cumulative debits never exceed
        auth.amount, and a single-capture instrument accepts one. Raises CaptureFailed if the bank declines."""

    @abstractmethod
    def refund(self, auth: Authorisation) -> Authorisation:
        """Return everything captured on this mandate. Used when a later capture in the same group failed."""

    @abstractmethod
    def release(self, auth: Authorisation) -> Authorisation:
        """Cancel the mandate / let the block lapse. Nothing is charged."""

    @abstractmethod
    def pay_supplier(self, supplier: str, amount: int, purpose: str, reference: str) -> Payout:
        """Pay a supplier from the pool. Must fail if the pool cannot cover it."""

    @abstractmethod
    def receive_refund(self, supplier: str, amount: int, reference: str) -> int:
        """A supplier (a carrier that cancelled) returns money to the pool. Returns the pool balance."""

    @abstractmethod
    def pool(self) -> int:
        """What is in Quorum's account for this checkout: captured − refunded − paid out + supplier refunds."""


class LogisticsRail(ABC):
    """Intercity legs and stays. Not parcels: Delhivery's role here is distances, not deliveries."""

    @abstractmethod
    def search_legs(self, origin: str, destination: str, on: date) -> list[Leg]:
        """Quote options for one traveller, one direction, cheapest first. Live: the price may differ by call."""

    @abstractmethod
    def search_stays(self, city: str, check_in: date, nights: int, must_haves: list[str]) -> list[Stay]:
        """Quote stays for the group: listed ones at the listing rate, phone-only ones at whatever is known."""

    @abstractmethod
    def book_leg(self, leg: Leg, member: Member) -> Leg:
        """Ticket one leg for one member. Returns the leg with a PNR."""

    @abstractmethod
    def book_stay(self, stay: Stay, members: list[Member]) -> Stay:
        """Confirm the stay for everyone who is in. For a phone-only stay this converts the hold."""

    @abstractmethod
    def cancel_leg(self, leg: Leg) -> Leg:
        """Simulate or perform a cancellation (disruption path)."""
