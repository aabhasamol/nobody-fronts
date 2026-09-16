"""Mock rails. Deterministic, offline, good enough to demo every state.

Inventory is hand-written for the demo scenario (five friends → Goa) but the
shapes match what the real adapters return, so the engine cannot tell the
difference.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
import random
from ..clock import clock
from ..models import Member, Leg, Stay, VerificationRecord, Authorisation, AuthStatus, new_id
from .base import VoiceRail, PaymentsRail, LogisticsRail


# --------------------------------------------------------------------------- voice
class MockVoice(VoiceRail):
    """Phone verification with two scripted properties: one passes, one fails.

    Any property whose name contains 'Palm Grove' fails verification (the room
    is not as pictured). Everything else verifies. That lets the demo show the
    'swap property' loop without a real call.
    """

    def verify_property(self, stay: Stay, language: str, questions: list[str]) -> VerificationRecord:
        fails = "Palm Grove" in stay.name
        answers = {
            "room_as_pictured": "No — the sea-view rooms are under renovation; only garden-facing available." if fails else "Yes, all rooms as listed, sea-facing block operational.",
            "road_motorable": "Yes" if not fails else "Last 800 m is unpaved, cabs refuse after rain.",
            "refund_terms": "Full refund up to 72 hours before check-in." if not fails else "No refunds.",
            "twin_sharing_available": "Yes",
        }
        transcript = (
            f"[agent, {language}] Namaskar, calling from Quorum on behalf of a group of five arriving {stay.city}. "
            f"Is the room in your listing — {stay.photos_claim} — what guests actually get?\n"
            f"[{stay.name}] {answers['room_as_pictured']}\n"
            f"[agent] Is the approach road motorable for a taxi?\n[{stay.name}] {answers['road_motorable']}\n"
            f"[agent] What are your refund terms if the group cancels?\n[{stay.name}] {answers['refund_terms']}\n"
            f"[agent] Dhanyavaad."
        )
        return VerificationRecord(
            property_name=stay.name, phone=stay.phone, language=language,
            disposition="MISMATCH" if fails else "VERIFIED",
            answers=answers, transcript=transcript,
            call_ref=new_id("call"), called_at=clock.now(),
        )

    def ask_member_by_call(self, member: Member, questions: list[str]) -> dict[str, str]:
        return {q: "(captured by call — mock)" for q in questions}


# --------------------------------------------------------------------------- payments
class MockPayments(PaymentsRail):
    """Each `create_block` mints a fake OT mandate. `approve()` is what the
    member's UPI app would do; the demo UI calls it on their behalf."""

    def __init__(self):
        self.ledger: dict[str, dict] = {}

    def create_block(self, member: Member, amount: int, validity_days: int, reference: str) -> Authorisation:
        sub = new_id("otsub")
        order = new_id("ord")
        self.ledger[sub] = {"member": member.id, "amount": amount, "status": "CREATED",
                            "expires": clock.now() + timedelta(days=validity_days), "captured": 0}
        return Authorisation(member_id=member.id, bundle_id=reference, amount=amount,
                             status=AuthStatus.PENDING, rail_ref=sub, order_ref=order, created_at=clock.now())

    def approve(self, auth: Authorisation) -> None:
        """Simulates the member tapping 'Approve' in their UPI app."""
        self.ledger[auth.rail_ref]["status"] = "ACTIVE"

    def refresh(self, auth: Authorisation) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        if row["status"] == "ACTIVE" and auth.status == AuthStatus.PENDING:
            auth.status = AuthStatus.BLOCKED
        if clock.now() > row["expires"] and auth.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
            auth.status = AuthStatus.EXPIRED
            row["status"] = "EXPIRED"
        return auth

    def capture(self, auth: Authorisation, amount: int) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        assert row["status"] == "ACTIVE", "presentation before activation"
        assert amount <= auth.amount, "presentation above ceiling"
        row["status"], row["captured"] = "SUCCESS", amount
        auth.status, auth.captured_amount = AuthStatus.CAPTURED, amount
        return auth

    def release(self, auth: Authorisation) -> Authorisation:
        self.ledger[auth.rail_ref]["status"] = "CANCELLED"
        auth.status = AuthStatus.RELEASED
        return auth


# --------------------------------------------------------------------------- logistics
_FLIGHTS = {
    # origin -> list of (carrier, dep_hour, dur_h, price)
    "Kolkata":   [("IndiGo", 6, 2.7, 6400), ("Air India", 14, 2.8, 7900), ("IndiGo", 19, 2.7, 5900)],
    "Bengaluru": [("IndiGo", 7, 1.2, 3800), ("Akasa", 12, 1.2, 3400), ("Air India", 18, 1.3, 4600)],
    "Delhi":     [("IndiGo", 5, 2.6, 6100), ("Vistara", 11, 2.7, 8200), ("Akasa", 20, 2.6, 5700)],
    "Mumbai":    [("Akasa", 8, 1.1, 2900), ("IndiGo", 13, 1.1, 3200), ("Air India", 19, 1.2, 3900)],
    "Hyderabad": [("IndiGo", 9, 1.3, 3700), ("Akasa", 15, 1.3, 3500)],
}
_STAYS = [
    # name, price/night/head twin-share, claim
    ("Sea Breeze Beach Resort, Candolim", 2100, "sea-facing rooms, pool, 200 m from beach"),
    ("Palm Grove Villas, Morjim", 1700, "private sea-view villas with balcony"),
    ("Cabana by the Cove, Anjuna", 1900, "cliff-top cottages, sunset deck"),
    ("Zostel Goa, Vagator", 900, "dorms and private rooms, hostel"),
]


class MockLogistics(LogisticsRail):
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.cancelled_pnrs: set[str] = set()

    def search_legs(self, origin: str, destination: str, on: date) -> list[Leg]:
        opts = _FLIGHTS.get(origin) or _FLIGHTS.get(destination) or _FLIGHTS["Kolkata"]   # return legs use the home-city table
        legs = []
        for carrier, dep_h, dur, price in opts:
            dep = datetime.combine(on, time(dep_h, 0))
            legs.append(Leg(member_id="", mode="flight", carrier=carrier, origin=origin, destination=destination,
                            depart=dep, arrive=dep + timedelta(hours=dur), price=price))
        return sorted(legs, key=lambda l: l.price)

    def search_stays(self, city: str, check_in: date, nights: int, must_haves: list[str]) -> list[Stay]:
        stays = [Stay(name=n, city=city, phone=f"08326{self.rng.randint(10000, 99999)}",
                      price_per_night=p, nights=nights, photos_claim=c) for n, p, c in _STAYS]
        if "no hostels" in [m.lower() for m in must_haves]:
            stays = [s for s in stays if "Zostel" not in s.name]
        return sorted(stays, key=lambda s: s.price_per_night)

    def book_leg(self, leg: Leg, member: Member) -> Leg:
        leg.pnr = "".join(self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        leg.status = "BOOKED"
        return leg

    def book_stay(self, stay: Stay, members: list[Member]) -> Stay:
        stay.booking_ref = "HTL" + str(self.rng.randint(100000, 999999))
        return stay

    def cancel_leg(self, leg: Leg) -> Leg:
        leg.status = "CANCELLED"
        if leg.pnr:
            self.cancelled_pnrs.add(leg.pnr)
        return leg
