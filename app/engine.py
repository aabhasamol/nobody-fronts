"""The Quorum loop as a state machine.

    TRIGGERED → CAPTURING → BUILDING → POSTED → BOOKED
                                              ↘ LAPSED → (re-run → POSTED | CLOSED)

Every user-visible utterance goes through `_say`, so the events list is a
complete transcript of what the group and each member saw. The demo UI and
`demo.py` both read from it.
"""
from __future__ import annotations
import math
from datetime import date, datetime, timedelta
from typing import Optional
from . import messages as M
from .clock import clock
from .models import (Trip, Member, Constraint, Bundle, Leg, Stay, Authorisation, AuthStatus,
                     TripState, Event)
from .rails.base import VoiceRail, PaymentsRail, LogisticsRail

CAPTURE_WINDOW_H = 24        # how long members get to reply to the private DM
DECISION_WINDOW_H = 48       # how long the group gets to authorise once bundles are posted
REBOOK_BUFFER = 0.10         # block share × 1.10, debit only the share; the 10% is re-booking headroom
VERIFY_QUESTIONS = ["room_as_pictured", "road_motorable", "refund_terms", "twin_sharing_available"]


class Engine:
    def __init__(self, voice: VoiceRail, payments: PaymentsRail, logistics: LogisticsRail):
        self.voice, self.payments, self.logistics = voice, payments, logistics
        self.trips: dict[str, Trip] = {}

    # ------------------------------------------------------------------ utils
    def _say(self, trip: Trip, channel: str, text: str, actor: str = "agent") -> None:
        trip.events.append(Event(at=clock.now(), channel=channel, actor=actor, text=text))

    def _rail(self, trip: Trip, rail: str, text: str) -> None:
        self._say(trip, f"rail:{rail}", text, actor=rail)

    # ------------------------------------------------------------------ 1. trigger
    def trigger(self, name: str, organiser: Member, members: list[Member], destination: str,
                start: date, end: date, rough_budget: int, quorum: int) -> Trip:
        assert organiser in members
        trip = Trip(name=name, organiser_id=organiser.id, members=members, destination=destination,
                    start=start, end=end, rough_budget=rough_budget, quorum=quorum)
        self.trips[trip.id] = trip
        self._say(trip, "group", M.hello_group(trip))
        # 2. load — what it already knows is on the Member objects (home city, ceiling, past trips)
        self._say(trip, "system", "Loaded per member: " + "; ".join(
            f"{m.name.split()[0]} — {m.home_city}, ceiling ₹{m.spend_ceiling:,}, {len(m.past_trips)} past trips"
            for m in members), actor="system")
        # 3. private capture
        trip.capture_deadline = clock.now() + timedelta(hours=CAPTURE_WINDOW_H)
        for m in members:
            self._say(trip, f"dm:{m.id}", M.dm_capture(trip, m, trip.capture_deadline))
        trip.state = TripState.CAPTURING
        return trip

    # ------------------------------------------------------------------ 3. capture
    def capture(self, trip: Trip, member_id: str, available: bool = True, budget_ceiling: Optional[int] = None,
                must_haves: Optional[list[str]] = None, earliest: Optional[date] = None,
                latest: Optional[date] = None, text: Optional[str] = None) -> None:
        assert trip.state == TripState.CAPTURING, f"cannot capture in {trip.state}"
        m = trip.member(member_id)
        if text:
            self._say(trip, f"dm:{m.id}", text, actor=m.name)
        c = Constraint(member_id=member_id, available=available, budget_ceiling=budget_ceiling,
                       must_haves=must_haves or [], earliest=earliest, latest=latest, replied_at=clock.now())
        trip.constraints[member_id] = c
        self._say(trip, f"dm:{m.id}", M.dm_capture_ack(m))
        if all(mm.id in trip.constraints for mm in trip.members):
            self.build(trip)

    def close_capture(self, trip: Trip) -> None:
        """Called by the clock when the capture window ends. Silence = default applies."""
        if trip.state != TripState.CAPTURING:
            return
        for m in trip.members:
            if m.id not in trip.constraints:
                trip.constraints[m.id] = Constraint(member_id=m.id, available=True,
                                                    budget_ceiling=min(trip.rough_budget, m.spend_ceiling))
                self._say(trip, "system", f"{m.name.split()[0]} did not reply; default applied.", actor="system")
        self.build(trip)

    # ------------------------------------------------------------------ 4. build + 5. verify
    def _ceiling(self, trip: Trip) -> int:
        vals = []
        for m in trip.members:
            c = trip.constraints.get(m.id)
            if c and c.available:
                vals.append(min(c.budget_ceiling or m.spend_ceiling, m.spend_ceiling))
        return min(vals) if vals else trip.rough_budget

    def _travellers(self, trip: Trip) -> list[Member]:
        return [m for m in trip.members if trip.constraints.get(m.id, Constraint(member_id=m.id)).available]

    def _legs_for(self, trip: Trip, travellers: list[Member], pick: str) -> list[Leg]:
        legs: list[Leg] = []
        for m in travellers:
            out = self.logistics.search_legs(m.home_city, trip.destination, trip.start)
            back = self.logistics.search_legs(trip.destination, m.home_city, trip.end)
            if pick == "cheapest":
                chooser = lambda xs: xs[0]                                              # cheapest first
            else:
                chooser = lambda xs: sorted(xs, key=lambda l: l.depart.hour)[len(xs) // 2]  # civilised hour
            for l in (chooser(out), chooser(back)):
                legs.append(l.model_copy(update={"member_id": m.id}))
        return legs

    def build(self, trip: Trip) -> None:
        trip.state = TripState.BUILDING
        travellers = self._travellers(trip)
        ceiling = self._ceiling(trip)
        must = sorted({h for c in trip.constraints.values() for h in c.must_haves})
        nights = trip.nights()
        stays = self.logistics.search_stays(trip.destination, trip.start, nights, must)
        self._rail(trip, "logistics", f"Quoted {len(stays)} stays and legs for {len(travellers)} travellers; "
                                      f"binding ceiling ₹{ceiling:,}/head; must-haves: {must or 'none'}")

        # Lean = cheapest verified stay + cheapest legs. Comfort = next tier up, mid-day legs.
        lean_stay = self._first_verified(trip, stays, tier=0)
        comfort_stay = self._first_verified(trip, [s for s in stays if s.id != lean_stay.id], tier=1)
        lean = Bundle(label="Lean", legs=self._legs_for(trip, travellers, "cheapest"), stay=lean_stay, is_default=True)
        comfort = Bundle(label="Comfort", legs=self._legs_for(trip, travellers, "midday"), stay=comfort_stay)
        trip.bundles = [lean, comfort]
        # If Lean breaches someone's own ceiling, say so instead of posting an unaffordable default.
        over = [m.name.split()[0] for m in travellers
                if lean.per_head(m.id) > min(trip.constraints[m.id].budget_ceiling or m.spend_ceiling, m.spend_ceiling)]
        if over:
            self._say(trip, "system", f"Lean is above the stated ceiling for {over}; posting anyway with a flag.",
                      actor="system")
        trip.default_bundle_id = lean.id
        self._say(trip, "group", M.verified_line(trip))
        self.post(trip)

    def _first_verified(self, trip: Trip, stays: list[Stay], tier: int) -> Stay:
        """Walk the list from `tier`, phone-verify, return the first that passes. Log every call."""
        candidates = stays[tier:] + stays[:tier]
        for s in candidates:
            rec = self.voice.verify_property(s, language="hi-IN", questions=VERIFY_QUESTIONS)
            s.verification = rec
            self._rail(trip, "voice", f"Called {s.name} ({s.phone}) in {rec.language}: {rec.disposition}. "
                                      f"Room as pictured: {rec.answers['room_as_pictured']}")
            if rec.disposition == "VERIFIED":
                return s
            self._say(trip, "system", M.swap_line(s.name, "next candidate", rec.answers["room_as_pictured"]),
                      actor="system")
        raise RuntimeError("no property verified")

    # ------------------------------------------------------------------ 6. post + 7. authorise
    def post(self, trip: Trip) -> None:
        trip.posted_at = clock.now()
        trip.deadline = clock.now() + timedelta(hours=DECISION_WINDOW_H)
        trip.state = TripState.POSTED
        self._say(trip, "group", M.post_bundles(trip, trip.deadline))
        b = trip.default_bundle()
        validity_days = max(1, (trip.deadline - clock.now()).days + 2)
        for m in self._travellers(trip):
            share = b.per_head(m.id)
            block = int(math.ceil(share * (1 + REBOOK_BUFFER) / 100.0) * 100)
            auth = self.payments.create_block(m, block, validity_days, reference=b.id)
            trip.authorisations[m.id] = auth
            self._rail(trip, "payments", f"OT mandate {auth.rail_ref} created for {m.name.split()[0]}: "
                                         f"₹{block:,} block (share ₹{share:,} + re-booking headroom), "
                                         f"{validity_days}d validity — PENDING approval")
            self._say(trip, f"dm:{m.id}", M.dm_authorise(trip, m, b, share, block))

    def switch_default(self, trip: Trip, bundle_id: str) -> None:
        """Organiser (or a group majority) asks for the other bundle. Re-issues mandates at new shares."""
        assert trip.state == TripState.POSTED
        for b in trip.bundles:
            b.is_default = (b.id == bundle_id)
        trip.default_bundle_id = bundle_id
        for a in list(trip.authorisations.values()):
            if a.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
                self.payments.release(a)
        trip.authorisations.clear()
        self._say(trip, "group", f"Default switched to {trip.bundle(bundle_id).label}. Re-sending share requests.")
        self.post(trip)

    def member_approves(self, trip: Trip, member_id: str) -> Authorisation:
        """The member approved the mandate in their UPI app (or the sandbox simulator did)."""
        assert trip.state == TripState.POSTED
        auth = trip.authorisations[member_id]
        if hasattr(self.payments, "approve"):          # mock / simulator convenience
            self.payments.approve(auth)
        auth = self.payments.refresh(auth)
        m = trip.member(member_id)
        self._rail(trip, "payments", f"{m.name.split()[0]} approved: mandate {auth.rail_ref} ACTIVE — ₹{auth.amount:,} blocked")
        self._say(trip, "group", f"{m.name.split()[0]} is in ({len(trip.blocked())}/{trip.quorum}).")
        if len(trip.blocked()) == len(self._travellers(trip)):
            self._say(trip, "group", "Everyone's in — closing early.")
            self.decide(trip)
        return auth

    # ------------------------------------------------------------------ 8. decide
    def tick(self, trip: Trip) -> None:
        """Advance the trip against the clock. Call after every clock.advance()."""
        now = clock.now()
        if trip.state == TripState.CAPTURING and trip.capture_deadline and now >= trip.capture_deadline:
            self.close_capture(trip)
        if trip.state == TripState.POSTED and trip.deadline and now >= trip.deadline:
            self.decide(trip)

    def decide(self, trip: Trip) -> None:
        for a in trip.authorisations.values():
            self.payments.refresh(a)
        if trip.quorum_met():
            self.book(trip)
        else:
            self.lapse(trip)

    def book(self, trip: Trip) -> None:
        b = trip.default_bundle()
        committed = [trip.member(a.member_id) for a in trip.blocked()]
        # Capture. Today: N presentations, one per mandate. The ask to Pine Labs is to make this one atomic call.
        captured = []
        for a in trip.blocked():
            share = b.per_head(a.member_id)
            self.payments.capture(a, share)
            captured.append(a)
            self._rail(trip, "payments", f"Presentation on {a.rail_ref}: ₹{share:,} debited (₹{a.amount - share:,} headroom left on the mandate)")
        # Whoever did not commit is released now; they are not on this trip and nothing is held.
        for a in trip.authorisations.values():
            if a.status == AuthStatus.PENDING:
                self.payments.release(a)
                self._rail(trip, "payments", f"Mandate {a.rail_ref} released — {trip.member(a.member_id).name.split()[0]} did not commit")
        # Book only for those who committed.
        b.stay = self.logistics.book_stay(b.stay, committed)
        self._rail(trip, "logistics", f"Stay confirmed {b.stay.booking_ref} for {len(committed)} guests")
        for a in captured:
            m = trip.member(a.member_id)
            for l in b.legs:
                if l.member_id == m.id:
                    self.logistics.book_leg(l, m)
            self._rail(trip, "logistics", f"Ticketed {m.name.split()[0]}: " + ", ".join(
                f"{l.carrier} {l.origin}→{l.destination} {l.pnr}" for l in b.legs if l.member_id == m.id))
            self._say(trip, f"dm:{m.id}", M.dm_confirmation(trip, m, b))
        trip.state = TripState.BOOKED
        self._say(trip, "group", M.booked_group(trip, b))

    def lapse(self, trip: Trip) -> None:
        committed = [a.member_id for a in trip.blocked()]           # read before releasing
        for a in trip.authorisations.values():
            if a.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
                self.payments.release(a)
                self._rail(trip, "payments", f"Mandate {a.rail_ref} released — nothing charged")
        trip.state = TripState.LAPSED
        self._say(trip, "group", M.lapsed_group(trip, len(committed)))
        self._say(trip, f"dm:{trip.organiser_id}", M.dm_lapsed_organiser(trip, committed))

    def rerun(self, trip: Trip, bundle_id: Optional[str] = None, quorum: Optional[int] = None) -> None:
        assert trip.state == TripState.LAPSED
        if quorum:
            trip.quorum = quorum
        if bundle_id:
            for b in trip.bundles:
                b.is_default = (b.id == bundle_id)
            trip.default_bundle_id = bundle_id
        trip.authorisations.clear()
        self._say(trip, "group", f"Re-running with {trip.default_bundle().label} as default, quorum {trip.quorum}.")
        self.post(trip)

    def close(self, trip: Trip) -> None:
        trip.state = TripState.CLOSED
        self._say(trip, "group", "Trip closed. Nobody was charged.")

    # ------------------------------------------------------------------ later: disruption → re-book
    def disrupt(self, trip: Trip, leg_id: str) -> None:
        """A carrier cancels one leg. Re-book inside the member's already-authorised cap; else escalate."""
        assert trip.state == TripState.BOOKED
        b = trip.default_bundle()
        leg = next(l for l in b.legs if l.id == leg_id)
        m = trip.member(leg.member_id)
        self.logistics.cancel_leg(leg)
        self._rail(trip, "logistics", f"Carrier cancelled {leg.carrier} {leg.origin}→{leg.destination} PNR {leg.pnr}")
        auth = trip.authorisations[m.id]
        headroom = auth.amount - auth.captured_amount + leg.price      # refund of the cancelled leg + any slack
        options = [o for o in self.logistics.search_legs(leg.origin, leg.destination, leg.depart.date())
                   if o.carrier != leg.carrier or o.depart != leg.depart]
        affordable = [o for o in options if o.price <= headroom]
        if affordable:
            new = affordable[0].model_copy(update={"member_id": m.id})
            self.logistics.book_leg(new, m)
            new.status = "REBOOKED"
            b.legs = [new if l.id == leg.id else l for l in b.legs]
            self._rail(trip, "logistics", f"Re-booked {new.carrier} {new.depart:%H:%M} PNR {new.pnr} at ₹{new.price:,} (cap headroom ₹{headroom:,})")
            self._say(trip, "group", M.disruption_group(f"{leg.carrier} {leg.depart:%H:%M}",
                                                         f"{new.carrier} {new.depart:%H:%M}", m))
            self._say(trip, f"dm:{m.id}", f"New PNR {new.pnr} — {new.carrier} {new.origin}→{new.destination} {new.depart:%d %b %H:%M}.")
        else:
            shortfall = min(o.price for o in options) - headroom
            self._say(trip, "group", M.disruption_escalate(m, shortfall))
            self._say(trip, f"dm:{m.id}", f"Cheapest alternative is ₹{shortfall:,} over your authorised cap. Approve a top-up of that amount and I'll re-book.")
