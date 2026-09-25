"""The Quorum loop as a state machine.

    INITIATED → GATHERING → PLANNING → VOTING → AUTHORISING → BOOKING → BOOKED
                               ▲          │ no majority      │ dropout → re-price       │ leg cancelled → re-book
                               └──────────┘ (≤ 2 revisions,  │ over the cap → back      │ inside the cap, or a
                               organiser decides after that)  │ to the group             │ top-up from that member
                                                              ▼                          ▼
                                                    LAPSED (nothing charged) ◄── one debit fails → refund the rest

Organiser gives a rough budget and a maximum overshoot → the agent builds one itinerary inside it → each
member votes privately, only the tally is posted → the trip's budget is then set by the people who are in
(their number × the lowest ceiling among them), the plan is re-sized for exactly them and must fit → each
authorises share × (1 + overshoot) → the agent debits each share into Quorum's account (the pool) and pays
every supplier from it. Voice is used in two places only: phone-only stays, and a member who has not
answered a time-critical text.

Every user-visible utterance goes through `_say`, so the events list is a complete transcript of what the
group and each member saw. The demo UI and `demo.py` both read from it.
"""
from __future__ import annotations
import math
from datetime import date, datetime, timedelta
from typing import Optional
from . import messages as M
from .clock import clock
from .models import (Trip, Member, Constraint, Plan, Leg, Stay, Vote, Authorisation, AuthStatus, Decision,
                     TripState, Event, CallRecord, INSTRUMENTS)
from .rails.base import VoiceRail, PaymentsRail, LogisticsRail, CaptureFailed

GATHER_WINDOW_H = 24         # how long members get to answer the private DM
VOTE_WINDOW_H = 24           # how long members get to vote; one reminder text at the halfway mark
AUTH_WINDOW_H = 48           # how long yes-voters get to approve the UPI block
MAX_REVISIONS = 2            # failed votes lead to at most two revised plans; then the organiser decides
ESCALATE_AFTER_MIN = 20      # a disruption text unanswered for this long becomes a call
FUNCTION_HOUR = 18           # wedding / offsite: arrive before the first function
LATE_ARRIVAL_HOUR = 20       # a re-booked arrival at or after this hour gets the phone-only stay a call
PRICE_WORDS = ("expensive", "cheap", "budget", "cost", "price", "₹", "money", "afford", "pricey", "over")
CALL_LANGUAGE = "hi-IN"

# Calls are for P0 things only. A P0 is: the other side cannot be reached any other way in time, and what
# they say changes what the agent does next. These three are the whole list; everything else is a text.
P0_CALLS = {
    "SUPPLIER_AVAILABILITY": "a stay with no online inventory; the plan cannot go to the vote (or the booking) without its answer",
    "LATE_ARRIVAL": "a guest will arrive late at a phone-only stay; without the call the room goes to a walk-in",
    "DISRUPTION_UNANSWERED": "a member's leg is cancelled, the options text went unanswered, and the choice cannot wait",
}


def ceil100(x: float) -> int:
    return int(math.ceil(x / 100.0) * 100)


class Engine:
    def __init__(self, voice: VoiceRail, payments: PaymentsRail, logistics: LogisticsRail):
        self.voice, self.payments, self.logistics = voice, payments, logistics
        self.trips: dict[str, Trip] = {}

    # ------------------------------------------------------------------ utils
    def _say(self, trip: Trip, channel: str, text: str, actor: str = "agent", priority: str = "P1") -> None:
        trip.events.append(Event(at=clock.now(), channel=channel, actor=actor, text=text, priority=priority))

    def _sys(self, trip: Trip, text: str) -> None:
        self._say(trip, "system", text, actor="system")

    def _rail(self, trip: Trip, rail: str, text: str, priority: str = "P1") -> None:
        self._say(trip, f"rail:{rail}", text, actor=rail, priority=priority)

    def _call(self, trip: Trip, reason: str, place_call, describe: str) -> CallRecord:
        """The only way the engine picks up the phone. `reason` must be one of P0_CALLS."""
        assert reason in P0_CALLS, f"not a P0 reason: {reason}"
        rec = place_call()
        self._rail(trip, "voice", f"{reason}: {describe} — {rec.disposition}", priority="P0")
        return rec

    # ------------------------------------------------------------------ 1. initiate → gather
    def trigger(self, name: str, organiser: Member, members: list[Member], destination: str, start: date, end: date,
                occasion: str, budget: int, overshoot: float, venue: str = "") -> Trip:
        assert organiser in members
        trip = Trip(name=name, organiser_id=organiser.id, members=members, destination=destination, venue=venue,
                    start=start, end=end, occasion=occasion, budget=budget, overshoot=overshoot)
        self.trips[trip.id] = trip
        self._say(trip, "group", M.hello_group(trip))
        self._sys(trip, "Loaded per member: " + "; ".join(
            f"{m.first} — {m.home_city}, {len(m.past_trips)} past trips" for m in members))
        trip.gather_deadline = clock.now() + timedelta(hours=GATHER_WINDOW_H)
        for m in members:
            self._say(trip, f"dm:{m.id}", M.dm_gather(trip, m, trip.gather_deadline))
        trip.state = TripState.GATHERING
        return trip

    def gather(self, trip: Trip, member_id: str, start_city: Optional[str] = None, return_city: Optional[str] = None,
               available: bool = True, must_haves: Optional[list[str]] = None, earliest: Optional[date] = None,
               latest: Optional[date] = None, budget: Optional[int] = None, text: Optional[str] = None) -> None:
        assert trip.state == TripState.GATHERING, f"cannot gather in {trip.state}"
        m = trip.member(member_id)
        if text:
            self._say(trip, f"dm:{m.id}", text, actor=m.name)
        start_city = start_city or m.home_city
        return_city = return_city or start_city
        trip.constraints[member_id] = Constraint(member_id=member_id, available=available, start_city=start_city,
                                                 return_city=return_city, must_haves=must_haves or [],
                                                 earliest=earliest, latest=latest, budget=budget or trip.budget,
                                                 replied_at=clock.now())
        self._say(trip, f"dm:{m.id}", M.dm_gather_ack(trip, m, start_city, return_city, available, trip.ceiling(m.id)), priority="P2")
        if all(mm.id in trip.constraints for mm in trip.members):
            self.plan(trip)

    def close_gathering(self, trip: Trip) -> None:
        """Called by the clock when the window ends. Silence = home city both ways on the stated dates."""
        if trip.state != TripState.GATHERING:
            return
        for m in trip.members:
            if m.id not in trip.constraints:
                trip.constraints[m.id] = Constraint(member_id=m.id, start_city=m.home_city, return_city=m.home_city,
                                                    budget=trip.budget, defaulted=True)
                self._sys(trip, f"{m.first} did not reply; default applied ({m.home_city} both ways, ₹{trip.budget:,} ceiling).")
        self.plan(trip)

    # ------------------------------------------------------------------ 2. plan: trip side + travel side
    def plan(self, trip: Trip) -> None:
        """First plan, for everyone available. If nothing fits, the organiser hears why and the trip waits."""
        trip.state = TripState.PLANNING
        travellers = trip.travellers()
        trip.budget_floor = trip.total_budget = None          # a proposal: nobody is in yet
        p, over = self._plan(trip, travellers, trip.start, trip.end)
        if not self._acceptable(trip, p, over):
            trip.awaiting_organiser = "no_fit"
            self._say(trip, f"dm:{trip.organiser_id}", M.dm_no_fit(trip, p, over))
            return
        self._accept(trip, p, over)

    def organiser_adjusts(self, trip: Trip, budget: Optional[int] = None, overshoot: Optional[float] = None,
                          end: Optional[date] = None, text: Optional[str] = None) -> None:
        """The organiser answers a no-fit: raise the budget, widen the overshoot, or shorten the trip."""
        assert trip.awaiting_organiser == "no_fit", "nothing to adjust"
        org = trip.organiser()
        if text:
            self._say(trip, f"dm:{org.id}", text, actor=org.name)
        if budget:
            trip.budget = budget
        if overshoot is not None:
            trip.overshoot = overshoot
        if end:
            trip.end = end
        trip.awaiting_organiser = None
        self._say(trip, f"dm:{org.id}", f"Re-planning at {M.fmt_inr(trip.limit())} a head, {trip.start:%d}–{trip.end:%d %b}.")
        self.plan(trip)

    def _plan(self, trip: Trip, travellers: list[Member], start: date, end: date, note: str = "",
              exclude: frozenset[str] = frozenset(), max_rate: Optional[int] = None,
              only: Optional[str] = None) -> tuple[Optional[Plan], list[Member]]:
        """Build the best itinerary for these travellers and dates. Returns (plan, members over the limit).
        (None, []) when no stay is usable at all. Does not change the trip."""
        nights = (end - start).days
        must = sorted({h for c in trip.constraints.values() for h in c.must_haves})
        stays = self.logistics.search_stays(trip.destination, start, nights, must)
        stays = [s for s in stays if s.name not in exclude and s.name not in trip.stays_out
                 and (max_rate is None or s.rate_per_room_night <= max_rate)
                 and (only is None or s.name == only)]
        ranked = self._rank(trip, stays)
        self._rail(trip, "logistics",
                   f"{len(ranked)} stays quoted in {trip.destination}, {nights} nights "
                   f"({sum(s.phone_only for s in ranked)} phone-only); legs for {len(travellers)} travellers, "
                   f"out and back searched separately; limit ₹{trip.limit():,}/head all-in; "
                   f"must-haves: {', '.join(must) or 'none'}" + (f"; {note}" if note else ""))
        best: Optional[tuple[Plan, list[Member]]] = None
        for s in ranked:
            if s.phone_only and not self._supplier_call(trip, s, len(travellers), start, nights):
                continue
            p = Plan(version=len(trip.plans) + 1, travellers=[m.id for m in travellers],
                     legs=self._legs_for(trip, travellers, start, end), stays=[s], start=start, end=end, note=note)
            ok, over = self._fit(trip, p)
            if ok:
                return p, over
            if best is None or len(over) < len(best[1]) or (len(over) == len(best[1]) and p.total() < best[0].total()):
                best = (p, over)
        return best if best else (None, [])

    def _fit(self, trip: Trip, p: Plan) -> tuple[bool, list[Member]]:
        """Before the vote the proposal is held to the organiser's rough budget plus overshoot, per head. After it,
        the plan must fit the budget the yes-voters set: their number × the lowest ceiling among them."""
        travellers = [trip.member(t) for t in p.travellers]
        if trip.total_budget is not None:
            return p.total() <= trip.total_budget, [m for m in travellers if p.per_head(m.id) > trip.budget_floor]
        over = [m for m in travellers if p.per_head(m.id) > trip.limit()]
        return not over, over

    def _acceptable(self, trip: Trip, p: Optional[Plan], over: list[Member]) -> bool:
        if p is None:
            return False
        if trip.total_budget is not None:                     # after the vote the total is the rule, full stop
            return p.total() <= trip.total_budget
        return len(over) <= len(p.travellers) // 2            # a proposal may carry a minority over the line, flagged

    def _accept(self, trip: Trip, p: Plan, over: list[Member]) -> None:
        trip.plans.append(p)
        if trip.total_budget is None:
            for m in over:                                    # one member's travel alone breaks the limit
                self._say(trip, f"dm:{m.id}", M.dm_leg_over_limit(trip, m, p))
        self.open_vote(trip)

    def _rank(self, trip: Trip, stays: list[Stay]) -> list[Stay]:
        """The occasion sets the priority: leisure = price first; wedding / offsite / pilgrimage = distance first."""
        if trip.occasion in ("wedding", "offsite", "pilgrimage", "family"):
            return sorted(stays, key=lambda s: (s.km_to_venue, s.rate_per_room_night))
        return sorted(stays, key=lambda s: (s.rate_per_room_night, s.km_to_venue))

    def _pick_leg(self, options: list[Leg], occasion: str, outbound: bool) -> Leg:
        cheapest = sorted(options, key=lambda l: l.price)
        if occasion in ("wedding", "offsite") and outbound:      # arrive before the first function, then price
            early = [o for o in cheapest if o.arrive.hour < FUNCTION_HOUR]
            return early[0] if early else cheapest[0]
        if occasion in ("pilgrimage", "family"):                  # comfort: daytime legs over red-eyes
            day = [o for o in cheapest if 6 <= o.depart.hour <= 20]
            return day[0] if day else cheapest[0]
        return cheapest[0]

    def _legs_for(self, trip: Trip, travellers: list[Member], start: date, end: date) -> list[Leg]:
        """Travel is per member: out from their start city, back to their return city, searched separately."""
        legs: list[Leg] = []
        for m in travellers:
            c = trip.constraints[m.id]
            out = self._pick_leg(self.logistics.search_legs(c.start_city, trip.destination, start), trip.occasion, True)
            back = self._pick_leg(self.logistics.search_legs(trip.destination, c.return_city, end), trip.occasion, False)
            for l in (out, back):
                legs.append(l.model_copy(update={"member_id": m.id, "quoted": l.price}))
        return legs

    def _supplier_call(self, trip: Trip, stay: Stay, party: int, check_in: date, nights: int) -> bool:
        """Voice job 1. Retry once on no answer; take the rate from the call, not the listing."""
        rec = None
        for attempt in (1, 2):
            rec = self._call(trip, "SUPPLIER_AVAILABILITY",
                             lambda: self.voice.call_supplier(stay, party, check_in, nights, language=CALL_LANGUAGE),
                             f"Called {stay.name} ({stay.phone}), {CALL_LANGUAGE}, attempt {attempt}")
            stay.calls.append(rec)
            if rec.disposition == "CONFIRMED":
                rate = f"₹{rec.rate_per_room_night:,}/night" if rec.rate_per_room_night else "rate not caught"
                hold = f"held till {rec.hold_until:%a %d %b %H:%M}" if rec.hold_until else "no hold agreed"
                self._rail(trip, "voice", f"  {rec.rooms} twin rooms at {rate}, refund: {rec.refund_terms or '?'}, {hold}")
            if rec.disposition != "NO_ANSWER":
                break
        if rec.disposition != "CONFIRMED":
            trip.stays_out.append(stay.name)                   # never call them again for this trip
            self._sys(trip, f"{stay.name}: {rec.disposition.lower().replace('_', ' ')} after "
                            f"{len([c for c in stay.calls if c.purpose == 'AVAILABILITY'])} call(s); moving to the next stay.")
            return False
        if rec.rate_per_room_night and rec.rate_per_room_night != stay.rate_per_room_night:
            self._sys(trip, f"{stay.name} quoted ₹{rec.rate_per_room_night:,}/room/night on the call, not the "
                            f"₹{stay.rate_per_room_night:,} we had. Using the call's price.")
            stay.rate_per_room_night = rec.rate_per_room_night
        stay.hold_until = rec.hold_until or (clock.now() + timedelta(hours=48))
        return True

    # ------------------------------------------------------------------ 3. vote (private; tally only)
    def open_vote(self, trip: Trip) -> None:
        p = trip.plan()
        trip.votes, trip.reminded = {}, []
        trip.vote_deadline = clock.now() + timedelta(hours=VOTE_WINDOW_H)
        trip.state = TripState.VOTING
        for t in p.travellers:
            self._say(trip, f"dm:{t}", M.dm_vote(trip, trip.member(t), p, trip.vote_deadline))

    def vote(self, trip: Trip, member_id: str, yes: bool, reason: str = "") -> None:
        assert trip.state == TripState.VOTING, f"cannot vote in {trip.state}"
        p = trip.plan()
        assert member_id in p.travellers, "not on this plan"
        m = trip.member(member_id)
        self._say(trip, f"dm:{m.id}", ("Yes" if yes else "No") + (f". {reason}" if reason else "."), actor=m.name)
        trip.votes[member_id] = Vote(member_id=member_id, yes=yes, reason=reason, at=clock.now())
        self._say(trip, f"dm:{m.id}", M.dm_vote_ack(m, yes), priority="P2")
        if all(t in trip.votes for t in p.travellers):
            self.tally(trip)

    def close_vote(self, trip: Trip) -> None:
        if trip.state != TripState.VOTING:
            return
        for t in trip.plan().travellers:
            if t not in trip.votes:
                self._sys(trip, f"{trip.member(t).first} did not vote; counted as not in.")
        self.tally(trip)

    def tally(self, trip: Trip) -> None:
        p = trip.plan()
        yes = trip.yes_voters()
        no = [v for v in trip.votes.values() if not v.yes]
        silent = [t for t in p.travellers if t not in trip.votes]
        if len(yes) >= trip.majority():
            trip.auth_deadline = clock.now() + timedelta(hours=AUTH_WINDOW_H)
            self.open_authorising(trip, M.group_tally(trip, len(yes), len(no), len(silent), passed=True))
            return
        revising = len(trip.plans) - 1 < MAX_REVISIONS
        self._say(trip, "group", M.group_tally(trip, len(yes), len(no), len(silent), passed=False, revising=revising))
        if revising:
            self.revise(trip)
        else:
            trip.awaiting_organiser = "vote_failed"
            self._say(trip, f"dm:{trip.organiser_id}", M.dm_organiser_decides(trip))

    def revise(self, trip: Trip, travellers: Optional[list[Member]] = None, hint: Optional[str] = None) -> None:
        """Build the next plan from the private 'what would make it a yes' replies."""
        cur = trip.plan()
        travellers = travellers or [trip.member(t) for t in cur.travellers]
        reasons = hint if hint is not None else " ".join(v.reason.lower() for v in trip.votes.values() if not v.yes)
        used = frozenset(s.name for pp in trip.plans for s in pp.stays)
        trip.state = TripState.PLANNING
        attempts: list[dict] = []
        if any(w in reasons for w in PRICE_WORDS):
            attempts.append(dict(start=cur.start, end=cur.end, exclude=used,
                                 max_rate=min(s.rate_per_room_night for s in cur.stays) - 1, note="a cheaper stay"))
            if cur.nights() > 2:
                attempts.append(dict(start=cur.start, end=cur.end - timedelta(days=1), only=cur.stays[0].name,
                                     note="same stay, one night fewer"))
        attempts.append(dict(start=cur.start, end=cur.end, exclude=used, note="a different stay"))
        for a in attempts:
            p, over = self._plan(trip, travellers, **a)
            if self._acceptable(trip, p, over):
                self._accept(trip, p, over)
                return
        trip.awaiting_organiser = "vote_failed"
        self._say(trip, f"dm:{trip.organiser_id}", M.dm_organiser_decides(trip))

    def organiser_decides(self, trip: Trip, go: bool, text: Optional[str] = None) -> None:
        """After two failed revisions: book for the yes-voters only, or close."""
        assert trip.awaiting_organiser == "vote_failed", "nothing to decide"
        org = trip.organiser()
        if text:
            self._say(trip, f"dm:{org.id}", text, actor=org.name)
        trip.awaiting_organiser = None
        if go and trip.yes_voters():
            trip.auth_deadline = clock.now() + timedelta(hours=AUTH_WINDOW_H)
            self.open_authorising(trip, M.group_organiser_go(trip))
        else:
            self.lapse(trip, f"{org.first} closed the trip after the vote didn't pass.")

    # ------------------------------------------------------------------ 4. authorise (yes-voters only)
    def open_authorising(self, trip: Trip, intro: str) -> None:
        """The people who are in set the budget; the plan is re-sized for exactly them and must fit it."""
        p = trip.plan()
        ins = trip.in_members()
        old = {t: p.per_head(t) for t in p.travellers}
        trip.set_budget([m.id for m in ins])
        changes = self._retarget(trip, p, [m.id for m in ins])   # the stay now splits among those who are in
        self._rail(trip, "logistics", f"Budget set by the {len(ins)} who are in: {len(ins)} × ₹{trip.budget_floor:,} "
                                      f"(lowest ceiling) = ₹{trip.total_budget:,}; plan re-sized for {len(ins)} comes to "
                                      f"₹{p.total():,}" + (f" ({'; '.join(changes)})" if changes else ""))
        if p.total() > trip.total_budget:
            self._say(trip, "group", intro)
            return self._back_to_group(trip, ins, M.why_over_budget(trip, p))
        self._say(trip, "group", M.group_go(trip, intro, changes))
        trip.state = TripState.AUTHORISING
        trip.authorisations.clear()
        trip.reminded = []
        validity_days = max(1, (trip.auth_deadline - clock.now()).days + 2)
        for m in ins:
            share = p.per_head(m.id)
            cap = ceil100(share * (1 + trip.overshoot))
            auth = self.payments.create_block(m, cap, validity_days, reference=p.id)
            trip.authorisations[m.id] = auth
            self._rail(trip, "payments", f"Block {auth.rail_ref} created for {m.first}: ₹{cap:,} "
                                         f"(share ₹{share:,} × {1 + trip.overshoot:.2f}), {validity_days}d validity, "
                                         f"UPI Reserve Pay or card pre-auth at the member's choice — PENDING")
            self._say(trip, f"dm:{m.id}", M.dm_authorise(trip, m, p, share, cap, old.get(m.id, share),
                                                         self.payments.emi_offers(cap)))

    def _approve(self, auth: Authorisation, via: str, emi_months: Optional[int]) -> Authorisation:
        assert via in INSTRUMENTS, f"unknown instrument {via}"
        if hasattr(self.payments, "approve"):                  # mock / simulator convenience
            self.payments.approve(auth, via, emi_months)
        return self.payments.refresh(auth)

    @staticmethod
    def _instrument_line(auth: Authorisation) -> str:
        s = INSTRUMENTS[auth.instrument]["label"]
        if auth.emi_months:
            s += f", {auth.emi_months}-month EMI at capture"
        if auth.expires_at and not auth.multi_debit:
            s += f", hold valid till {auth.expires_at:%a %d %b}"
        return s

    def member_approves(self, trip: Trip, member_id: str, via: str = "UPI_RESERVE",
                        emi_months: Optional[int] = None) -> Authorisation:
        """The member approved a block: in their UPI app (Reserve Pay or a one-time mandate) or on the card page
        (a pre-authorisation, with EMI at capture if they chose it). Their share block, or a top-up asked of them."""
        m = trip.member(member_id)
        top = trip.top_ups.get(member_id)
        if top is not None and top.status == AuthStatus.PENDING:
            self._approve(top, via, emi_months)
            self._rail(trip, "payments", f"{m.first} approved top-up {top.rail_ref} ({self._instrument_line(top)}) — ₹{top.amount:,} blocked")
            self._complete_top_up(trip, m)
            return top
        assert trip.state == TripState.AUTHORISING, f"cannot approve in {trip.state}"
        auth = self._approve(trip.authorisations[member_id], via, emi_months)
        self._rail(trip, "payments", f"{m.first} approved: {auth.rail_ref} ACTIVE on {self._instrument_line(auth)} — "
                                     f"₹{auth.amount:,} blocked ({len(trip.blocked())}/{len(trip.in_members())} in)")
        self._say(trip, f"dm:{m.id}", M.dm_authorise_ack(m, auth), priority="P2")
        self._maybe_book(trip)
        return auth

    def member_withdraws(self, trip: Trip, member_id: str, text: Optional[str] = None) -> None:
        """The member asks to be let out after blocking. A UPI mandate or a card hold cannot be revoked from their
        own app (the OTM doc is explicit); they tell Quorum, Quorum releases the block. Treated like not authorising."""
        assert trip.state == TripState.AUTHORISING
        m = trip.member(member_id)
        self._say(trip, f"dm:{m.id}", text or "Count me out, please release my block.", actor=m.name)
        self._drop(trip, [m], "you asked to be let out")

    def close_authorising(self, trip: Trip) -> None:
        if trip.state != TripState.AUTHORISING:
            return
        for a in trip.authorisations.values():
            self.payments.refresh(a)
        late = [m for m in trip.in_members() if trip.authorisations[m.id].status != AuthStatus.BLOCKED]
        if late:
            self._drop(trip, late, "the deadline passed without your approval")
        else:
            self._maybe_book(trip)

    def _drop(self, trip: Trip, who: list[Member], why: str) -> None:
        """Yes-voters who did not authorise drop out; the plan is re-priced for the rest."""
        p = trip.plan()
        old = {t: p.per_head(t) for t in p.travellers}
        for m in who:
            a = trip.authorisations.get(m.id)
            if a and a.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
                self.payments.release(a)
                self._rail(trip, "payments", f"Mandate {a.rail_ref} released — {m.first} is out, nothing charged")
            trip.dropped.append(m.id)
            self._say(trip, f"dm:{m.id}", M.dm_dropped(trip, m, why))
        ins = trip.in_members()
        if not ins:
            return self.lapse(trip, M.why_nobody_left(trip))
        trip.set_budget([m.id for m in ins])
        changes = self._retarget(trip, p, [m.id for m in ins])
        self._rail(trip, "logistics", f"Re-priced for {len(ins)}: ₹{p.total():,} against a budget of {len(ins)} × "
                                      f"₹{trip.budget_floor:,} = ₹{trip.total_budget:,}" + (f" ({'; '.join(changes)})" if changes else ""))
        over = [m for m in ins if p.per_head(m.id) > trip.authorisations[m.id].amount]
        if over:
            return self._back_to_group(trip, ins, M.why_reprice_over(trip, who, over))
        if p.total() > trip.total_budget:
            return self._back_to_group(trip, ins, M.why_over_budget(trip, p))
        for m in ins:
            if p.per_head(m.id) != old[m.id]:
                self._say(trip, f"dm:{m.id}", M.dm_repriced(trip, m, old[m.id], p.per_head(m.id), who))
        self._maybe_book(trip)

    def _back_to_group(self, trip: Trip, travellers: list[Member], why: str) -> None:
        """A re-price went past what people authorised: release every block and put a revised plan to a vote."""
        for a in trip.authorisations.values():
            if a.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
                self.payments.release(a)
                self._rail(trip, "payments", f"Mandate {a.rail_ref} released — plan going back to the group")
        trip.authorisations.clear()
        trip.dropped.clear()
        self._say(trip, "group", M.group_back_to_group(trip, why))
        self.revise(trip, travellers, hint="over budget")

    def _retarget(self, trip: Trip, p: Plan, travellers: list[str]) -> list[str]:
        """Re-size the plan for a different set of people: their legs go, and every shared item is re-sized for
        the new headcount (rooms today; the same hook is where a car or a group activity would re-size).
        Returns one line per thing that changed."""
        before = len(p.travellers)
        p.travellers = travellers
        p.legs = [l for l in p.legs if l.member_id in travellers]
        changes = []
        for s in p.stays:
            if s.rooms_for(before) != s.rooms_for(len(travellers)):
                changes.append(f"{s.rooms_for(len(travellers))} twin rooms at {s.name} instead of {s.rooms_for(before)}")
        return changes

    def _maybe_book(self, trip: Trip) -> None:
        if trip.state != TripState.AUTHORISING:
            return
        ins = trip.in_members()
        if ins and all(trip.authorisations[m.id].status == AuthStatus.BLOCKED for m in ins):
            self.book(trip)

    # ------------------------------------------------------------------ 5. book: re-price, debit, ticket
    def _cap(self, trip: Trip, member_id: str) -> int:
        a = trip.authorisations[member_id]
        t = trip.top_ups.get(member_id)
        return a.amount + (t.amount if t and t.status in (AuthStatus.BLOCKED, AuthStatus.CAPTURED) else 0)

    def _headroom(self, trip: Trip, member_id: str) -> int:
        """What can still be debited without a new approval. UPI Reserve Pay keeps its headroom after the share
        is taken; a card hold or a one-time mandate has none once captured."""
        a = trip.authorisations[member_id]
        t = trip.top_ups.get(member_id)
        return a.headroom() + (t.headroom() if t else 0)

    def _present(self, trip: Trip, member_id: str, amount: int) -> tuple[int, int]:
        """Debit `amount`: the share block first, then the top-up block if there is one."""
        a = trip.authorisations[member_id]
        t = trip.top_ups.get(member_id)
        first = min(amount, a.headroom())
        if first > 0:
            self.payments.capture(a, first)
        rest = amount - first
        if rest > 0:
            assert t and t.headroom() >= rest, "debit above the authorised cap"
            self.payments.capture(t, rest)
        return first, rest

    def book(self, trip: Trip) -> None:
        p = trip.plan()
        ins = trip.in_members()
        trip.state = TripState.BOOKING
        # a. phone-only holds may have expired while the group voted and authorised: call to re-hold
        for s in p.stays:
            if s.phone_only and s.hold_until and s.hold_until < clock.now():
                self._sys(trip, f"Hold at {s.name} expired {s.hold_until:%a %d %b %H:%M}; calling to re-hold.")
                if not self._supplier_call(trip, s, len(ins), p.start, p.nights()):
                    return self._back_to_group(trip, ins, M.why_hold_lost(s))
        # b. re-price at live fares: inside the cap it is absorbed; beyond it, only that member is asked to top up
        short = self._reprice_live(trip, p, ins)
        if short:
            for mid, need in short.items():
                self._ask_top_up(trip, trip.member(mid), need, kind="TOP_UP", why="book your legs at today's fare")
            return                                                  # BOOKING until the affected members approve
        # c. debit every share; one failure rolls the others back
        if not self._capture_all(trip, p, ins):
            return
        # d. inventory
        self._book_inventory(trip, p, ins)

    def _reprice_live(self, trip: Trip, p: Plan, ins: list[Member]) -> dict[str, int]:
        short: dict[str, int] = {}
        for m in ins:
            for l in p.legs_for(m.id):
                live = self.logistics.search_legs(l.origin, l.destination, l.depart.date())
                same = next((o for o in live if o.carrier == l.carrier and o.depart == l.depart), None)
                if same and same.price != l.price:
                    l.price = same.price
            moved = sum(l.price - (l.quoted or l.price) for l in p.legs_for(m.id))
            share, cap = p.per_head(m.id), self._cap(trip, m.id)
            if moved:
                self._rail(trip, "logistics", f"Live fares for {m.first}: {'+' if moved > 0 else ''}₹{moved:,} vs the vote; "
                                              f"share ₹{share:,} is {'inside' if share <= cap else 'over'} the ₹{cap:,} authorised")
            if share > cap:
                short[m.id] = share - cap
        return short

    def _ask_top_up(self, trip: Trip, m: Member, need: int, kind: str, why: str, leg_id: Optional[str] = None) -> None:
        cap = ceil100(need)
        top = self.payments.create_block(m, cap, 1, reference=trip.plan().id)
        top.purpose = "TOP_UP"
        trip.top_ups[m.id] = top
        if kind == "TOP_UP":
            trip.pending[m.id] = Decision(member_id=m.id, kind="TOP_UP", leg_id=leg_id, shortfall=need, asked_at=clock.now())
        self._rail(trip, "payments", f"Top-up block {top.rail_ref} for {m.first}: ₹{cap:,} — PENDING")
        self._say(trip, f"dm:{m.id}", M.dm_topup_request(trip, m, cap, why, trip.authorisations[m.id].instrument))

    def _complete_top_up(self, trip: Trip, m: Member) -> None:
        d = trip.pending.get(m.id)
        if d is None:
            return
        if d.kind == "REBOOK":
            p = trip.plan()
            leg = next(l for l in p.legs if l.id == d.leg_id)
            del trip.pending[m.id]
            self._rebook(trip, m, leg, d.options[d.choice - 1])
        else:
            del trip.pending[m.id]
            if trip.state == TripState.BOOKING and not any(x.kind == "TOP_UP" for x in trip.pending.values()):
                self.book(trip)

    def _capture_all(self, trip: Trip, p: Plan, ins: list[Member]) -> bool:
        """N presentations, one per mandate. Today's approximation of the atomic group capture we are asking
        Pine Labs for: on any failure, refund everyone already debited and stop."""
        done: list[Member] = []
        for m in ins:
            share = p.per_head(m.id)
            try:
                first, rest = self._present(trip, m.id, share)
            except CaptureFailed as e:
                self._rail(trip, "payments", f"Presentation for {m.first} on {trip.authorisations[m.id].rail_ref} FAILED: {e}")
                for c in done:
                    for a in (trip.authorisations[c.id], trip.top_ups.get(c.id)):
                        if a and a.captured_amount:
                            amt = a.captured_amount
                            self.payments.refund(a)
                            self._rail(trip, "payments", f"Refunded ₹{amt:,} to {c.first} ({a.rail_ref})")
                for o in ins:
                    for a in (trip.authorisations[o.id], trip.top_ups.get(o.id)):
                        if a and a.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
                            self.payments.release(a)
                trip.state = TripState.LAPSED
                self._say(trip, "group", M.group_capture_failed(trip, m, len(done)))
                self._say(trip, f"dm:{trip.organiser_id}", M.dm_capture_failed_organiser(trip, m, str(e)))
                return False
            done.append(m)
            a = trip.authorisations[m.id]
            line = f"Debited ₹{first:,} on {a.rail_ref} ({INSTRUMENTS[a.instrument]['label']})"
            line += f" + ₹{rest:,} on top-up {trip.top_ups[m.id].rail_ref}" if rest else ""
            if a.multi_debit:
                line += f"; ₹{self._headroom(trip, m.id):,} headroom stays live for a re-booking"
            else:
                line += f"; the remaining ₹{a.amount - first:,} of the hold is released — one capture per {INSTRUMENTS[a.instrument]['label']}"
            if a.emi_months:
                line += f"; {m.first} pays the issuer in {a.emi_months} instalments, Quorum is settled in full"
            self._rail(trip, "payments", line)
        return True

    def _pay(self, trip: Trip, supplier: str, amount: int, purpose: str, reference: str) -> None:
        """Quorum pays a supplier from the pool. Never more than is in it: Quorum does not front either."""
        pay = self.payments.pay_supplier(supplier, amount, purpose, reference)
        trip.payouts.append(pay)
        self._rail(trip, "payments", f"Paid {supplier} ₹{amount:,} from the pool ({pay.method.lower().replace('_', ' ')}, "
                                     f"{reference}) — pool now ₹{self.payments.pool():,}")

    def _book_inventory(self, trip: Trip, p: Plan, ins: list[Member]) -> None:
        self._rail(trip, "payments", f"Pool (Quorum's account) holds ₹{self.payments.pool():,} for this trip; paying suppliers")
        for s in p.stays:
            self.logistics.book_stay(s, ins)
            self._rail(trip, "logistics", f"Stay confirmed {s.booking_ref}: {s.name}, {s.rooms_for(len(ins))} twin rooms × "
                                          f"{s.nights} nights for {len(ins)}" + (" — phone hold converted" if s.phone_only else ""))
            self._pay(trip, s.name, p.stay_total(s), "STAY", s.booking_ref)
        for m in ins:
            for l in p.legs_for(m.id):
                self.logistics.book_leg(l, m)
                self._pay(trip, l.carrier, l.price, "LEG", l.pnr)
            self._rail(trip, "logistics", f"Ticketed {m.first}: " + ", ".join(
                f"{l.carrier} {l.origin}→{l.destination} {l.pnr}" for l in p.legs_for(m.id)))
            self._say(trip, f"dm:{m.id}", M.dm_confirmation(trip, m, p))
        left = self.payments.pool()
        if left:
            self._rail(trip, "payments", f"₹{left:,} of rounding stays in the pool for this trip")
        trip.state = TripState.BOOKED
        self._say(trip, "group", M.booked_group(trip, p))

    def rerun(self, trip: Trip) -> None:
        """After a rolled-back capture: re-issue the UPI requests to everyone who is in."""
        assert trip.state == TripState.LAPSED and trip.in_members(), "nothing to re-run"
        trip.top_ups.clear()
        trip.pending.clear()
        trip.auth_deadline = clock.now() + timedelta(hours=AUTH_WINDOW_H)
        self.open_authorising(trip, f"Re-running the authorisation for {', '.join(m.first for m in trip.in_members())}.")

    def lapse(self, trip: Trip, why: str) -> None:
        for a in list(trip.authorisations.values()) + list(trip.top_ups.values()):
            if a.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
                self.payments.release(a)
                self._rail(trip, "payments", f"Mandate {a.rail_ref} released — nothing charged")
        trip.state = TripState.LAPSED
        self._say(trip, "group", M.lapsed_group(trip, why))

    def close(self, trip: Trip) -> None:
        for a in list(trip.authorisations.values()) + list(trip.top_ups.values()):
            if a.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
                self.payments.release(a)
        trip.state = TripState.CLOSED
        self._say(trip, "group", M.closed_group(trip))

    # ------------------------------------------------------------------ 6. after booking: disruptions
    def disrupt(self, trip: Trip, leg_id: str) -> None:
        """A carrier cancels one leg. Re-book inside the member's authorised cap; else ask that member only."""
        assert trip.state == TripState.BOOKED
        p = trip.plan()
        leg = next(l for l in p.legs if l.id == leg_id)
        m = trip.member(leg.member_id)
        self.logistics.cancel_leg(leg)
        self._rail(trip, "logistics", f"Carrier cancelled {M.leg_desc(leg)} PNR {leg.pnr}")
        self._rail(trip, "payments", f"{leg.carrier} refunds ₹{leg.price:,} for {leg.pnr} into the pool — "
                                     f"pool now ₹{self.payments.receive_refund(leg.carrier, leg.price, leg.pnr):,}")
        options = sorted([o for o in self.logistics.search_legs(leg.origin, leg.destination, leg.depart.date())
                          if o.carrier != leg.carrier or o.depart != leg.depart], key=lambda o: o.price)
        budget = self._headroom(trip, m.id) + leg.price          # unused cap + the cancelled fare coming back
        affordable = [o for o in options if o.price <= budget]
        if affordable:
            new = self._pick_leg(affordable, trip.occasion, outbound=(leg.destination == trip.destination))
            self._rebook(trip, m, leg, new)
            return
        top2 = options[:2]
        trip.pending[m.id] = Decision(member_id=m.id, kind="REBOOK", leg_id=leg.id, options=top2,
                                      shortfall=top2[0].price - budget, asked_at=clock.now())
        self._say(trip, f"dm:{m.id}", M.dm_disruption_options(trip, m, leg, top2, budget, ESCALATE_AFTER_MIN,
                                                               trip.authorisations[m.id]))
        self._say(trip, "group", M.disruption_escalate(m))

    def member_chooses(self, trip: Trip, member_id: str, option: int, via: str = "dm") -> None:
        """The member picked a re-booking option, by text or on the escalation call."""
        d = trip.pending[member_id]
        assert d.kind == "REBOOK"
        m = trip.member(member_id)
        p = trip.plan()
        leg = next(l for l in p.legs if l.id == d.leg_id)
        new = d.options[option - 1]
        d.choice = option
        if via == "dm":
            self._say(trip, f"dm:{m.id}", str(option), actor=m.name)
        need = new.price - (self._headroom(trip, m.id) + leg.price)
        if need <= 0:
            del trip.pending[m.id]
            self._rebook(trip, m, leg, new)
            return
        self._ask_top_up(trip, m, need, kind="REBOOK", why=f"re-book you on {new.carrier} {new.depart:%H:%M}", leg_id=leg.id)

    def _rebook(self, trip: Trip, m: Member, leg: Leg, new: Leg) -> None:
        p = trip.plan()
        new = new.model_copy(update={"member_id": m.id, "quoted": leg.price})
        self.logistics.book_leg(new, m)
        new.status = "REBOOKED"
        p.legs = [new if l.id == leg.id else l for l in p.legs]
        extra = new.price - leg.price
        if extra > 0:
            first, rest = self._present(trip, m.id, extra)
            funding = f"₹{leg.price:,} refund + ₹{first:,} headroom" + (f" + ₹{rest:,} top-up" if rest else "")
        elif extra < 0:
            funding = f"₹{leg.price:,} refund covers it; ₹{-extra:,} stays in the pool for {m.first}"
        else:
            funding = "same fare"
        self._rail(trip, "logistics", f"Re-booked {M.leg_desc(new)} PNR {new.pnr} at ₹{new.price:,} ({funding})")
        self._pay(trip, new.carrier, new.price, "REBOOK", new.pnr)
        late_stay: Optional[Stay] = None
        if leg.destination == trip.destination and (new.arrive.hour >= LATE_ARRIVAL_HOUR or new.arrive.date() > leg.arrive.date()):
            for s in p.stays:
                if s.phone_only:                                 # the room must not go to a walk-in
                    rec = self._call(trip, "LATE_ARRIVAL", lambda: self.voice.notify_late_arrival(s, m, new.arrive),
                                     f"Called {s.name} ({s.phone}): {m.first} now arrives {new.arrive:%H:%M}")
                    s.calls.append(rec)
                    late_stay = s
        self._say(trip, "group", M.disruption_group(m, leg, new, late_stay))
        self._say(trip, f"dm:{m.id}", M.dm_rebooked(m, leg, new, extra))

    def _escalate(self, trip: Trip, d: Decision) -> None:
        """Voice job 2: the text went unanswered and the decision cannot wait."""
        d.escalated = True
        m = trip.member(d.member_id)
        leg = next(l for l in trip.plan().legs if l.id == d.leg_id)
        rec = self._call(trip, "DISRUPTION_UNANSWERED",
                         lambda: self.voice.escalate_member(m, M.call_escalation_question(leg),
                                                            [f"{o.carrier} {o.depart:%H:%M}, {M.fmt_inr(o.price)}" for o in d.options]),
                         f"Called {m.first} ({m.phone}) after {ESCALATE_AFTER_MIN} min without a reply")
        if rec.choice:
            self._rail(trip, "voice", f"  {m.first} chose option {rec.choice}")
        if rec.disposition == "CONFIRMED" and rec.choice:
            self._say(trip, f"dm:{m.id}", f"(on the call) option {rec.choice}", actor=m.name)
            self.member_chooses(trip, m.id, rec.choice, via="call")
        else:
            self._say(trip, f"dm:{m.id}", M.dm_escalation_failed(m))

    def member_exits(self, trip: Trip, member_id: str) -> None:
        """A member drops out after booking. Their legs stay theirs; the stay split is shown to the rest."""
        assert trip.state == TripState.BOOKED
        p = trip.plan()
        m = trip.member(member_id)
        if member_id not in p.travellers:
            return
        old = {t: p.per_head(t) for t in p.travellers}
        self._retarget(trip, p, [t for t in p.travellers if t != member_id])
        trip.dropped.append(member_id)
        self._say(trip, f"dm:{m.id}", M.dm_exit_ack(trip, m, p))
        self._say(trip, "group", M.group_exit(trip, m, old, p))

    # ------------------------------------------------------------------ clock
    def tick(self, trip: Trip) -> None:
        """Advance the trip against the clock. Call after every clock.advance()."""
        now = clock.now()
        if trip.state == TripState.GATHERING and trip.gather_deadline and now >= trip.gather_deadline:
            self.close_gathering(trip)
        if trip.state == TripState.VOTING and trip.vote_deadline:
            if now >= trip.vote_deadline:
                self.close_vote(trip)
            elif now >= trip.vote_deadline - timedelta(hours=VOTE_WINDOW_H / 2):
                for t in trip.plan().travellers:
                    if t not in trip.votes and t not in trip.reminded:      # one reminder text, never a call
                        trip.reminded.append(t)
                        self._say(trip, f"dm:{t}", M.dm_vote_reminder(trip.member(t), trip.vote_deadline))
        if trip.state == TripState.AUTHORISING and trip.auth_deadline:
            if now >= trip.auth_deadline:
                self.close_authorising(trip)
            elif now >= trip.auth_deadline - timedelta(hours=AUTH_WINDOW_H / 2):
                for m in trip.in_members():                                   # one reminder text, never a call
                    a = trip.authorisations.get(m.id)
                    if a and a.status == AuthStatus.PENDING and m.id not in trip.reminded:
                        trip.reminded.append(m.id)
                        self._say(trip, f"dm:{m.id}", M.dm_authorise_reminder(m, a, trip.auth_deadline))
        for d in list(trip.pending.values()):
            if (d.kind == "REBOOK" and d.choice is None and not d.escalated
                    and now >= d.asked_at + timedelta(minutes=ESCALATE_AFTER_MIN)):
                self._escalate(trip, d)
