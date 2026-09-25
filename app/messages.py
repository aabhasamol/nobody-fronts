"""Everything the agent says, in one place.

House rules: short sentences, one ask per message, always a default and a deadline, never a group poll.
The group chat gets four kinds of post: kickoff, the tally, the booking, and any disruption. Everything
else is a private DM.
"""
from __future__ import annotations
from datetime import datetime
from .models import Trip, Member, Plan, Leg, Stay, CallRecord, Authorisation, INSTRUMENTS


def fmt_dt(d: datetime) -> str:
    return d.strftime("%a %d %b, %H:%M")


def fmt_inr(n: int) -> str:
    return f"₹{n:,}"


def fmt_pct(x: float) -> str:
    return f"{int(round(x * 100))}%"


def leg_desc(l: Leg) -> str:
    return f"{l.carrier} {l.origin}→{l.destination} {l.depart:%d %b %H:%M}"


def cap_for(trip: Trip, share: int) -> int:
    import math
    return int(math.ceil(share * (1 + trip.overshoot) / 100.0) * 100)


# ------------------------------------------------------------------ kickoff + gathering
def hello_group(trip: Trip) -> str:
    org = trip.organiser()
    return (f"Hi all — {org.first} added me to plan {trip.name}: {trip.destination}, "
            f"{trip.start:%d}–{trip.end:%d %b}, a {trip.occasion} trip, around {fmt_inr(trip.budget)} a head all-in, "
            f"at most {fmt_pct(trip.overshoot)} over. I'll DM each of you for where you're travelling from and your own "
            f"ceiling, then send you one plan with your own cost to vote on privately. Only the tally comes back here. "
            f"Whoever says yes sets the budget; nothing is booked and no money moves until everyone who's in has "
            f"authorised their own share.")


def dm_gather(trip: Trip, m: Member, deadline: datetime) -> str:
    return (f"Hi {m.first} — {trip.name}, {trip.start:%d}–{trip.end:%d %b}. Five quick things, just to me:\n"
            f"1. Which city do you start from? (I have {m.home_city})\n"
            f"2. Which city do you go back to? (same, unless you say otherwise)\n"
            f"3. Any dates in that window you can't do?\n"
            f"4. The most you'd pay for the whole trip, per head? ({trip.organiser().first} said around {fmt_inr(trip.budget)})\n"
            f"5. Anything non-negotiable — no hostels, direct flights, veg food?\n"
            f"Reply by {fmt_dt(deadline)}. If I don't hear back I'll plan {m.home_city} → {trip.destination} → "
            f"{m.home_city} on those dates with {fmt_inr(trip.budget)} as your ceiling.")


def dm_gather_ack(trip: Trip, m: Member, start_city: str, return_city: str, available: bool, ceiling: int) -> str:
    if not available:
        return f"Noted, {m.first} — you're out for these dates. I'll plan for the others."
    return (f"Got it, {m.first}: {start_city} → {trip.destination} → {return_city}, up to {fmt_inr(ceiling)}. You'll get "
            f"the plan with your own cost to vote on here, not in the group.")


# ------------------------------------------------------------------ planning
def dm_no_fit(trip: Trip, p: Plan | None, over: list[Member]) -> str:
    org = trip.organiser()
    if p is None:
        return (f"{org.first}, no stay in {trip.destination} meets the must-haves for those dates. "
                f"Say 'budget 22000', 'overshoot 20' or 'end {trip.end:%d %b}' with a new date and I'll re-plan.")
    heads = sorted(p.per_head(t) for t in p.travellers)
    driver = max((trip.member(t) for t in p.travellers), key=lambda m: p.travel(m.id))
    return (f"{org.first}, nothing fits {fmt_inr(trip.limit())} a head ({fmt_inr(trip.budget)} + "
            f"{fmt_pct(trip.overshoot)}). The cheapest plan that meets the must-haves is {p.stays[0].name} at "
            f"{fmt_inr(heads[0])}–{fmt_inr(heads[-1])} a head; {len(over)} of {len(p.travellers)} would be over. "
            f"What drives it: flights from {trip.constraints[driver.id].start_city} at "
            f"{fmt_inr(p.travel(driver.id))} return. Reply 'budget 22000', 'overshoot 20', or 'end {trip.end:%d %b}' "
            f"with a new date and I'll re-plan. I won't quietly go over.")


def dm_leg_over_limit(trip: Trip, m: Member, p: Plan) -> str:
    c = trip.constraints[m.id]
    return (f"{m.first}, a heads-up before the vote: your flights from {c.start_city} come to "
            f"{fmt_inr(p.travel(m.id))}, which puts your all-in at {fmt_inr(p.per_head(m.id))} — over the "
            f"{fmt_inr(trip.limit())} the plan is built to. Everyone else's share stays theirs; yours isn't averaged "
            f"in. Vote no if that doesn't work and say what would — a train, or other dates.")


# ------------------------------------------------------------------ voting
def dm_vote(trip: Trip, m: Member, p: Plan, deadline: datetime) -> str:
    c = trip.constraints[m.id]
    n = len(p.travellers)
    title = f"plan v{p.version}" + (f" — {p.note}" if p.note else "")
    lines = [f"{m.first}, here's the {title} for {trip.name}. Your cost, from {c.start_city}:"]
    for l in p.legs_for(m.id):
        lines.append(f" • {l.depart:%a %d %b %H:%M} {l.carrier} {l.origin}→{l.destination}, lands {l.arrive:%H:%M} — {fmt_inr(l.price)}")
    for s in p.stays:
        how = "confirmed by phone, held till " + fmt_dt(s.hold_until) if s.phone_only and s.hold_until else "listed"
        lines.append(f" • {s.name}, {s.nights} nights, twin sharing among {n} ({how}) — {fmt_inr(s.per_head(n))} your share")
    share = p.per_head(m.id)
    ceiling = trip.ceiling(m.id)
    vs = "inside" if share <= ceiling else "over"
    lines.append(f"Your all-in: {fmt_inr(share)}, {vs} the {fmt_inr(ceiling)} you gave me. If it goes ahead you'd "
                 f"authorise up to {fmt_inr(cap_for(trip, share))} — your share plus {fmt_pct(trip.overshoot)} so I can "
                 f"re-book you if a fare moves or a flight cancels.")
    lines.append(f"Reply yes or no by {fmt_dt(deadline)}. If no, say what would make it a yes. Only the tally goes to "
                 f"the group. Whoever says yes sets the budget: your number × the lowest ceiling among you, and I "
                 f"re-size the plan for exactly who's in.")
    return "\n".join(lines)


def dm_vote_ack(m: Member, yes: bool) -> str:
    return "Thanks — counted." if yes else "Noted — if the plan needs a revision, that's what I'll build from."


def dm_vote_reminder(m: Member, deadline: datetime) -> str:
    return (f"{m.first}, a nudge — yes or no on the plan by {fmt_dt(deadline)}? If I don't hear back I'll count "
            f"you as not in this time.")


def group_tally(trip: Trip, yes: int, no: int, silent: int, passed: bool, revising: bool = False) -> str:
    p = trip.plan()
    line = f"Vote on plan v{p.version}: {yes} yes, {no} no" + (f", {silent} didn't reply" if silent else "")
    line += f" — {'passes' if passed else 'does not pass'} ({trip.majority()} of {len(trip.members)} needed)."
    if passed:
        pass                                                  # group_go carries the rest
    elif revising:
        line += " Revising from what you told me privately; the new plan is in your DMs."
    else:
        line += f" That was the last revision — {trip.organiser().first}, I've DM'd you the options."
    return line


def group_go(trip: Trip, intro: str, changes: list[str]) -> str:
    p = trip.plan()
    n = len(p.travellers)
    s = (f"{intro} Budget for the trip: {fmt_inr(trip.total_budget)} ({n} × {fmt_inr(trip.budget_floor)}, the lowest "
         f"ceiling among the {n}). Plan re-sized for {n}" + (f": {'; '.join(changes)}" if changes else "") +
         f" — comes to {fmt_inr(p.total())}, inside it. I've DM'd each of the {n} a UPI request for their own share. "
         f"Approve it and the money is blocked, not charged. Once everyone who's in has approved, by "
         f"{fmt_dt(trip.auth_deadline)}, I book everything and pay the suppliers from Quorum's account. Anyone who "
         f"doesn't approve by then is simply not on the trip.")
    return s


def why_over_budget(trip: Trip, p: Plan) -> str:
    n = len(p.travellers)
    return (f"For the {n} who are in, the plan comes to {fmt_inr(p.total())} against a budget of {fmt_inr(trip.total_budget)} "
            f"({n} × {fmt_inr(trip.budget_floor)}, the lowest ceiling among them).")


def dm_organiser_decides(trip: Trip) -> str:
    yes = [trip.member(i).first for i in trip.yes_voters()]
    return (f"Plan v{trip.plan().version} didn't pass and I've used both revisions. {len(yes)} said yes: "
            f"{', '.join(yes) or 'nobody'}. Say 'go' to book for just them — each authorises their own share — or "
            f"'close' to drop the trip. Nobody has been charged.")


def group_organiser_go(trip: Trip) -> str:
    ins = [m.first for m in trip.in_members()]
    return f"{trip.organiser().first} says go ahead for those who said yes: {', '.join(ins)}."


def group_back_to_group(trip: Trip, why: str) -> str:
    return f"{why} Every block is released. A revised plan is in your DMs for a fresh yes/no."


# ------------------------------------------------------------------ authorising
def dm_authorise(trip: Trip, m: Member, p: Plan, share: int, cap: int, old_share: int,
                 emi: list[tuple[int, int]]) -> str:
    n = len(p.travellers)
    moved = ""
    if old_share != share:
        moved = (f" (it was {fmt_inr(old_share)} when the plan went to the vote; {n} are in, so the rooms split "
                 f"differently)")
    tenures = ", ".join(f"{fmt_inr(monthly)} × {months}" for months, monthly in emi)
    return (f"{m.first}, your share of the plan is {fmt_inr(share)}{moved}, inside the {fmt_inr(trip.ceiling(m.id))} "
            f"you gave me. Block {fmt_inr(cap)} — your share plus the {fmt_pct(trip.overshoot)} headroom "
            f"{trip.organiser().first} allowed — either way:\n"
            f" • UPI: approve the request I've sent. It's blocked in your account, not charged, and the headroom stays "
            f"live so I can re-book you if a flight cancels, no new tap.\n"
            f" • Credit card: tap the card link to hold it. One capture at booking; EMI if you like ({tenures} months). "
            f"A card hold is used up at capture, so a re-booking beyond the airline's refund would need one more tap.\n"
            f"Nothing is charged now. Only what you actually owe is debited, into Quorum's account from which I pay "
            f"the airline and the stay, never more than {fmt_inr(cap)}, and only once everyone who's in has approved "
            f"by {fmt_dt(trip.auth_deadline)}. Otherwise the block is released.")


def dm_authorise_reminder(m: Member, a: Authorisation, deadline: datetime) -> str:
    return (f"{m.first}, a nudge — the UPI request for {fmt_inr(a.amount)} is still waiting in your app. Approve by "
            f"{fmt_dt(deadline)} or I'll take you off the plan; nothing is charged either way.")


def dm_authorise_ack(m: Member, a: Authorisation) -> str:
    how = INSTRUMENTS[a.instrument]["label"] + (f", {a.emi_months}-month EMI when it's captured" if a.emi_months else "")
    return f"Blocked {fmt_inr(a.amount)} on your {how}, {m.first} — nothing charged. I'll book the moment the others have approved."


def dm_dropped(trip: Trip, m: Member, why: str) -> str:
    return (f"{m.first}, {why}, so I've taken you off this plan. Nothing was charged. Tell "
            f"{trip.organiser().first} if that's wrong.")


def dm_repriced(trip: Trip, m: Member, old: int, new: int, who: list[Member]) -> str:
    names = ", ".join(w.first for w in who)
    n = len(trip.plan().travellers)
    direction = "down" if new < old else "up"
    return (f"{m.first}, {names} dropped out, so the rooms now split among {n}: your share is {fmt_inr(new)}, "
            f"{direction} from {fmt_inr(old)} and still inside the {fmt_inr(trip.authorisations[m.id].amount)} you "
            f"authorised. Nothing else changes.")


def why_reprice_over(trip: Trip, who: list[Member], over: list[Member]) -> str:
    return (f"{', '.join(w.first for w in who)} dropped out and the re-price for {len(trip.in_members())} pushes "
            f"{', '.join(o.first for o in over)} past what they authorised.")


def why_nobody_left(trip: Trip) -> str:
    return "Nobody who said yes authorised by the deadline."


def why_hold_lost(s: Stay) -> str:
    return f"{s.name} let the hold go and has no rooms left."


# ------------------------------------------------------------------ booking
def dm_topup_request(trip: Trip, m: Member, cap: int, why: str, instrument: str | None) -> str:
    ask = ("tap the card link to authorise" if instrument == "CARD_PREAUTH" else "approve the UPI request for")
    return (f"{m.first}, {ask} {fmt_inr(cap)} and I'll {why} right away. Nobody else is being asked for anything.")


def dm_confirmation(trip: Trip, m: Member, p: Plan) -> str:
    lines = [f"Your confirmations for {trip.name}:"]
    for l in p.legs_for(m.id):
        diff = ""
        if l.quoted is not None and l.quoted != l.price:
            d = l.price - l.quoted
            diff = f" ({'+' if d > 0 else ''}{fmt_inr(d)} vs the fare at the vote)"
        lines.append(f" • {leg_desc(l)} — PNR {l.pnr} — {fmt_inr(l.price)}{diff}")
    n = len(p.travellers)
    for s in p.stays:
        lines.append(f" • {s.name}, {s.nights} nights, twin sharing — ref {s.booking_ref} — {fmt_inr(s.per_head(n))}")
    a = trip.authorisations[m.id]
    charged = a.captured_amount + (trip.top_ups[m.id].captured_amount if m.id in trip.top_ups else 0)
    how = INSTRUMENTS[a.instrument]["label"]
    tail = (f"; your issuer collects it as {a.emi_months} monthly instalments" if a.emi_months else
            "; the rest of the block is released when the trip ends" if a.multi_debit else
            "; the rest of the hold was released at capture")
    lines.append(f"Charged: {fmt_inr(charged)} on your {how} into Quorum's account, paid on to the suppliers above{tail}.")
    return "\n".join(lines)


def booked_group(trip: Trip, p: Plan) -> str:
    who = ", ".join(trip.member(t).first for t in p.travellers)
    stays = "; ".join(f"{s.name} ({s.booking_ref})" for s in p.stays)
    return (f"Booked. {stays} for {who}, {p.start:%d}–{p.end:%d %b}. Flights ticketed from each city; PNRs are in "
            f"your DMs. Every supplier is paid from Quorum's account, {fmt_inr(sum(x.amount for x in trip.payouts))} in "
            f"all. {trip.organiser().first} didn't pay for anyone and doesn't need to chase anyone.")


def group_capture_failed(trip: Trip, m: Member, refunded: int) -> str:
    return (f"Stopping. {m.first}'s debit failed, so I've refunded the {refunded} that had gone through and booked "
            f"nothing. Nobody is out of pocket. {trip.organiser().first}, I've DM'd you what to do next.")


def dm_capture_failed_organiser(trip: Trip, m: Member, reason: str) -> str:
    return (f"{m.first}'s bank declined the presentation ({reason}). Everyone else has been refunded and the "
            f"holds stand for now. Ask {m.first} to check with their bank, then say 're-run' and I'll re-issue the "
            f"UPI requests, or 'close'. This all-or-nothing rollback is the thing we're asking Pine Labs to make "
            f"atomic.")


# ------------------------------------------------------------------ after booking
def disruption_group(m: Member, old: Leg, new: Leg, late_stay: Stay | None) -> str:
    s = (f"{m.first}'s {old.carrier} {old.depart:%H:%M} was cancelled by the carrier. Re-booked on "
         f"{new.carrier} {new.depart:%H:%M} within what they'd already authorised — no new approval needed. New "
         f"PNR sent.")
    if late_stay:
        s += f" I've called {late_stay.name} so the room is kept for a {new.arrive:%H:%M} arrival."
    return s


def dm_rebooked(m: Member, old: Leg, new: Leg, extra: int) -> str:
    money = (f"{fmt_inr(-extra)} of the cancelled fare stays in the pool for you and comes back after the trip." if extra < 0 else
             f"{fmt_inr(extra)} more than the cancelled fare, taken from the headroom you authorised." if extra > 0 else
             "Same fare.")
    return f"New PNR {new.pnr} — {leg_desc(new)}, lands {new.arrive:%H:%M}. {money}"


def dm_disruption_options(trip: Trip, m: Member, old: Leg, options: list[Leg], budget: int, minutes: int,
                          auth: Authorisation) -> str:
    why = (f"Your card hold was used up at booking, so beyond {old.carrier}'s {fmt_inr(old.price)} refund this needs one "
           f"more tap." if auth.instrument == "CARD_PREAUTH" else
           f"Every alternative is over what you authorised, even with {old.carrier}'s {fmt_inr(old.price)} refund.")
    lines = [f"{m.first}, {old.carrier} cancelled your {old.origin}→{old.destination} {old.depart:%d %b %H:%M}. {why} Options:"]
    for i, o in enumerate(options, 1):
        lines.append(f" {i}. {o.carrier} {o.depart:%H:%M}, lands {o.arrive:%H:%M} — {fmt_inr(o.price)} "
                     f"(needs {fmt_inr(o.price - budget)} more)")
    lines.append(f"Reply 1 or 2 within {minutes} minutes; if I can't reach you here I'll call. The rest of the group "
                 f"is unaffected.")
    return "\n".join(lines)


def disruption_escalate(m: Member) -> str:
    return (f"{m.first}'s flight was cancelled and every alternative is over their authorised cap. I've DM'd them "
            f"two options; the rest of the trip is unaffected.")


def call_escalation_question(old: Leg) -> str:
    return (f"Your {old.carrier} {old.origin} to {old.destination} on {old.depart:%d %B} was cancelled and the "
            f"alternatives cost more than you authorised. Which do you want?")


def dm_escalation_failed(m: Member) -> str:
    return f"{m.first}, I tried calling too. I'll hold both options as long as the carrier lets me; reply 1 or 2 when you can."


def dm_exit_ack(trip: Trip, m: Member, p: Plan) -> str:
    return (f"Understood, {m.first}. Your flights stay in your name — the carrier's rules, not mine — and your "
            f"share of the stay isn't something I can refund. I've told the group what changes for them.")


def group_exit(trip: Trip, m: Member, old: dict[str, int], p: Plan) -> str:
    rest = [trip.member(t) for t in p.travellers]
    split = ", ".join(f"{r.first} {fmt_inr(old[r.id])}→{fmt_inr(p.per_head(r.id))}" for r in rest)
    return (f"{m.first} has dropped out after booking. Flights are theirs. The stay now splits among "
            f"{len(rest)}: {split}. I'm not moving anyone's money on this — {trip.organiser().first}, over to you.")


def lapsed_group(trip: Trip, why: str) -> str:
    return f"{why} Nothing was booked and no money moved — every block is released."


def closed_group(trip: Trip) -> str:
    return "Trip closed. Nobody was charged."
