from datetime import date
from app.clock import clock
from app.engine import Engine
from app.models import TripState, AuthStatus
from app.rails import build_rails
from app.scenario import five_friends, TRIP


def make():
    engine = Engine(*build_rails())
    members = five_friends()
    trip = engine.trigger(organiser=members[0], members=members, **TRIP)
    for m in members:
        engine.capture(trip, m.id, budget_ceiling=20000, must_haves=["no hostels"])
    return engine, members, trip


def test_all_replies_posts_two_verified_bundles():
    engine, members, trip = make()
    assert trip.state == TripState.POSTED
    assert len(trip.bundles) == 2
    assert all(b.stay.verification.disposition == "VERIFIED" for b in trip.bundles)
    assert "Palm Grove" not in {b.stay.name for b in trip.bundles}          # failed the call, got swapped
    assert len(trip.authorisations) == 5 and all(a.status == AuthStatus.PENDING for a in trip.authorisations.values())


def test_quorum_books_only_those_who_committed():
    engine, members, trip = make()
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    clock.advance(48); engine.tick(trip)
    assert trip.state == TripState.BOOKED
    statuses = {m.id: trip.authorisations[m.id].status for m in members}
    assert [statuses[m.id] for m in members[:4]] == [AuthStatus.CAPTURED] * 4
    assert statuses[members[4].id] == AuthStatus.RELEASED
    b = trip.default_bundle()
    assert all(l.pnr for l in b.legs if l.member_id in {m.id for m in members[:4]})
    assert not any(l.pnr for l in b.legs if l.member_id == members[4].id)


def test_below_quorum_lapses_and_charges_nobody():
    engine, members, trip = make()
    for m in members[:2]:
        engine.member_approves(trip, m.id)
    clock.advance(48); engine.tick(trip)
    assert trip.state == TripState.LAPSED
    assert all(a.status == AuthStatus.RELEASED for a in trip.authorisations.values())
    assert sum(a.captured_amount for a in trip.authorisations.values()) == 0


def test_everyone_in_closes_early():
    engine, members, trip = make()
    for m in members:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED                                    # no clock advance needed


def test_rebook_stays_inside_cap():
    engine, members, trip = make()
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    clock.advance(48); engine.tick(trip)
    b = trip.default_bundle()
    leg = next(l for l in b.legs if l.member_id == members[0].id and l.status == "BOOKED")
    engine.disrupt(trip, leg.id)
    new = next(l for l in b.legs if l.member_id == members[0].id and l.origin == leg.origin)
    assert new.status == "REBOOKED" and new.pnr and new.pnr != leg.pnr
    a = trip.authorisations[members[0].id]
    assert new.price <= a.amount - a.captured_amount + leg.price          # inside the blocked headroom


def test_rerun_after_lapse():
    engine, members, trip = make()
    clock.advance(48); engine.tick(trip)
    assert trip.state == TripState.LAPSED
    engine.rerun(trip, quorum=3)
    assert trip.state == TripState.POSTED and trip.quorum == 3
    for m in members[:3]:
        engine.member_approves(trip, m.id)
    clock.advance(48); engine.tick(trip)
    assert trip.state == TripState.BOOKED
