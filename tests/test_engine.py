"""The loop against the mock rails: gather → plan → vote → authorise → book, and every unhappy turn."""
from datetime import date
from app.clock import clock
from app.engine import Engine
from app.models import TripState, AuthStatus
from app.rails import build_rails
from app.scenario import five_friends, WEDDING, LEISURE, REPLIES


def make(trip_kwargs=WEDDING, silent=(), **overrides):
    voice, payments, logistics = build_rails()
    engine = Engine(voice, payments, logistics)
    members = five_friends()
    trip = engine.trigger(organiser=members[0], members=members, **{**trip_kwargs, **overrides})
    for m in members:
        if m.first not in silent:
            engine.gather(trip, m.id, **REPLIES[m.first])
    if silent:
        clock.advance(24); engine.tick(trip)
    return engine, members, trip, (voice, payments, logistics)


def vote_all(engine, trip, members, no=(), skip=()):
    for m in members:
        if m.first in skip:
            continue
        engine.vote(trip, m.id, m.first not in no, "too expensive" if m.first in no else "")


def group_posts(trip):
    return [e.text for e in trip.events if e.channel == "group"]


# ------------------------------------------------------------------ gather + plan
def test_silence_defaults_to_home_city_both_ways():
    engine, members, trip, _ = make(silent=("Karan",))
    c = trip.constraints[members[4].id]
    assert c.defaulted and c.start_city == "Mumbai" and c.return_city == "Mumbai"
    assert trip.state == TripState.VOTING


def test_travel_is_per_member_out_and_back_searched_separately():
    engine, members, trip, _ = make()
    p = trip.plan()
    aditi = p.legs_for(members[2].id)
    assert [l.origin for l in aditi] == ["Bengaluru", "Goa"] and aditi[1].destination == "Mumbai"
    assert p.heads() == 6 and p.stay_share() == 6_400                              # Aabhas pays for two
    assert p.per_head(members[0].id) == 18_700 and p.per_head(members[2].id) == 12_700
    assert p.share(members[1].id) == 37_400 == 2 * p.per_head(members[1].id)
    assert all(l.seats == 2 for l in p.legs_for(members[1].id)) and all(l.seats == 1 for l in aditi)


def test_phone_only_stay_is_called_and_the_calls_price_wins_listed_stays_are_not_called():
    engine, members, trip, (voice, _, _) = make()
    p = trip.plan()
    stay = p.stays[0]
    assert stay.phone_only and stay.name.startswith("Dona Maria")
    assert [c.disposition for c in stay.calls] == ["NO_ANSWER", "CONFIRMED"]       # retried once
    assert stay.rate_per_room_night == 3_200 and stay.hold_until is not None       # listing said 2,800
    assert voice.attempts == {"Dona Maria Homestay, Assagao": 2}                    # Cabana, Sea Breeze: never called


def test_wedding_ranks_by_distance_to_venue_leisure_by_price():
    _, _, wedding, _ = make(WEDDING)
    _, _, leisure, (voice, _, _) = make(LEISURE)
    assert wedding.plan().stays[0].km_to_venue == 1.5
    assert "Fisherman's Rest, Morjim" in leisure.stays_out                          # cheapest, called, no rooms
    assert leisure.plan().legs_for(leisure.members[0].id)[0].depart.hour == 19      # cheapest, not earliest
    assert wedding.plan().legs_for(wedding.members[0].id)[0].arrive.hour < 18       # before the first function


def test_nothing_fits_goes_to_the_organiser_never_quietly_over():
    engine, members, trip, _ = make(LEISURE, budget=12_000)
    assert trip.state == TripState.PLANNING and trip.awaiting_organiser == "no_fit" and not trip.plans
    dm = [e for e in trip.events if e.channel == f"dm:{members[0].id}" and "nothing fits" in e.text]
    assert dm and "₹13,200" in dm[0].text
    engine.organiser_adjusts(trip, budget=20_000)
    assert trip.state == TripState.VOTING and trip.plan().version == 1


def test_one_members_travel_over_the_limit_is_flagged_privately_not_averaged():
    engine, members, trip, _ = make(LEISURE, budget=18_000, overshoot=0.0)
    assert trip.state == TripState.VOTING
    flagged = {e.channel for e in trip.events if "heads-up before the vote" in e.text}
    assert flagged == {f"dm:{members[0].id}", f"dm:{members[1].id}"}                # the two from Kolkata
    p = trip.plan()
    assert p.per_head(members[3].id) == 17_800 <= trip.limit() < p.per_head(members[0].id) == 18_200


# ------------------------------------------------------------------ vote
def test_private_vote_only_the_tally_is_posted_and_yes_voters_are_in():
    engine, members, trip, _ = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.state == TripState.AUTHORISING
    assert [m.first for m in trip.in_members()] == ["Sayan", "Aabhas", "Aditi", "Riya"]
    assert members[4].id not in trip.authorisations
    posts = group_posts(trip)
    assert len(posts) == 2 and "4 yes, 1 no" in posts[1] and "Karan" not in posts[1]


def test_authorised_amount_is_share_times_one_plus_overshoot_after_the_resplit():
    engine, members, trip, _ = make()
    vote_all(engine, trip, members, no=("Karan",))
    p = trip.plan()
    assert len(p.travellers) == 4 and p.heads() == 5 and p.per_head(members[0].id) == 19_980   # was 18,700 with six heads
    a = trip.authorisations[members[0].id]
    assert a.amount == 22_000 and a.status == AuthStatus.PENDING
    assert trip.authorisations[members[1].id].amount == 44_000                     # two heads, one payer


def test_failed_vote_revises_from_the_reasons_then_one_reminder_then_not_in():
    engine, members, trip, _ = make(LEISURE)
    vote_all(engine, trip, members, no=("Aditi", "Riya"), skip=("Karan",))
    clock.advance(12); engine.tick(trip)
    assert sum("a nudge" in e.text for e in trip.events if e.channel == f"dm:{members[4].id}") == 1
    clock.advance(12); engine.tick(trip)
    assert trip.state == TripState.VOTING and trip.plan().version == 2
    assert trip.plan().note == "same stay, one night fewer" and trip.plan().nights() == 3
    assert "2 yes, 2 no, 1 didn't reply" in group_posts(trip)[-1]


def test_after_two_revisions_the_organiser_decides():
    engine, members, trip, _ = make(LEISURE)
    for _ in range(3):
        vote_all(engine, trip, members, no=("Aditi", "Riya", "Karan"))
    assert trip.awaiting_organiser == "vote_failed" and len(trip.plans) == 3
    engine.organiser_decides(trip, go=True)
    assert trip.state == TripState.AUTHORISING and [m.first for m in trip.in_members()] == ["Sayan", "Aabhas"]


# ------------------------------------------------------------------ authorise + book
def test_everyone_in_authorises_and_it_books_for_exactly_them():
    engine, members, trip, (voice, _, _) = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED
    assert {trip.authorisations[m.id].status for m in members[:4]} == {AuthStatus.CAPTURED}
    assert sum(a.captured_amount for a in trip.authorisations.values()) == 93_400
    p = trip.plan()
    assert all(l.pnr for l in p.legs) and members[4].id not in p.travellers and p.stays[0].booking_ref
    assert len(group_posts(trip)) == 3                                             # kickoff, tally, booked


def test_dropout_at_the_deadline_reprices_the_rest_and_proceeds_inside_their_caps():
    engine, members, trip, _ = make(LEISURE)
    vote_all(engine, trip, members, no=("Aditi", "Riya"), skip=("Karan",))
    clock.advance(24); engine.tick(trip)                                           # 2–2–1: no majority → v2
    vote_all(engine, trip, members)                                                # v2, everyone yes
    for m in (members[0], members[1], members[2], members[4]):
        engine.member_approves(trip, m.id)
    clock.advance(48); engine.tick(trip)
    assert trip.state == TripState.BOOKED
    assert trip.authorisations[members[3].id].status == AuthStatus.RELEASED and members[3].id in trip.dropped
    p = trip.plan()
    assert len(p.travellers) == 4 and p.heads() == 5 and p.per_head(members[0].id) == 17_560 > 16_600   # 5 heads, 3 rooms: up, inside caps
    assert any("dropped out, so the rooms now split among 5" in e.text and "up from" in e.text
               for e in trip.events if e.channel == f"dm:{members[0].id}")


def test_withdrawing_after_blocking_is_a_dropout_and_a_reprice_over_the_cap_goes_back_to_the_group():
    engine, members, trip, _ = make()
    vote_all(engine, trip, members, no=("Karan",))
    for m in members[:4]:
        engine.member_approves(trip, m.id) if m.first != "Riya" else None
    engine.member_approves(trip, members[3].id)                                   # everyone in... (booking fires)
    assert trip.state == TripState.BOOKED
    # Riya asks out after blocking: four heads left need two rooms, so everyone's share drops inside their cap
    engine, members, trip, _ = make()
    vote_all(engine, trip, members, no=("Karan",))
    for m in members[:4]:
        engine.member_approves(trip, m.id) if m.first != "Aditi" else None
    engine.member_withdraws(trip, members[3].id)
    assert trip.state == TripState.AUTHORISING and trip.plan().heads() == 4 and trip.plan().share(members[0].id) == 18_700
    assert trip.authorisations[members[3].id].status == AuthStatus.RELEASED and members[3].id in trip.dropped
    # (if Aditi also left, the lowest ceiling would rise to ₹24,000 and the two Kolkata payers would still fit)
    # Where a withdrawal does break a cap: leisure, all five in, Aabhas's two heads leave, then Karan —
    # three heads on two rooms pushes Sayan, Aditi and Riya past what they authorised
    engine, members, trip, _ = make(LEISURE)
    vote_all(engine, trip, members)
    for m in (members[0], members[2], members[3]):
        engine.member_approves(trip, m.id)
    engine.member_withdraws(trip, members[1].id)
    assert trip.state == TripState.AUTHORISING and trip.plan().heads() == 4
    engine.member_withdraws(trip, members[4].id)
    assert trip.state == TripState.VOTING and trip.plan().version == 2
    assert len(trip.plan().travellers) == 3 and trip.plan().heads() == 3 and not trip.authorisations
    assert any("released — plan going back to the group" in e.text for e in trip.events)


def test_fare_moves_inside_the_cap_are_absorbed_and_shown_on_the_receipt():
    engine, members, trip, (_, _, logistics) = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    logistics.drift["Delhi"] = 1.04
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    riya = trip.authorisations[members[3].id]
    assert trip.state == TripState.BOOKED and riya.captured_amount == 19_952 <= riya.amount
    assert any("+₹244 vs the fare at the vote" in e.text for e in trip.events if e.channel == f"dm:{members[3].id}")


def test_fare_moves_beyond_the_cap_ask_only_that_member_to_top_up():
    engine, members, trip, (_, _, logistics) = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    logistics.drift["Delhi"] = 1.3
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKING and set(trip.top_ups) == {members[3].id}
    assert not any(a.captured_amount for a in trip.authorisations.values())       # nobody debited yet
    engine.member_approves(trip, members[3].id)                                   # approves the top-up
    assert trip.state == TripState.BOOKED and trip.top_ups[members[3].id].captured_amount > 0


def test_one_failed_debit_refunds_the_others_and_books_nothing():
    engine, members, trip, (_, payments, _) = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    payments.fail_capture_for.add(members[3].id)
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.LAPSED
    assert [trip.authorisations[m.id].status for m in members[:3]] == [AuthStatus.REFUNDED] * 3
    assert sum(a.captured_amount for a in trip.authorisations.values()) == 0
    assert not trip.plan().stays[0].booking_ref and not any(l.pnr for l in trip.plan().legs)
    payments.fail_capture_for.clear()
    engine.rerun(trip)
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED


def test_expired_hold_is_re_called_before_any_debit():
    engine, members, trip, (voice, _, _) = make(silent=("Karan",))
    clock.advance(20); engine.tick(trip)                                           # people vote over a day...
    vote_all(engine, trip, members, no=("Karan",))
    clock.advance(30); engine.tick(trip)                                           # ...and authorise over two
    for m in members[:4]:
        engine.member_approves(trip, m.id)                                         # the 48h hold has lapsed by now
    assert trip.state == TripState.BOOKED
    assert voice.attempts["Dona Maria Homestay, Assagao"] == 3
    assert any("expired" in e.text and "re-hold" in e.text for e in trip.events)


# ------------------------------------------------------------------ after booking
def booked():
    engine, members, trip, rails = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED
    return engine, members, trip, rails


def test_rebook_inside_the_cap_and_call_the_phone_only_stay_about_a_late_arrival():
    engine, members, trip, _ = booked()
    p = trip.plan()
    leg = next(l for l in p.legs_for(members[1].id) if l.destination == "Goa")
    engine.disrupt(trip, leg.id)
    new = next(l for l in p.legs_for(members[1].id) if l.destination == "Goa")
    assert new.status == "REBOOKED" and new.pnr != leg.pnr and new.price == 5_900 and new.seats == 2   # both seats moved
    assert new.arrive.hour >= 20 and p.stays[0].calls[-1].purpose == "LATE_ARRIVAL"
    assert "no new approval needed" in group_posts(trip)[-1]


def test_rebook_over_the_cap_texts_then_calls_then_tops_up_that_member_only():
    engine, members, trip, (_, _, logistics) = booked()
    p = trip.plan()
    logistics.drift["Delhi"] = 1.6
    leg = next(l for l in p.legs_for(members[3].id) if l.destination == "Goa")
    engine.disrupt(trip, leg.id)
    d = trip.pending[members[3].id]
    assert d.kind == "REBOOK" and d.choice is None and d.shortfall == 1_000 and not trip.top_ups
    clock.advance(0.5); engine.tick(trip)                                          # 20 minutes without a reply
    assert d.escalated and d.choice == 1 and trip.top_ups[members[3].id].amount == 1_000
    engine.member_approves(trip, members[3].id)
    new = next(l for l in p.legs_for(members[3].id) if l.destination == "Goa")
    assert new.status == "REBOOKED" and new.price == 9_120
    assert trip.top_ups[members[3].id].captured_amount == 1_000 and members[3].id not in trip.pending
    assert all(trip.authorisations[m.id].captured_amount == trip.plan().share(m.id) for m in members[:3])


def test_member_exit_after_booking_goes_to_humans():
    engine, members, trip, _ = booked()
    engine.member_exits(trip, members[2].id)
    assert members[2].id in trip.dropped and len(trip.plan().travellers) == 3
    assert "over to you" in group_posts(trip)[-1]


# ------------------------------------------------------------------ the yes-voters set the budget; the pool pays
def test_yes_voters_set_the_budget_and_the_plan_is_resized_to_fit_it():
    engine, members, trip, _ = make(silent=("Karan",))
    assert trip.ceiling(members[2].id) == 20_000 and trip.ceiling(members[4].id) == 20_000   # stated / defaulted
    assert trip.total_budget is None                                                # a proposal: nobody is in yet
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.budget_floor == 20_000 and trip.total_budget == 100_000              # 5 heads × Aditi's ceiling
    p = trip.plan()
    assert len(p.travellers) == 4 and p.heads() == 5 and p.total() == 93_400 <= trip.total_budget
    assert p.per_head(members[0].id) == 19_980 and p.share(members[1].id) == 39_960   # 3 rooms among 5, not 6
    post = group_posts(trip)[-1]
    assert "₹1,00,000 (5 × ₹20,000" in post and "4 paying for 5" in post and "comes to ₹93,400" in post


def test_a_low_ceiling_among_the_yes_voters_sends_the_plan_back_for_a_cheaper_version():
    engine, members, trip, _ = make(silent=("Karan",))
    trip.constraints[members[2].id].budget = 17_000                                   # Aditi: 5 × 17,000 = 85,000 < 93,400
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.state == TripState.VOTING and trip.plan().version == 2
    assert trip.plan().note == "same stay, one night fewer" and len(trip.plan().travellers) == 4
    assert trip.plan().total() == 83_800 <= trip.total_budget == 85_000
    assert any("against a budget of ₹85,000" in e.text for e in trip.events if e.channel == "group")
    assert not trip.authorisations                                                   # nobody was asked for money


def test_captures_land_in_the_pool_and_every_supplier_is_paid_from_it():
    engine, members, trip, (_, payments, _) = booked()
    p = trip.plan()
    captured = sum(a.captured_amount for a in trip.authorisations.values())
    paid = sum(x.amount for x in trip.payouts)
    assert captured == paid == 93_400 and payments.pool() == 0                       # nothing fronted, nothing left over
    assert {x.purpose for x in trip.payouts} == {"STAY", "LEG"} and len(trip.payouts) == 1 + 8
    stay = next(x for x in trip.payouts if x.purpose == "STAY")
    assert stay.amount == p.stay_total(p.stays[0]) == 38_400 and stay.reference == p.stays[0].booking_ref
    aabhas_out = next(x for x in trip.payouts if x.purpose == "LEG" and x.amount == 12_800)   # 2 seats × ₹6,400
    assert aabhas_out.supplier == "IndiGo"
    assert "Every supplier is paid from Quorum's account, ₹93,400 in all" in group_posts(trip)[-1]


def test_a_cancelled_leg_refunds_into_the_pool_and_the_new_ticket_is_paid_from_it():
    engine, members, trip, (_, payments, _) = booked()
    p = trip.plan()
    leg = next(l for l in p.legs_for(members[1].id) if l.destination == "Goa")
    engine.disrupt(trip, leg.id)
    assert payments.pool() == 2 * (6_400 - 5_900)                                    # ₹1,000 stays for Aabhas's two seats
    rebook = trip.payouts[-1]
    assert rebook.purpose == "REBOOK" and rebook.amount == 11_800 and rebook.supplier == "IndiGo"


def test_the_pool_never_goes_negative():
    import pytest
    from app.rails.mock import MockPayments
    pm = MockPayments()
    with pytest.raises(AssertionError, match="does not front"):
        pm.pay_supplier("Anyone", 1, "STAY", "x")


# ------------------------------------------------------------------ calls are for P0 things only
def test_every_call_is_p0_and_nothing_else_is():
    engine, members, trip, (_, _, logistics) = booked()
    p = trip.plan()
    logistics.drift["Delhi"] = 1.6
    engine.disrupt(trip, next(l for l in p.legs_for(members[3].id) if l.destination == "Goa").id)
    clock.advance(0.5); engine.tick(trip)
    p0 = [e for e in trip.events if e.priority == "P0"]
    assert p0 and all(e.channel == "rail:voice" for e in p0)
    reasons = {e.text.split(":")[0] for e in p0}
    assert reasons == {"SUPPLIER_AVAILABILITY", "DISRUPTION_UNANSWERED"}          # no late arrival in this run
    assert all(e.priority != "P0" for e in trip.events if e.channel.startswith("dm:") or e.channel == "group")
    assert {e.priority for e in trip.events if "counted" in e.text or "Blocked ₹" in e.text} == {"P2"}


def test_authorisation_reminder_is_a_text_never_a_call():
    engine, members, trip, _ = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    engine.member_approves(trip, members[0].id)
    calls_before = len([e for e in trip.events if e.priority == "P0"])
    clock.advance(24); engine.tick(trip)                                           # halfway through the 48h window
    nudged = [e.channel for e in trip.events if "are still waiting for you" in e.text]
    assert sorted(nudged) == sorted(f"dm:{m.id}" for m in members[1:4])           # the three who haven't approved
    clock.advance(1); engine.tick(trip)
    assert len([e for e in trip.events if "are still waiting for you" in e.text]) == 3   # once, not again
    assert len([e for e in trip.events if e.priority == "P0"]) == calls_before
