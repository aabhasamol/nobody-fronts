"""Run the whole loop in the terminal, twice: once to a booking, once to a lapse, then a disruption.

    python demo.py
"""
from __future__ import annotations
from app.clock import clock
from app.engine import Engine
from app.rails import build_rails
from app.scenario import five_friends, TRIP


def show(trip, since=0):
    for e in trip.events[since:]:
        ch = {"group": "GROUP", "system": "  sys"}.get(e.channel, e.channel.replace("dm:", "DM→").replace("rail:", "RAIL "))
        if e.channel.startswith("dm:"):
            ch = "DM→" + trip.member(e.channel[3:]).name.split()[0]
        print(f"[{e.at:%a %H:%M}] {ch:<14} {e.actor:<8} {e.text.splitlines()[0][:110]}")
        for line in e.text.splitlines()[1:]:
            print(" " * 34 + line[:110])
    return len(trip.events)


def run(path: str):
    engine = Engine(*build_rails())
    members = five_friends()
    print(f"\n{'='*100}\nPATH: {path}\n{'='*100}")
    t = engine.trigger(organiser=members[0], members=members, **TRIP)
    n = show(t)
    # three reply, two go silent; the capture window closes and defaults apply
    engine.capture(t, members[0].id, budget_ceiling=20000, must_haves=["no hostels"], text="Yes. ₹20k. No hostels.")
    engine.capture(t, members[1].id, budget_ceiling=22000, text="In. ₹22k.")
    engine.capture(t, members[2].id, budget_ceiling=18000, must_haves=["no hostels"], text="Yes, ₹18k, not a hostel.")
    clock.advance(24); engine.tick(t)
    n = show(t, n)
    approvers = members[:4] if path == "book" else members[:2]
    for m in approvers:
        engine.member_approves(t, m.id)
    clock.advance(48); engine.tick(t)
    n = show(t, n)
    if path == "book":
        b = t.default_bundle()
        leg = next(l for l in b.legs if l.member_id == members[1].id and l.status == "BOOKED")
        engine.disrupt(t, leg.id)
        show(t, n)
    print(f"\nFINAL STATE: {t.state.value}   blocked={len(t.blocked())}   captured=₹{sum(a.captured_amount for a in t.authorisations.values()):,}")


if __name__ == "__main__":
    run("book")
    clock.set(clock.now().replace(hour=10, minute=0))
    run("lapse")
