"""Everything the agent says, in one place.

House rules: short sentences, one ask per message, always a default and a
deadline, never a group poll.
"""
from __future__ import annotations
from datetime import datetime
from .models import Trip, Member, Bundle


def fmt_dt(d: datetime) -> str:
    return d.strftime("%a %d %b, %H:%M")


def fmt_inr(n: int) -> str:
    return f"₹{n:,}"


def hello_group(trip: Trip) -> str:
    org = trip.organiser()
    return (f"Hi all — {org.name} added me to plan {trip.name}. {trip.destination}, "
            f"{trip.start:%d %b}–{trip.end:%d %b}, around {fmt_inr(trip.rough_budget)} a head. "
            f"I'll message each of you privately for your constraints. Nothing gets booked and no money "
            f"moves until {trip.quorum} of {len(trip.members)} of you authorise your own share.")


def dm_capture(trip: Trip, m: Member, deadline: datetime) -> str:
    return (f"Hi {m.name.split()[0]} — three quick things for {trip.name}, just to me, not the group:\n"
            f"1. Can you do {trip.start:%d}–{trip.end:%d %b}? If not, which dates?\n"
            f"2. Your ceiling for the whole trip (flights + stay), per head?\n"
            f"3. Anything non-negotiable — no hostels, direct flights, veg food?\n"
            f"Reply by {fmt_dt(deadline)}. If I don't hear back I'll assume yes to the dates and "
            f"{fmt_inr(min(trip.rough_budget, m.spend_ceiling))} as your ceiling.")


def dm_capture_ack(m: Member) -> str:
    return f"Got it, {m.name.split()[0]}. I'll come back to the group with two options."


def post_bundles(trip: Trip, deadline: datetime) -> str:
    lines = [f"Two options for {trip.name}. Both hotels phone-verified today. Per-head from your own city:"]
    for b in trip.bundles:
        tag = "  ← default" if b.is_default else ""
        lines.append(f"\n{b.label}{tag} — {b.stay.name}, {b.stay.nights} nights")
        for m in trip.members:
            lines.append(f"   {m.name.split()[0]} ({m.home_city}): {fmt_inr(b.per_head(m.id))}")
    lines.append(f"\nI'll DM each of you a UPI request for your own share of the default. Approve it and "
                 f"your money is blocked, not charged. If {trip.quorum} of {len(trip.members)} approve by "
                 f"{fmt_dt(deadline)}, I book everything and the debits fire. If not, every block is released "
                 f"and nobody pays. Reply 'Comfort' if you'd rather switch the default.")
    return "\n".join(lines)


def dm_authorise(trip: Trip, m: Member, b: Bundle, share: int, block: int) -> str:
    return (f"{m.name.split()[0]}, your share of {b.label} is {fmt_inr(share)} "
            f"({b.stay.name} + flights from {m.home_city}). Approve the UPI request I've sent: it blocks "
            f"{fmt_inr(block)} — your share plus 10% headroom so I can re-book you if a flight cancels — and "
            f"charges nothing now. Only your {fmt_inr(share)} share is debited, and only if {trip.quorum} of "
            f"{len(trip.members)} approve by {fmt_dt(trip.deadline)}. Otherwise the block is released.")


def verified_line(trip: Trip) -> str:
    out = []
    for b in trip.bundles:
        v = b.stay.verification
        out.append(f"{b.stay.name}: {v.disposition.lower()} — refund: {v.answers.get('refund_terms', '?')}")
    return "Hotel calls done. " + " | ".join(out)


def swap_line(old: str, new: str, reason: str) -> str:
    return f"Dropped {old} after the call ({reason}). Swapped in {new}."


def booked_group(trip: Trip, b: Bundle) -> str:
    who = ", ".join(trip.member(a.member_id).name.split()[0] for a in trip.authorisations.values()
                    if a.status.value == "CAPTURED")
    return (f"Booked. {b.stay.name} ({b.stay.booking_ref}) for {who}. Flights ticketed from each city; "
            f"I've sent everyone their own PNRs. {trip.organiser().name.split()[0]} didn't pay for anyone and "
            f"doesn't need to chase anyone.")


def dm_confirmation(trip: Trip, m: Member, b: Bundle) -> str:
    legs = [l for l in b.legs if l.member_id == m.id]
    lines = [f"Your confirmations for {trip.name}:"]
    for l in legs:
        lines.append(f" • {l.carrier} {l.origin}→{l.destination} {l.depart:%d %b %H:%M} — PNR {l.pnr}")
    lines.append(f" • {b.stay.name}, {b.stay.nights} nights — ref {b.stay.booking_ref}")
    lines.append(f"Charged: {fmt_inr(trip.authorisations[m.id].captured_amount)} via UPI mandate.")
    return "\n".join(lines)


def lapsed_group(trip: Trip, committed: int) -> str:
    return (f"Deadline passed with {committed} of {trip.quorum} needed. No money moved — every "
            f"block has been released. {trip.organiser().name.split()[0]}, I've sent you who didn't commit; "
            f"say 're-run' to try a new default or 'close' to drop the trip.")


def dm_lapsed_organiser(trip: Trip, committed: list[str]) -> str:
    missing = [m.name for m in trip.members if m.id not in set(committed)]
    return (f"Didn't commit by the deadline: {', '.join(missing) or 'nobody — quorum was mis-set'}. "
            f"Options: re-run with the other bundle as default, lower the quorum, or close.")


def disruption_group(leg_desc: str, rebook_desc: str, member: Member) -> str:
    return (f"{member.name.split()[0]}'s {leg_desc} was cancelled by the carrier. Re-booked on {rebook_desc} "
            f"within the amount already authorised — no new approval needed. New PNR sent.")


def disruption_escalate(member: Member, shortfall: int) -> str:
    return (f"{member.name.split()[0]}'s flight was cancelled and every alternative is {fmt_inr(shortfall)} over "
            f"the authorised cap. I've DMed them a one-tap top-up; the rest of the trip is unaffected.")
