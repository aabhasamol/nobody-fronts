"""Mock rails. Deterministic, offline, good enough to demo every state.

Inventory is hand-written for the demo scenario (five friends → Goa) but the
shapes match what the real adapters return, so the engine cannot tell the
difference.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
import math
import random
from ..clock import clock
from ..models import Member, Leg, Stay, CallRecord, Authorisation, AuthStatus, new_id
from .base import VoiceRail, PaymentsRail, LogisticsRail, CaptureFailed


# --------------------------------------------------------------------------- voice
class MockVoice(VoiceRail):
    """Scripted suppliers so the demo shows every call outcome without a phone.

      * Dona Maria Homestay does not pick up the first time, then confirms — at a rate ₹400/night above the
        figure the organiser had (the call's price wins).
      * Fisherman's Rest has no rooms on those dates.
      * Anyone else confirms at the listed rate.
    Escalation calls always reach the member, who takes option 1.
    """

    def __init__(self):
        self.attempts: dict[str, int] = {}

    def call_supplier(self, stay: Stay, party_size: int, check_in: date, nights: int, language: str) -> CallRecord:
        n = self.attempts.get(stay.name, 0) + 1
        self.attempts[stay.name] = n
        rooms = math.ceil(party_size / 2)
        base = dict(to=stay.name, phone=stay.phone, language=language, purpose="AVAILABILITY",
                    call_ref=new_id("call"), called_at=clock.now())
        if "Dona Maria" in stay.name and n == 1:
            return CallRecord(disposition="NO_ANSWER", transcript="[ring ×6] no answer", **base)
        if "Fisherman" in stay.name:
            return CallRecord(disposition="UNAVAILABLE", available=False,
                              transcript=(f"[agent, {language}] Namaskar, {rooms} twin rooms, {check_in:%d %b}, {nights} raat?\n"
                                          f"[{stay.name}] Sorry, poora booked hai — ek shaadi ka block hai un dates pe."),
                              **base)
        rate = stay.rate_per_room_night + (400 if "Dona Maria" in stay.name else 0)
        hold_until = clock.now() + timedelta(hours=48)
        return CallRecord(
            disposition="CONFIRMED", available=True, rate_per_room_night=rate, rooms=rooms, twin_sharing=True,
            refund_terms="Full refund up to 72 hours before check-in",
            hold_until=hold_until,
            transcript=(f"[agent, {language}] Namaskar, Quorum se bol raha hoon, {party_size} logon ka group, "
                        f"{check_in:%d %b} se {nights} raat. {rooms} twin rooms milenge?\n"
                        f"[{stay.name}] Haan, {rooms} rooms hain. ₹{rate:,} per room per night, breakfast included.\n"
                        f"[agent] Twin sharing theek hai? Cancel karein toh refund?\n"
                        f"[{stay.name}] Twin theek hai. 72 ghante pehle tak full refund.\n"
                        f"[agent] 48 ghante hold kar sakte hain? Group vote ke baad confirm karenge.\n"
                        f"[{stay.name}] Theek hai, {hold_until:%A} tak hold.\n[agent] Dhanyavaad."),
            **base)

    def notify_late_arrival(self, stay: Stay, member: Member, eta: datetime) -> CallRecord:
        return CallRecord(to=stay.name, phone=stay.phone, language="hi-IN", purpose="LATE_ARRIVAL",
                          disposition="CONFIRMED",
                          transcript=(f"[agent] {member.first} ki flight cancel hui; ab {eta:%H:%M} pe pahunchenge. "
                                      f"Room hold rahega?\n[{stay.name}] Haan, koi dikkat nahi, chowkidar jaga rahega."),
                          call_ref=new_id("call"), called_at=clock.now())

    def escalate_member(self, member: Member, question: str, options: list[str]) -> CallRecord:
        lines = [f"[agent] {member.first}, Quorum here. {question}"]
        lines += [f"[agent] Option {i}: {o}" for i, o in enumerate(options, 1)]
        lines += [f"[{member.first}] Option 1, book it.", "[agent] Done — approve the UPI request I'm sending now."]
        return CallRecord(to=member.name, phone=member.phone, language="en-IN", purpose="ESCALATION",
                          disposition="CONFIRMED", choice=1, transcript="\n".join(lines),
                          call_ref=new_id("call"), called_at=clock.now())


# --------------------------------------------------------------------------- payments
class MockPayments(PaymentsRail):
    """Each `create_block` mints a fake OT mandate. `approve()` and `revoke()` are what the member's UPI app
    would do; the demo UI calls them on their behalf. Put a member id in `fail_capture_for` to make their
    presentation bounce, which is how the demo shows the partial-capture wall."""

    def __init__(self):
        self.ledger: dict[str, dict] = {}
        self.fail_capture_for: set[str] = set()

    def create_block(self, member: Member, amount: int, validity_days: int, reference: str) -> Authorisation:
        sub = new_id("otsub")
        order = new_id("ord")
        self.ledger[sub] = {"member": member.id, "amount": amount, "status": "CREATED",
                            "expires": clock.now() + timedelta(days=validity_days), "captured": 0}
        return Authorisation(member_id=member.id, plan_id=reference, amount=amount,
                             status=AuthStatus.PENDING, rail_ref=sub, order_ref=order, created_at=clock.now())

    def approve(self, auth: Authorisation) -> None:
        """Simulates the member tapping 'Approve' in their UPI app."""
        self.ledger[auth.rail_ref]["status"] = "ACTIVE"

    def revoke(self, auth: Authorisation) -> None:
        """Simulates the member revoking the mandate in their UPI app."""
        self.ledger[auth.rail_ref]["status"] = "REVOKED"

    def refresh(self, auth: Authorisation) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        if row["status"] == "ACTIVE" and auth.status == AuthStatus.PENDING:
            auth.status = AuthStatus.BLOCKED
        elif row["status"] == "REVOKED" and auth.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
            auth.status = AuthStatus.REVOKED
        elif clock.now() > row["expires"] and auth.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
            auth.status = AuthStatus.EXPIRED
            row["status"] = "EXPIRED"
        return auth

    def capture(self, auth: Authorisation, amount: int) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        assert row["status"] in ("ACTIVE", "SUCCESS"), f"presentation on a {row['status']} mandate"
        assert auth.captured_amount + amount <= auth.amount, "presentation above the authorised cap"
        if row["member"] in self.fail_capture_for:
            raise CaptureFailed("issuer declined: U30 debit failed at the remitter bank")
        row["status"] = "SUCCESS"
        row["captured"] += amount
        auth.status = AuthStatus.CAPTURED
        auth.captured_amount += amount
        return auth

    def refund(self, auth: Authorisation) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        row["status"], row["captured"] = "REFUNDED", 0
        auth.status, auth.captured_amount = AuthStatus.REFUNDED, 0
        return auth

    def release(self, auth: Authorisation) -> Authorisation:
        self.ledger[auth.rail_ref]["status"] = "CANCELLED"
        auth.status = AuthStatus.RELEASED
        return auth


# --------------------------------------------------------------------------- logistics
_FLIGHTS = {
    # origin -> list of (carrier, dep_hour, dur_h, price). Also used for the return leg into that city.
    "Kolkata":   [("IndiGo", 6, 2.7, 6400), ("Air India", 14, 2.8, 9900), ("IndiGo", 19, 2.7, 5900), ("SpiceJet", 21, 2.8, 6100)],
    "Bengaluru": [("IndiGo", 7, 1.2, 3800), ("Akasa", 12, 1.2, 3400), ("Air India", 18, 1.3, 4600)],
    "Delhi":     [("IndiGo", 5, 2.6, 6100), ("Vistara", 11, 2.7, 8200), ("Akasa", 20, 2.6, 5700)],
    "Mumbai":    [("Akasa", 8, 1.1, 2900), ("IndiGo", 13, 1.1, 3200), ("Air India", 19, 1.2, 3900)],
    "Hyderabad": [("IndiGo", 9, 1.3, 3700), ("Akasa", 15, 1.3, 3500)],
}
_STAYS = [
    # name, area, rate per twin room per night, phone_only, km to the venue (Assagao) / beach, what we know
    ("Fisherman's Rest, Morjim", "Morjim", 2400, True, 7.0, "family-run, six rooms, no online booking; number from a friend"),
    ("Dona Maria Homestay, Assagao", "Assagao", 2800, True, 1.5, "heritage house, four twin rooms; the bride's family has a block here"),
    ("Zostel Goa, Vagator", "Vagator", 1800, False, 3.0, "hostel: dorms and private twins"),
    ("Cabana by the Cove, Anjuna", "Anjuna", 3800, False, 4.0, "cliff-top cottages, sunset deck, pool"),
    ("Sea Breeze Beach Resort, Candolim", "Candolim", 4600, False, 9.0, "sea-facing rooms, pool, 200 m from the beach"),
]


class MockLogistics(LogisticsRail):
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.cancelled_pnrs: set[str] = set()
        self.drift: dict[str, float] = {}      # origin city -> multiplier on live fares (fares move after the vote)

    def search_legs(self, origin: str, destination: str, on: date) -> list[Leg]:
        table = _FLIGHTS.get(origin) or _FLIGHTS.get(destination) or _FLIGHTS["Kolkata"]
        mult = self.drift.get(origin, self.drift.get(destination, 1.0)) if origin in _FLIGHTS else self.drift.get(destination, 1.0)
        legs = []
        for carrier, dep_h, dur, price in table:
            dep = datetime.combine(on, time(dep_h, 0))
            legs.append(Leg(member_id="", mode="flight", carrier=carrier, origin=origin, destination=destination,
                            depart=dep, arrive=dep + timedelta(hours=dur), price=int(round(price * mult))))
        return sorted(legs, key=lambda l: l.price)

    def search_stays(self, city: str, check_in: date, nights: int, must_haves: list[str]) -> list[Stay]:
        stays = [Stay(name=n, city=city, area=area, phone=f"08326{self.rng.randint(10000, 99999)}",
                      rate_per_room_night=rate, nights=nights, phone_only=po, km_to_venue=km, listing_note=note)
                 for n, area, rate, po, km, note in _STAYS]
        if "no hostels" in [m.lower() for m in must_haves]:
            stays = [s for s in stays if "hostel" not in s.listing_note]
        return sorted(stays, key=lambda s: s.rate_per_room_night)

    def book_leg(self, leg: Leg, member: Member) -> Leg:
        leg.pnr = "".join(self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        leg.status = "BOOKED"
        return leg

    def book_stay(self, stay: Stay, members: list[Member]) -> Stay:
        stay.booking_ref = ("HOLD-" if stay.phone_only else "HTL") + str(self.rng.randint(100000, 999999))
        return stay

    def cancel_leg(self, leg: Leg) -> Leg:
        leg.status = "CANCELLED"
        if leg.pnr:
            self.cancelled_pnrs.add(leg.pnr)
        return leg
