"""Run the whole loop in the terminal, three times.

    python demo.py

  1. wedding   — phone-only homestay (no answer → retry → confirmed at the call's rate), vote passes 4–1,
                 the stay re-splits for the four who are in, fares move inside the cap, everything books;
                 then a cancelled flight re-booked inside the cap (late arrival → the homestay gets a call),
                 and a second cancellation whose alternatives exceed the cap → text → no reply → escalation
                 call → top-up from that member only.
  2. leisure   — one stay unavailable on the call; the vote fails on price; the revision drops a night; the
                 vote passes; one yes-voter never authorises, drops out, and the rest re-price downwards.
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
    for m in (sayan, aabhas, aditi, riya):
        engine.member_approves(t, m.id)
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
    engine.member_approves(t, riya.id)                          # Riya approves the top-up in her UPI app
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
    show(t, n)
    print(f"\nFINAL STATE: {t.state.value}   captured=₹{sum(a.captured_amount for a in t.authorisations.values()):,}")


if __name__ == "__main__":
    run_wedding()
    clock.set(clock.now().replace(hour=10, minute=0))
    run_leisure()
    clock.set(clock.now().replace(hour=10, minute=0))
    run_wedding(fail_capture_for=True)
