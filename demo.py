"""Run the whole loop in the terminal, three times.

    python demo.py

  1. wedding   — phone-only homestay (no answer → retry → confirmed at the call's rate), vote passes 4–1,
                 the four payers who are in (five heads: Aabhas pays for Meera too) set the budget and the stay
                 re-splits for them; one blocks by UPI Reserve Pay, one holds a credit card for two seats with
                 3-month EMI, one pays the link outright (the money sits in the pool), one holds a card; fares
                 move inside the cap;
                 everything books and the pool pays every supplier. Then a cancelled flight re-booked from the
                 carrier's refund alone (late arrival → the homestay gets a call), and a second cancellation for
                 the card-holder whose hold was used up at capture → text → no reply → escalation call → a fresh
                 card tap for the difference, from that member only.
  2. leisure   — one stay unavailable on the call; the vote fails on price; the revision drops a night; the
                 vote passes with a pre-booked Dudhsagar jeep as the one essential; one yes-voter never authorises,
                 drops out, and the rest re-price. Then Karan misses his Mumbai flight: later flights, a train and an
                 Uber Outstation cab go to him, no refund, his money; the daily fact about the place starts the day it's
                 booked, and a week before the trip everyone gets the countdown text with their PNR.
  3. wall      — the fourth of four debits fails: the three that went through are refunded, nothing is booked.
"""
from __future__ import annotations
from app.clock import clock
from app.engine import Engine
from app.rails import build_rails
from app.scenario import five_friends, WEDDING, LEISURE, REPLIES


def show(trip, since=0):
    for e in trip.events[since:]:
        ch = {"group": "GROUP", "system": "  sys"}.get(e.channel, e.channel.replace("rail:", "RAIL "))
        if e.channel.startswith("dm:"):
            ch = "DM→" + trip.member(e.channel[3:]).first
        lines = e.text.splitlines()
        print(f"[{e.at:%a %H:%M}] {ch:<14} {e.actor[:10]:<10} {lines[0][:120]}")
        for line in lines[1:]:
            print(" " * 36 + line[:120])
    return len(trip.events)


def gather_all(engine, t, members, silent=()):
    for m in members:
        if m.first not in silent:
            engine.gather(t, m.id, **REPLIES[m.first])
    if silent:
        clock.advance(24); engine.tick(t)                      # the window closes; defaults apply


def run_wedding(fail_capture_for=None):
    voice, payments, logistics = build_rails()
    engine = Engine(voice, payments, logistics)
    members = five_friends()
    sayan, aabhas, aditi, riya, karan = members
    label = "wall — one debit fails, everyone is refunded" if fail_capture_for else "wedding — book, then two disruptions"
    print(f"\n{'=' * 110}\nPATH: {label}\n{'=' * 110}")
    t = engine.trigger(organiser=sayan, members=members, **WEDDING)
    n = show(t)
    gather_all(engine, t, members, silent=("Karan",))          # Karan goes quiet; default applies
    n = show(t, n)
    # private votes; only the tally reaches the group
    engine.vote(t, sayan.id, True); engine.vote(t, aabhas.id, True); engine.vote(t, aditi.id, True)
    engine.vote(t, riya.id, True); engine.vote(t, karan.id, False, "Can't get leave that week, sorry.")
    n = show(t, n)
    if fail_capture_for:
        payments.fail_capture_for.add(riya.id)
    logistics.drift["Delhi"] = 1.04                            # fares moved a little since the vote
    engine.member_approves(t, aditi.id, "PREPAID")               # paid the link with a method that can't hold: in the pool now
    clock.advance(24); engine.tick(t)                            # a day passes: the others get one nudge, Aditi's money earns
    engine.member_approves(t, sayan.id, "UPI_RESERVE")           # blocked in his account, headroom stays live
    engine.member_approves(t, aabhas.id, "CARD_PREAUTH", emi_months=3)   # card hold for two seats, EMI at capture
    engine.member_approves(t, riya.id, "CARD_PREAUTH")
    n = show(t, n)
    if fail_capture_for:
        print(f"\nFINAL STATE: {t.state.value}   captured=₹{sum(a.captured_amount for a in t.authorisations.values()):,}")
        return
    # disruption 1: inside the cap, but the only affordable flight lands late → the homestay gets a call
    p = t.plan()
    leg = next(l for l in p.legs if l.member_id == aabhas.id and l.destination == "Goa")
    engine.disrupt(t, leg.id)
    n = show(t, n)
    # disruption 2: day-of fares from Delhi have spiked; every alternative is over Riya's cap
    logistics.drift["Delhi"] = 1.6
    leg = next(l for l in p.legs if l.member_id == riya.id and l.destination == "Goa")
    engine.disrupt(t, leg.id)
    n = show(t, n)
    clock.advance(0.5); engine.tick(t)                          # 30 minutes, no reply → escalation call
    n = show(t, n)
    engine.member_approves(t, riya.id, "CARD_PREAUTH")          # Riya taps the card link for the difference
    show(t, n)
    print(f"\nFINAL STATE: {t.state.value}   captured=₹{sum(a.captured_amount for a in t.authorisations.values()) + sum(a.captured_amount for a in t.top_ups.values()):,}")


def run_leisure():
    engine = Engine(*build_rails())
    members = five_friends()
    sayan, aabhas, aditi, riya, karan = members
    print(f"\n{'=' * 110}\nPATH: leisure — vote fails on price, revision, dropout at authorisation\n{'=' * 110}")
    t = engine.trigger(organiser=sayan, members=members, **LEISURE)
    n = show(t)
    gather_all(engine, t, members)
    n = show(t, n)
    engine.vote(t, sayan.id, True); engine.vote(t, aabhas.id, True)
    engine.vote(t, aditi.id, False, "₹14k for a homestay is a lot. Cheaper?")
    engine.vote(t, riya.id, False, "Over 19k for me — can we bring the cost down?")
    clock.advance(12); engine.tick(t)                           # halfway: Karan gets one reminder text
    clock.advance(12); engine.tick(t)                           # deadline: Karan counted as not in; tally; revision
    n = show(t, n)
    for m in members:                                           # everyone says yes to v2
        engine.vote(t, m.id, True)
    n = show(t, n)
    for m in (sayan, aabhas, aditi, karan):                     # Riya never approves
        engine.member_approves(t, m.id)
    clock.advance(48); engine.tick(t)
    n = show(t, n)
    leg = next(l for l in t.plan().legs if l.member_id == karan.id and l.destination == "Goa")
    engine.missed(t, leg.id)                                     # Karan sleeps through the 08:00 from Mumbai
    engine.member_chooses(t, karan.id, 1)                        # the 13:00: ₹1,900 more than his headroom
    engine.member_approves(t, karan.id)                          # he approves the top-up
    clock.set(clock.now().replace(month=9, day=25, hour=10, minute=0)); engine.tick(t)   # a week before: the countdown
    show(t, n)
    print(f"\nFINAL STATE: {t.state.value}   captured=₹{sum(a.captured_amount for a in t.authorisations.values()) + sum(a.captured_amount for a in t.top_ups.values()):,}")


if __name__ == "__main__":
    run_wedding()
    clock.set(clock.now().replace(hour=10, minute=0))
    run_leisure()
    clock.set(clock.now().replace(hour=10, minute=0))
    run_wedding(fail_capture_for=True)
