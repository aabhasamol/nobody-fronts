"""Making people feel the place: hooks that never repeat, the nudge to the outs and the flip, details after a
yes, essentials from shared interests, the organiser's deadline, personas, a missed flight, pay-later, countdowns."""
from datetime import date, timedelta
from app.clock import clock
from app.lore import hook, persona_of, HOOKS
from app.models import TripState, AuthStatus
from app.scenario import WEDDING, LEISURE
from tests.test_engine import make, vote_all, group_posts, booked


def dms(trip, m):
    return [e.text for e in trip.events if e.channel == f"dm:{m.id}"]


def test_hooks_prefer_the_persons_interests_and_never_repeat():
    used = []
    first = hook("Goa", "bachelors", ["trekking"], used)
    assert "Dudhsagar" in first and "Collem" in first
    seen = [first]
    while (h := hook("Goa", "bachelors", ["trekking"], used)) is not None:
        assert h not in seen
        seen.append(h)
    families_only = [h["text"] for h in HOOKS["Goa"] if h.get("persona") == "families"]
    assert len(seen) == len(HOOKS["Goa"]) - len(families_only)                       # everything but the other persona's
    assert not any(t in seen for t in families_only)
    assert seen[1] == next(h["text"] for h in HOOKS["Goa"] if h.get("persona") == "bachelors")   # persona comes second
    assert hook("Mars", "bachelors", [], []) is None
    assert persona_of([1, 2, 1], "wedding") == "bachelors" and persona_of([1, 3], "leisure") == "families"
    assert persona_of([1, 1], "pilgrimage") == "families"


def test_the_place_is_in_the_kickoff_the_vote_dm_and_the_reminder():
    engine, members, trip, _ = make(silent=("Karan",))
    assert any("Chapora" in t or "monsoon greens" in t or "Fontainhas" in t for t in group_posts(trip)[:1])
    sayan_vote = next(t for t in dms(trip, members[0]) if "here's the plan" in t)
    assert "Dudhsagar" in sayan_vote or "Anjuna flea" in sayan_vote                 # his interests: trekking, food
    riya_vote = next(t for t in dms(trip, members[3]) if "here's the plan" in t)
    assert "Vagator's clubs" in riya_vote or "reel" in riya_vote                    # hers: nightlife, reels
    vote_all(engine, trip, members, no=("Karan",))
    clock.advance(24); engine.tick(trip)
    nudge = next(t for t in dms(trip, members[0]) if "still waiting for you" in t)
    assert nudge.count("₹") >= 1 and len(nudge) > 200                              # the reminder carries a hook too
    assert len(set(trip.used_hooks[members[0].id])) == len(trip.used_hooks[members[0].id])   # nothing repeated to him


def test_outs_get_one_nudge_with_their_own_number_and_can_flip_in():
    engine, members, trip, _ = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    karan = members[4]
    nudge = next(t for t in dms(trip, karan) if "the others are going" in t)
    assert "₹12,200 a head" in nudge and "Say yes by" in nudge                      # 6 heads: cheaper for everyone
    assert trip.flip_until == clock.now() + timedelta(hours=12)
    before = {t: trip.plan().share(t) for t in trip.plan().travellers}
    engine.member_flips(trip, karan.id)
    p = trip.plan()
    assert karan.id in [m.id for m in trip.in_members()] and p.heads() == 6 and karan.id in trip.authorisations
    assert trip.total_budget == 6 * 20_000 and p.total() == 99_200 <= trip.total_budget   # Karan was silent: the default ceiling
    assert all(p.share(t) < before[t] for t in before)                              # everyone's share dropped
    assert any("Karan joined" in t and "down from" in t for t in dms(trip, members[0]))
    for m in members:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED and len(trip.plan().travellers) == 5
    clock.advance(13)
    engine2, members2, trip2, _ = make(silent=("Karan",))
    vote_all(engine2, trip2, members2, no=("Karan",))
    clock.advance(13); engine2.tick(trip2)
    import pytest
    with pytest.raises(AssertionError, match="window to change your mind has closed"):
        engine2.member_flips(trip2, members2[4].id)


def test_names_and_dob_are_asked_only_after_a_yes():
    engine, members, trip, _ = make()
    assert not any("date of birth" in t for m in members for t in dms(trip, m))
    engine.vote(trip, members[4].id, False, "no leave")
    assert not any("date of birth" in t for t in dms(trip, members[4]))
    engine.vote(trip, members[1].id, True)
    ask = next(t for t in dms(trip, members[1]) if "date of birth" in t)
    assert "each of the 2" in ask                                                   # Aabhas pays for two
    engine.details(trip, members[1].id, {"names": "Aabhas Amol; Meera Amol", "dob": "1997-02-11; 1998-07-30", "food": "veg"})
    assert trip.constraints[members[1].id].details["food"] == "veg"
    assert "on the tickets exactly like that" in dms(trip, members[1])[-1]


def test_leisure_gets_one_prebooked_essential_from_a_shared_interest_and_pays_it():
    engine, members, trip, (_, payments, _) = make(LEISURE)
    p = trip.plan()
    essentials = [a for a in p.activities if a.prebook]
    assert [a.name for a in essentials] == ["Dudhsagar jeep-and-hike from Collem"] and p.essentials_share() == 1_200
    assert any(not a.prebook for a in p.activities)                                 # and things to do on the day
    vote = next(t for t in dms(trip, members[0]) if "here's the plan" in t)
    assert "booked with the trip" in vote and "On the day, if you like" in vote
    _, _, wedding, _ = make(WEDDING)
    assert wedding.plan().activities == []                                          # a wedding sets its own agenda
    vote_all(engine, trip, members)
    for m in members:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED
    act = next(x for x in trip.payouts if x.purpose == "ACTIVITY")
    assert act.amount == 1_200 * 6 and act.supplier == "Collem Jeep Owners' Co-op"
    assert payments.pool() == 0


def test_the_organiser_sets_the_deadline_and_personas_change_defaults():
    engine, members, trip, _ = make(silent=("Karan",), auth_window_h=24)
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.auth_deadline == clock.now() + timedelta(hours=24)
    assert trip.persona() == "bachelors"                                            # a couple is not a family
    trip.constraints[members[1].id].party = 3
    assert trip.persona() == "families"
    _, _, fam, (_, _, logistics) = make(LEISURE, persona="families")
    acts = logistics.search_activities("Goa", ["nightlife", "food"], "families")
    assert not any(a.interest == "nightlife" for a in acts)                         # no club nights for the parents
    assert "persona" not in group_posts(fam)[0]                                     # personas shape, they are never announced


def test_missed_flight_offers_flights_a_train_and_a_cab_on_the_members_own_money():
    engine, members, trip, (_, payments, logistics) = make(LEISURE)
    vote_all(engine, trip, members)
    for m in members:
        engine.member_approves(trip, m.id)
    karan = members[4]
    p = trip.plan()
    leg = next(l for l in p.legs_for(karan.id) if l.destination == "Goa")
    pool_before = payments.pool()
    engine.missed(trip, leg.id)
    d = trip.pending[karan.id]
    assert d.refund == 0 and payments.pool() == pool_before                         # no carrier refund this time
    modes = {o.mode for o in d.options}
    assert "flight" in modes and "cab" in modes                                     # Mumbai → Goa is a road too
    cab = next(o for o in d.options if o.mode == "cab")
    assert cab.carrier == "Uber Outstation" and cab.total == 9_400
    dm = dms(trip, karan)[-1]
    assert "No refund on that one" in dm and "Uber Outstation cab" in dm and "Everyone else's plan stands" in dm
    assert "missed the Akasa 08:00" in group_posts(trip)[-1]
    engine.member_chooses(trip, karan.id, 1)                                        # the 13:00 IndiGo at ₹3,200
    top = trip.top_ups[karan.id]
    assert top.amount == 1_800                                                      # his ₹1,400 headroom covers the rest
    engine.member_approves(trip, karan.id)
    new = next(l for l in p.legs_for(karan.id) if l.destination == "Goa")
    assert new.status == "REBOOKED" and new.depart.hour == 13
    assert "a missed departure carries no refund" in dms(trip, karan)[-1]
    assert payments.pool() == pool_before                                           # 3,200 in from Karan, 3,200 out to IndiGo


def test_pay_later_via_lazypay_settles_quorum_in_full():
    engine, members, trip, (_, payments, _) = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    aditi = members[2]
    ask = next(t for t in dms(trip, aditi) if "Block ₹" in t)
    assert "Pay later: LazyPay at booking" in ask                                    # ₹15,400 is inside the limit
    sayan_ask = next(t for t in dms(trip, members[0]) if "Block ₹" in t)
    assert "Pay later" not in sayan_ask or trip.authorisations[members[0].id].amount <= 30_000
    engine.member_approves(trip, aditi.id, "BNPL")
    for m in members[:2] + [members[3]]:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED
    a = trip.authorisations[aditi.id]
    assert a.instrument == "BNPL" and a.captured_amount == 13_980 and a.headroom() == 0
    assert any("LazyPay fronted it — Quorum settled in full" in e.text for e in trip.events if e.channel == "rail:payments")
    assert sum(x.amount for x in trip.payouts) == 93_400 and payments.pool() == 0


def test_countdown_drips_keep_the_place_in_mind_without_repeating():
    engine, members, trip, _ = booked()
    n = len(trip.events)
    clock.set(clock.now().replace(month=9, day=25, hour=9))                          # a week before 2 Oct
    engine.tick(trip)
    drips = [e for e in trip.events[n:] if e.channel.startswith("dm:")]
    assert len(drips) == 4 and all(e.priority == "P2" and "days to go" in e.text and "PNR" in e.text for e in drips)
    engine.tick(trip)
    assert len(trip.events) == n + 4                                                # T-7 goes once
    clock.set(clock.now().replace(month=10, day=1, hour=9))
    engine.tick(trip)
    last = [e for e in trip.events[n + 4:] if e.channel.startswith("dm:")]
    assert len(last) == 4 and all("Tomorrow" in e.text for e in last)
    for m in members[:4]:
        hooks = trip.used_hooks[m.id]
        assert len(hooks) == len(set(hooks))


def test_daily_place_fact_after_booking_until_departure():
    engine, members, trip, _ = booked()
    n = len(trip.events)
    clock.set(clock.now().replace(month=9, day=22, hour=8))                          # before the hour: nothing
    engine.tick(trip)
    assert len(trip.events) == n
    clock.set(clock.now().replace(hour=9))
    engine.tick(trip)
    day1 = [e for e in trip.events[n:] if e.channel.startswith("dm:")]
    assert len(day1) == 4 and all(e.priority == "P2" and "Goa in 10 days" in e.text for e in day1)
    engine.tick(trip)                                                                # once a day
    assert len(trip.events) == n + 4
    clock.set(clock.now().replace(day=23, hour=14))
    engine.tick(trip)
    day2 = [e for e in trip.events[n + 4:] if e.channel.startswith("dm:")]
    assert len(day2) == 4 and all("Goa in 9 days" in e.text for e in day2)
    for a, b in zip(day1, day2):                                                     # same person, different line
        assert a.channel == b.channel and a.text.split(". ", 1)[1] != b.text.split(". ", 1)[1]
    clock.set(clock.now().replace(day=25, hour=9))                                   # T-7: the countdown, no extra fact
    engine.tick(trip)
    t7 = [e for e in trip.events[n + 8:] if e.channel.startswith("dm:")]
    assert len(t7) == 4 and all("days to go" in e.text for e in t7)
    clock.set(clock.now().replace(month=10, day=2, hour=9))                          # departure day: silent
    engine.tick(trip)
    assert len(trip.events) == n + 12
    for m in members[:4]:
        hooks = trip.used_hooks[m.id]
        assert len(hooks) == len(set(hooks))
