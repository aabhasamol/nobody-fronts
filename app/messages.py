"""Everything the agent says, in one place.

House rules: short sentences, one ask per message, always a default and a deadline, never a group poll.
The group chat gets four kinds of post: kickoff, the tally, the booking, and any disruption. Everything
else is a private DM.
"""
from __future__ import annotations
from datetime import datetime
from .models import Trip, Member, Plan, Leg, Stay, CallRecord, Authorisation, INSTRUMENTS, inr
from .lore import INTERESTS


def fmt_dt(d: datetime) -> str:
    return d.strftime("%a %d %b, %H:%M")


def fmt_inr(n: int) -> str:
    return f"₹{inr(n)}"


def fmt_pct(x: float) -> str:
    return f"{int(round(x * 100))}%"


def leg_desc(l: Leg) -> str:
    return f"{l.carrier} {l.origin}→{l.destination} {l.depart:%d %b %H:%M}"


def cap_for(trip: Trip, share: int) -> int:
    import math
    return int(math.ceil(share * (1 + trip.overshoot) / 100.0) * 100)


# ------------------------------------------------------------------ kickoff + gathering
def hello_group(trip: Trip, hook: str | None = None) -> str:
    org = trip.organiser()
    return (f"Hi all — {org.first} added me to plan {trip.name}: {trip.destination}, "
            f"{trip.start:%d}–{trip.end:%d %b}, a {trip.occasion} trip, around {fmt_inr(trip.budget)} a head all-in, "
            f"at most {fmt_pct(trip.overshoot)} over." + (f" {hook}" if hook else "") +
            f" I'll DM each of you for where you're travelling from and your own ceiling, then send you one plan with "
            f"your own cost to vote on privately. Only the tally comes back here. Whoever says yes sets the budget; "
            f"nothing is booked and no money moves until everyone who's in has authorised their own share.")


def dm_gather(trip: Trip, m: Member, deadline: datetime) -> str:
    return (f"Hi {m.first} — {trip.name}, {trip.start:%d}–{trip.end:%d %b}. Six taps, just to me:\n"
            f"1. How many people am I booking for, you included?\n"
            f"2. Which city do you all start from? (I have {m.home_city})\n"
            f"3. Which city do you go back to? (same, unless you say otherwise)\n"
            f"4. Any dates in that window you can't do?\n"
            f"5. The most you'd pay for the whole trip, per head? ({trip.organiser().first} said around {fmt_inr(trip.budget)})\n"
            f"6. Pick what you're into: {', '.join(INTERESTS)}. Anything non-negotiable — no hostels, direct flights, veg?\n"
            f"Names and dates of birth only if you say yes to the plan. Reply by {fmt_dt(deadline)}. If I don't hear back "
            f"I'll plan just you, {m.home_city} → {trip.destination} → {m.home_city} on those dates, with "
            f"{fmt_inr(trip.budget)} as your ceiling.")


def party_desc(c) -> str:
    if c.party <= 1:
        return "just you"
    names = ", ".join(c.party_names) if c.party_names else f"{c.party} of you"
    return f"{c.party} people ({names})"


def dm_gather_ack(trip: Trip, m: Member, c) -> str:
    if not c.available:
        return f"Noted, {m.first} — you're out for these dates. I'll plan for the others."
    return (f"Got it, {m.first}: {party_desc(c)}, {c.start_city} → {trip.destination} → {c.return_city}, up to "
            f"{fmt_inr(c.budget or trip.budget)} a head. You'll get the plan with your own cost to vote on here, not in the group.")


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
def dm_vote(trip: Trip, m: Member, p: Plan, deadline: datetime, hook: str | None = None) -> str:
    c = trip.constraints[m.id]
    n, k = p.heads(), p.party(m.id)
    title = f"plan v{p.version}" + (f" — {p.note}" if p.note else "")
    who = f"for {party_desc(c)}" if k > 1 else "for you"
    lines = [f"{m.first}, here's the {title} for {trip.name}. Your cost {who}, from {c.start_city}:"]
    for l in p.legs_for(m.id):
        lines.append(f" • {l.depart:%a %d %b %H:%M} {l.carrier} {l.origin}→{l.destination}, lands {l.arrive:%H:%M} — "
                     f"{fmt_inr(l.price)} a seat")
    for s in p.stays:
        how = "confirmed by phone, held till " + fmt_dt(s.hold_until) if s.phone_only and s.hold_until else "listed"
        lines.append(f" • {s.name}, {s.nights} nights, twin sharing among {n} ({how}) — {fmt_inr(s.per_head(n))} a head")
    for a in p.activities:
        if a.prebook:
            lines.append(f" • {a.name} — {fmt_inr(a.price_per_head)} a head, booked with the trip")
    on_the_day = [a.name for a in p.activities if not a.prebook]
    if on_the_day:
        lines.append(f" • On the day, if you like: {'; '.join(on_the_day)}")
    if hook:
        lines.append(hook)
    per_head, share = p.per_head(m.id), p.share(m.id)
    ceiling = trip.ceiling(m.id)
    vs = "inside" if per_head <= ceiling else "over"
    each = f"{fmt_inr(per_head)} a head, {vs} the {fmt_inr(ceiling)} you gave me" + (f"; {fmt_inr(share)} for the {k} of you" if k > 1 else "")
    lines.append(f"Your all-in: {each}. If it goes ahead you'd authorise up to {fmt_inr(cap_for(trip, share))} — your "
                 f"share plus {fmt_pct(trip.overshoot)} so I can re-book you if a fare moves or a flight cancels.")
    lines.append(f"Reply yes or no by {fmt_dt(deadline)}. If no, say what would make it a yes. Only the tally goes to "
                 f"the group. Whoever says yes sets the budget: the heads you're paying for × the lowest ceiling among "
                 f"you, and I re-size the plan for exactly who's in.")
    return "\n".join(lines)


def dm_details_after_yes(trip: Trip, m: Member) -> str:
    c = trip.constraints[m.id]
    k = c.party
    fam = trip.persona() == "families"
    return (f"Great. For the tickets I need, for {'each of the ' + str(k) if k > 1 else 'you'}: full name as on ID and date of "
            f"birth. Also: veg / non-veg / Jain" + (", any medical needs I should plan around, and pets" if fam else "") +
            f". One message is fine; nothing is booked until everyone's in.")


def dm_details_ack(m: Member) -> str:
    return f"Noted, {m.first} — on the tickets exactly like that."


def dm_nudge_out(trip: Trip, m: Member, reason: str, per_head_if_in: int, hook: str | None, until: datetime) -> str:
    r = reason.lower()
    answer = ("With you in it's cheaper for everyone, you included" if any(w in r for w in ("expens", "cheap", "cost", "price", "budget", "₹"))
              else "If it was the dates, tell me which ones and I'll check the fares" if "date" in r
              else "If something in the plan put you off, say what and I'll see if it can change")
    return (f"{m.first}, the others are going. {answer}: your all-in would be {fmt_inr(per_head_if_in)} a head."
            + (f" {hook}" if hook else "") +
            f" Say yes by {fmt_dt(until)} and you're in on the same terms; after that the plan is booked for those who said yes.")


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
    n, h = len(p.travellers), p.heads()
    heads = f"{h} of you" if h == n else f"{n} paying for {h}"
    s = (f"{intro} Budget for the trip: {fmt_inr(trip.total_budget)} ({h} × {fmt_inr(trip.budget_floor)}, the lowest "
         f"ceiling among the {n} who are in). Plan re-sized for {heads}" + (f": {'; '.join(changes)}" if changes else "") +
         f" — comes to {fmt_inr(p.total())}, inside it. I've DM'd each of the {n} how to block their own share: a UPI "
         f"mandate, or a payment link. Nothing is charged until everyone who's in has approved, by "
         f"{fmt_dt(trip.auth_deadline)}; then I book everything and pay the suppliers from Quorum's account. Anyone "
         f"who doesn't approve by then is simply not on the trip.")
    return s


def why_over_budget(trip: Trip, p: Plan) -> str:
    h = p.heads()
    return (f"For the {h} who are in, the plan comes to {fmt_inr(p.total())} against a budget of {fmt_inr(trip.total_budget)} "
            f"({h} × {fmt_inr(trip.budget_floor)}, the lowest ceiling among them).")


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
    h, k = p.heads(), p.party(m.id)
    moved = ""
    if old_share != share:
        moved = (f" (it was {fmt_inr(old_share)} when the plan went to the vote; {h} are in, so the rooms split "
                 f"differently)")
    tenures = ", ".join(f"{fmt_inr(monthly)} × {months}" for months, monthly in emi)
    for_whom = f" for the {k} of you" if k > 1 else ""
    later = (f"\n • Pay later: LazyPay at booking, repay after the trip on their terms; Quorum is paid in full so nobody waits on you."
             if cap <= INSTRUMENTS["BNPL"]["max_amount"] else "")
    return (f"{m.first}, your share of the plan is {fmt_inr(share)}{for_whom}{moved}, {fmt_inr(p.per_head(m.id))} a head "
            f"against the {fmt_inr(trip.ceiling(m.id))} you gave me. Block {fmt_inr(cap)} — your share plus the "
            f"{fmt_pct(trip.overshoot)} headroom {trip.organiser().first} allowed — any of these:\n"
            f" • UPI mandate: approve the request I've sent. It's blocked in your account, not charged, and the headroom "
            f"stays live so I can re-book you if a flight cancels, no new tap.\n"
            f" • Payment link: pay with what you like. A credit card is only held — one capture at booking, EMI if you "
            f"want it ({tenures} months), and a re-booking beyond the airline's refund would need one more tap. Anything "
            f"that can't hold pays now, and that money sits in Quorum's account until booking, refunded in full if the "
            f"trip doesn't happen." + later + "\n"
            f"Only what you actually owe is kept, into Quorum's account from which I pay the airline and the stay, never "
            f"more than {fmt_inr(cap)}, and only once everyone who's in has approved by {fmt_dt(trip.auth_deadline)}. "
            f"Otherwise the block is released.")


def dm_authorise_reminder(m: Member, a: Authorisation, deadline: datetime, hook: str | None = None) -> str:
    return (f"{m.first}, a nudge — the UPI request and the payment link for {fmt_inr(a.amount)} are still waiting for "
            f"you. Use either by {fmt_dt(deadline)} or I'll take you off the plan; nothing is charged either way."
            + (f" {hook}" if hook else ""))


def dm_authorise_ack(m: Member, a: Authorisation) -> str:
    how = INSTRUMENTS[a.instrument]["label"] + (f", {a.emi_months}-month EMI when it's captured" if a.emi_months else "")
    if a.prepaid:
        return (f"Received {fmt_inr(a.amount)} via the {how}, {m.first}. It sits in Quorum's account until everyone who's in "
                f"has approved; then I book and keep only what you owe. If the trip doesn't happen it all comes back.")
    return f"Blocked {fmt_inr(a.amount)} on your {how}, {m.first} — nothing charged. I'll book the moment the others have approved."


def dm_dropped(trip: Trip, m: Member, why: str) -> str:
    return (f"{m.first}, {why}, so I've taken you off this plan. Nothing was charged. Tell "
            f"{trip.organiser().first} if that's wrong.")


def dm_repriced(trip: Trip, m: Member, old: int, new: int, who: list[Member], joined: bool = False) -> str:
    names = ", ".join(w.first for w in who)
    h = trip.plan().heads()
    direction = "down" if new < old else "up"
    what = "joined" if joined else "dropped out"
    return (f"{m.first}, {names} {what}, so the rooms now split among {h}: your share is {fmt_inr(new)}, "
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
    k = p.party(m.id)
    lines = [f"Your confirmations for {trip.name}" + (f", {k} seats" if k > 1 else "") + ":"]
    for l in p.legs_for(m.id):
        diff = ""
        if l.quoted is not None and l.quoted != l.price:
            d = (l.price - l.quoted) * l.seats
            diff = f" ({'+' if d > 0 else ''}{fmt_inr(d)} vs the fare at the vote)"
        seats = f" × {l.seats}" if l.seats > 1 else ""
        lines.append(f" • {leg_desc(l)} — PNR {l.pnr} — {fmt_inr(l.price)}{seats}{diff}")
    h = p.heads()
    for s in p.stays:
        lines.append(f" • {s.name}, {s.nights} nights, twin sharing — ref {s.booking_ref} — {fmt_inr(s.per_head(h))} a head"
                     + (f" × {k}" if k > 1 else ""))
    a = trip.authorisations[m.id]
    charged = a.captured_amount + (trip.top_ups[m.id].captured_amount if m.id in trip.top_ups else 0)
    how = INSTRUMENTS[a.instrument]["label"]
    tail = (f"; your issuer collects it as {a.emi_months} monthly instalments" if a.emi_months else
            f"; the {fmt_inr(a.amount - charged)} left of your prepayment comes back after the trip" if a.prepaid else
            "; the rest of the block is released when the trip ends" if a.multi_debit else
            "; the rest of the hold was released at capture")
    lines.append(f"Charged: {fmt_inr(charged)} on your {how} into Quorum's account, paid on to the suppliers above{tail}.")
    return "\n".join(lines)


def booked_group(trip: Trip, p: Plan) -> str:
    who = ", ".join(trip.member(t).first + (f" +{p.party(t) - 1}" if p.party(t) > 1 else "") for t in p.travellers)
    stays = "; ".join(f"{s.name} ({s.booking_ref})" for s in p.stays)
    return (f"Booked. {stays} for {who} ({p.heads()} of you), {p.start:%d}–{p.end:%d %b}. Flights ticketed from each city; PNRs are in "
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


def dm_rebooked(m: Member, old: Leg, new: Leg, extra: int, refund: int | None = None) -> str:
    if refund == 0:
        money = f"{fmt_inr(new.total)} taken from the headroom you authorised; a missed departure carries no refund."
    else:
        money = (f"{fmt_inr(-extra)} of the cancelled fare stays in the pool for you and comes back after the trip." if extra < 0 else
                 f"{fmt_inr(extra)} more than the cancelled fare, taken from the headroom you authorised." if extra > 0 else
                 "Same fare.")
    what = "PNR" if new.mode == "flight" else "booking"
    return f"New {what} {new.pnr} — {leg_desc(new)}, {'arrives' if new.mode != 'flight' else 'lands'} {new.arrive:%d %b %H:%M}. {money}"


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


def dm_missed_options(trip: Trip, m: Member, old: Leg, options: list[Leg], budget: int, minutes: int) -> str:
    lines = [f"{m.first}, you've missed {old.carrier} {old.depart:%H:%M}. No refund on that one, but here's how you still get "
             f"to {old.destination}, soonest first:"]
    for i, o in enumerate(options, 1):
        mode = {"flight": o.carrier, "train": f"{o.carrier} (train)", "cab": f"{o.carrier} cab, whole car"}[o.mode]
        need = o.total - budget
        lines.append(f" {i}. {mode} {o.depart:%H:%M} → arrives {o.arrive:%d %b %H:%M} — {fmt_inr(o.total)}"
                     + (f" (needs {fmt_inr(need)} more than your headroom)" if need > 0 else " (inside your headroom)"))
    lines.append(f"Reply 1, 2 or 3 within {minutes} minutes; if I can't reach you here I'll call. Everyone else's plan stands.")
    return "\n".join(lines)


def missed_group(m: Member, old: Leg) -> str:
    return (f"{m.first} missed the {old.carrier} {old.depart:%H:%M} from {old.origin}. I've sent them the ways to still get "
            f"there today; nothing changes for anyone else.")


def dm_countdown(trip: Trip, m: Member, p: Plan, days: int, hook: str | None) -> str:
    when = "Tomorrow" if days <= 1 else f"{days} days to go"
    first_leg = next((l for l in p.legs_for(m.id) if l.destination == trip.destination), None)
    leg = f" {first_leg.carrier} {first_leg.depart:%a %d %b %H:%M} from {first_leg.origin}, PNR {first_leg.pnr}." if first_leg else ""
    return f"{when}, {m.first}.{leg}" + (f" {hook}" if hook else "") + (" Bags: the monsoon's just gone, so a light rain shell and one warm layer." if days > 1 else " See you there.")


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
