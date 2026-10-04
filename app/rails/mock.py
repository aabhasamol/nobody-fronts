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
from ..models import Member, Leg, Stay, CallRecord, Authorisation, AuthStatus, Payout, Activity, INSTRUMENTS, new_id, inr
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
        hold_until = clock.now() + timedelta(hours=96)            # at least 96 h: outlasts vote, flip-in and block
        return CallRecord(
            disposition="CONFIRMED", available=True, rate_per_room_night=rate, rooms=rooms, twin_sharing=True,
            refund_terms="Full refund up to 72 hours before check-in",
            hold_until=hold_until,
            transcript=(f"[agent, {language}] Namaskar, Quorum se bol raha hoon, {party_size} logon ka group, "
                        f"{check_in:%d %b} se {nights} raat. {rooms} twin rooms milenge?\n"
                        f"[{stay.name}] Haan, {rooms} rooms hain. ₹{inr(rate)} per room per night, breakfast included.\n"
                        f"[agent] Twin sharing theek hai? Cancel karein toh refund?\n"
                        f"[{stay.name}] Twin theek hai. 72 ghante pehle tak full refund.\n"
                        f"[agent] 96 ghante hold kar sakte hain? Group vote ke baad confirm karenge.\n"
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
    """Each `create_block` mints a pending block; `approve(auth, via, emi_months)` is what the member does in
    their UPI app or on the card page, and fixes the instrument: UPI Reserve Pay (multi-debit), a UPI one-time
    mandate (one debit) or a credit-card pre-authorisation (one capture, 7-day window, optional EMI). `revoke()`
    is the member cancelling in-app. Put a member id in `fail_capture_for` to make their debit bounce, which is
    how the demo shows the partial-capture wall."""

    def __init__(self):
        self.ledger: dict[str, dict] = {}
        self.fail_capture_for: set[str] = set()
        self._pool = 0                                        # Quorum's merchant account, in INR
        self.payouts: list[Payout] = []

    def create_block(self, member: Member, amount: int, validity_days: int, reference: str) -> Authorisation:
        sub = new_id("blk")
        order = new_id("ord")
        self.ledger[sub] = {"member": member.id, "amount": amount, "status": "CREATED",
                            "expires": clock.now() + timedelta(days=validity_days), "captured": 0}
        return Authorisation(member_id=member.id, plan_id=reference, amount=amount,
                             status=AuthStatus.PENDING, rail_ref=sub, order_ref=order, created_at=clock.now())

    def approve(self, auth: Authorisation, via: str = "UPI_RESERVE", emi_months: int | None = None) -> None:
        """Simulates the member approving: in their UPI app, or on the card page (3-D Secure), with EMI if chosen."""
        assert via in INSTRUMENTS, f"unknown instrument {via}"
        assert emi_months is None or via == "CARD_PREAUTH", "EMI is a card thing"
        limit = INSTRUMENTS[via]["max_amount"]
        assert limit is None or auth.amount <= limit, f"a {INSTRUMENTS[via]['label']} blocks at most ₹{inr(limit)}"
        row = self.ledger[auth.rail_ref]
        row["status"], row["instrument"] = "ACTIVE", via
        auth.instrument, auth.emi_months = via, emi_months
        if INSTRUMENTS[via]["prepaid"]:                        # the link took the money now: it sits in the pool
            row["status"], row["prepaid_at"] = "PAID", clock.now()
            self._pool += auth.amount
        cap = INSTRUMENTS[via]["max_validity_days"]
        if cap is not None:                                    # a card hold lives 5–7 days whatever we asked for
            row["expires"] = min(row["expires"], clock.now() + timedelta(days=cap))
        auth.expires_at = row["expires"]

    def emi_offers(self, amount: int) -> list[tuple[int, int]]:
        """Mock of Offer Discovery: three tenures, issuer interest folded in roughly (13–15 % p.a.)."""
        return [(3, math.ceil(amount * 1.02 / 3 / 10) * 10), (6, math.ceil(amount * 1.04 / 6 / 10) * 10),
                (9, math.ceil(amount * 1.06 / 9 / 10) * 10)]

    def refresh(self, auth: Authorisation) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        if row["status"] in ("ACTIVE", "PAID") and auth.status == AuthStatus.PENDING:
            auth.status = AuthStatus.BLOCKED                   # PAID: a prepaid link, the money is already in the pool
        elif clock.now() > row["expires"] and auth.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
            auth.status = AuthStatus.EXPIRED
            row["status"] = "EXPIRED"
        return auth

    def capture(self, auth: Authorisation, amount: int) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        assert row["status"] in ("ACTIVE", "PAID", "SUCCESS"), f"debit on a {row['status']} block"
        assert auth.captured_amount + amount <= auth.amount, "debit above the authorised cap"
        assert auth.multi_debit or auth.captured_amount == 0, f"{auth.instrument} allows one capture"
        if row["member"] in self.fail_capture_for:
            raise CaptureFailed("issuer declined: " + ("card authorisation reversed by the issuer"
                                                       if auth.instrument == "CARD_PREAUTH" else "U30 debit failed at the remitter bank"))
        row["status"] = "SUCCESS"
        row["captured"] += amount
        if not auth.prepaid:
            self._pool += amount                               # settles into Quorum's account, in full, EMI or not
        auth.status = AuthStatus.CAPTURED                      # prepaid money was already in the pool
        auth.captured_amount += amount
        return auth

    def refund(self, auth: Authorisation) -> Authorisation:
        row = self.ledger[auth.rail_ref]
        self._pool -= auth.amount if auth.prepaid else auth.captured_amount   # prepaid: the whole payment goes back
        row["status"], row["captured"] = "REFUNDED", 0
        auth.status, auth.captured_amount = AuthStatus.REFUNDED, 0
        return auth

    def pay_supplier(self, supplier: str, amount: int, purpose: str, reference: str) -> Payout:
        assert amount <= self._pool, f"pool ₹{inr(self._pool)} cannot cover ₹{inr(amount)} to {supplier} — Quorum does not front"
        self._pool -= amount
        method = "UPI" if purpose == "STAY" else "B2B_WALLET"
        p = Payout(supplier=supplier, amount=amount, purpose=purpose, reference=reference, method=method, at=clock.now())
        self.payouts.append(p)
        return p

    def receive_refund(self, supplier: str, amount: int, reference: str) -> int:
        self._pool += amount
        return self._pool

    def pool(self) -> int:
        return self._pool

    def release(self, auth: Authorisation) -> Authorisation:
        if auth.prepaid and self.ledger[auth.rail_ref]["status"] == "PAID":
            self._pool -= auth.amount - auth.captured_amount   # the unused prepayment is refunded
        self.ledger[auth.rail_ref]["status"] = "CANCELLED"
        auth.status = AuthStatus.RELEASED
        return auth

    def float_days(self, auth: Authorisation) -> int:
        """How long a prepayment has sat in the pool. The float idea is priced on this."""
        row = self.ledger[auth.rail_ref]
        return (clock.now() - row["prepaid_at"]).days if row.get("prepaid_at") else 0


# --------------------------------------------------------------------------- logistics
_FLIGHTS = {
    # origin -> list of (carrier, dep_hour, dur_h, price). Also used for the return leg into that city.
    "Kolkata":   [("IndiGo", 6, 2.7, 6400), ("Air India", 14, 2.8, 9900), ("IndiGo", 19, 2.7, 5900), ("SpiceJet", 21, 2.8, 6100)],
    "Bengaluru": [("IndiGo", 7, 1.2, 3800), ("Akasa", 12, 1.2, 3400), ("Air India", 18, 1.3, 4600)],
    "Delhi":     [("IndiGo", 5, 2.6, 6100), ("Vistara", 11, 2.7, 8200), ("Akasa", 20, 2.6, 5700)],
    "Mumbai":    [("Akasa", 8, 1.1, 2900), ("IndiGo", 13, 1.1, 3200), ("Air India", 19, 1.2, 3900)],
    "Hyderabad": [("IndiGo", 9, 1.3, 3700), ("Akasa", 15, 1.3, 3500)],
}
_ROAD_KM = {"Mumbai": 590, "Bengaluru": 560, "Hyderabad": 680, "Kolkata": 2000, "Delhi": 1900}   # to Goa, by road
_ACTIVITIES = [
    # name, supplier, interest, price per head, pre-bookable
    ("Dudhsagar jeep-and-hike from Collem", "Collem Jeep Owners' Co-op", "trekking", 1200, True),
    ("Sunrise kayaking at Palolem", "Palolem Kayaks", "water sports", 600, True),
    ("Reis Magos and Fontainhas walk", "Goa Heritage Walks", "heritage", 400, True),
    ("Chorao mangrove boat", "Salim Ali Boat Club", "wildlife", 150, False),
    ("Saturday night market, Arpora", "—", "food", 0, False),
    ("Hilltop / Curlies night", "—", "nightlife", 0, False),
    ("Galgibaga turtle beach", "—", "quiet beaches", 0, False),
    ("Parra road and Butterfly Beach boat", "Palolem boatmen", "reels spots", 500, False),
]
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

    def search_alternatives(self, origin: str, destination: str, after: datetime, seats: int) -> list[Leg]:
        """Later flights today and the first one tomorrow; a train and an outstation cab where the road allows."""
        out: list[Leg] = []
        for l in self.search_legs(origin, destination, after.date()):
            if l.depart > after:
                out.append(l.model_copy(update={"seats": seats}))
        first = min(self.search_legs(origin, destination, after.date() + timedelta(days=1)), key=lambda l: l.depart)
        out.append(first.model_copy(update={"seats": seats}))
        km = _ROAD_KM.get(origin) or _ROAD_KM.get(destination)
        if km and km <= 700:
            dep = after + timedelta(hours=1)
            hours = km / 55
            cab_total = int(round(km * 16 / 100.0)) * 100                    # ₹16/km, the whole car
            out.append(Leg(member_id="", mode="cab", carrier="Uber Outstation", origin=origin, destination=destination,
                           depart=dep, arrive=dep + timedelta(hours=hours), price=math.ceil(cab_total / seats), seats=seats))
            train_dep = datetime.combine(after.date(), time(22, 0))
            if train_dep > after:
                out.append(Leg(member_id="", mode="train", carrier="Konkan Kanya Express", origin=origin, destination=destination,
                               depart=train_dep, arrive=train_dep + timedelta(hours=11), price=1500, seats=seats))
        return sorted(out, key=lambda l: (l.arrive, l.price))

    def search_activities(self, city: str, interests: list[str], persona: str) -> list[Activity]:
        acts = [Activity(name=n, supplier=s, interest=i, price_per_head=p, prebook=b) for n, s, i, p, b in _ACTIVITIES]
        if persona == "families":
            acts = [a for a in acts if a.interest != "nightlife"]
        return [a for a in acts if a.interest in interests]

    def book_activity(self, activity: Activity, heads: int) -> Activity:
        activity.booking_ref = "ACT" + str(self.rng.randint(10000, 99999))
        return activity
