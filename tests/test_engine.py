"""The loop against the mock rails: gather → plan → vote → authorise → book, and every unhappy turn."""
from datetime import date, timedelta
from app.clock import clock
from app.engine import Engine
from app.models import TripState, AuthStatus
from app.rails import build_rails
from app.scenario import five_friends, WEDDING, LEISURE, REPLIES


def make(trip_kwargs=WEDDING, silent=(), ceilings=None, **overrides):
    voice, payments, logistics = build_rails()
    engine = Engine(voice, payments, logistics)
    members = five_friends()
    trip = engine.trigger(organiser=members[0], members=members, **{**trip_kwargs, **overrides})
    for m in members:
        if m.first not in silent:
            engine.gather(trip, m.id, **{**REPLIES[m.first], **({"budget": ceilings[m.first]} if m.first in (ceilings or {}) else {})})
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
    engine, members, trip, _ = make(LEISURE, budget=19_000, overshoot=0.0)
    assert trip.state == TripState.VOTING
    flagged = {e.channel for e in trip.events if "heads-up before the vote" in e.text}
    assert flagged == {f"dm:{members[0].id}", f"dm:{members[1].id}"}                # the two from Kolkata
    p = trip.plan()
    assert p.essentials_share() == 1_200                                           # Dudhsagar, trekking shared by two
    assert p.per_head(members[3].id) == 19_000 <= trip.limit() < p.per_head(members[0].id) == 19_400


def test_a_quote_under_the_organisers_limit_but_over_the_payers_own_ceiling_is_flagged_to_them_alone():
    engine, members, trip, _ = make(LEISURE, ceilings={"Riya": 18_000})
    p = trip.plan()
    assert p.per_head(members[3].id) == 19_000 <= trip.limit() == 22_000             # fits the organiser's limit...
    assert trip.quote_limit(members[3].id) == 18_000                                 # ...not her own ceiling
    flagged = [e for e in trip.events if "heads-up before the vote" in e.text]
    assert [e.channel for e in flagged] == [f"dm:{members[3].id}"] and "over the ₹18,000 you gave me" in flagged[0].text
    assert trip.state == TripState.VOTING                                           # one of five over: still goes to the vote
    assert not any("18,000" in t for t in group_posts(trip))


# ------------------------------------------------------------------ vote
def test_private_vote_only_the_tally_is_posted_and_yes_voters_are_in():
    engine, members, trip, _ = make(silent=("Karan",))
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.state == TripState.AUTHORISING
    assert [m.first for m in trip.in_members()] == ["Sayan", "Aabhas", "Aditi", "Riya"]
    assert members[4].id not in trip.authorisations
    posts = group_posts(trip)
    assert len(posts) == 2 and "4 yes, 1 no" in posts[1] and "Karan" not in posts[1]


def test_authorised_amount_is_heads_times_the_payers_own_ceiling_not_rounded_up():
    engine, members, trip, _ = make()
    vote_all(engine, trip, members, no=("Karan",))
    p = trip.plan()
    assert len(p.travellers) == 4 and p.heads() == 5 and p.per_head(members[0].id) == 19_980   # was 18,700 with six heads
    a = trip.authorisations[members[0].id]
    assert a.amount == 24_000 and a.status == AuthStatus.PENDING                   # Sayan's own ceiling, not share × 1.1
    aabhas = trip.authorisations[members[1].id]
    assert aabhas.amount == 44_000 and p.share(members[1].id) == 39_960             # 2 heads × ₹22,000; ₹4,040 headroom
    assert trip.authorisations[members[2].id].amount == 20_000                     # Aditi: her ₹20,000, not the organiser's
    dm = next(e.text for e in trip.events if e.channel == f"dm:{members[1].id}" and "Block ₹44,000" in e.text)
    assert "2 × your ₹22,000 ceiling" in dm and "₹4,040 of headroom" in dm


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
    assert len(p.travellers) == 4 and p.heads() == 5 and p.per_head(members[0].id) == 18_760 > 17_800   # 5 heads, 3 rooms: up, inside caps
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
    # Where a withdrawal does break a cap: leisure, all five in, Riya's ceiling is ₹20,000, Aabhas's two heads
    # leave, then Karan — three heads on two rooms put Riya at ₹21,134, past the ₹20,000 she authorised
    engine, members, trip, _ = make(LEISURE)
    trip.constraints[members[3].id].budget = 20_000
    vote_all(engine, trip, members)
    for m in (members[0], members[2], members[3]):
        engine.member_approves(trip, m.id)
    engine.member_withdraws(trip, members[1].id)
    assert trip.state == TripState.AUTHORISING and trip.plan().heads() == 4
    engine.member_withdraws(trip, members[4].id)
    assert trip.state == TripState.VOTING and trip.plan().version == 2
    assert len(trip.plan().travellers) == 3 and trip.plan().heads() == 3 and not trip.authorisations
    assert any("released — plan going back to the group" in e.text for e in trip.events)
    post = next(t for t in group_posts(trip) if "past what you authorised" in t)
    assert "puts 1 of you" in post and "Riya" not in post                          # who is over stays private


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


def test_the_96h_hold_outlasts_the_vote_and_the_block_windows():
    engine, members, trip, (voice, _, _) = make(silent=("Karan",))
    stay = trip.plan().stays[0]
    assert stay.hold_until == clock.now() + timedelta(hours=96)                     # asked for 96 h on the call
    clock.advance(23); engine.tick(trip)                                           # the vote runs to its last hour...
    vote_all(engine, trip, members, no=("Karan",))
    clock.advance(47); engine.tick(trip)                                           # ...and the block to its last hour
    for m in members[:4]:
        engine.member_approves(trip, m.id)
    assert trip.state == TripState.BOOKED and clock.now() < stay.hold_until
    assert voice.attempts["Dona Maria Homestay, Assagao"] == 2                      # no re-hold needed


def test_expired_hold_is_re_called_before_any_debit():
    engine, members, trip, (voice, _, _) = make(silent=("Karan",), auth_window_h=96)   # the organiser gave a long window
    clock.advance(20); engine.tick(trip)                                           # people vote over a day...
    vote_all(engine, trip, members, no=("Karan",))
    clock.advance(80); engine.tick(trip)                                           # ...and authorise over three
    for m in members[:4]:
        engine.member_approves(trip, m.id)                                         # the 96h hold has lapsed by now
    assert trip.state == TripState.BOOKED
    assert voice.attempts["Dona Maria Homestay, Assagao"] == 3
    assert any("expired" in e.text and "re-hold" in e.text for e in trip.events)


# ------------------------------------------------------------------ after booking
def booked(**ceilings):
    engine, members, trip, rails = make(silent=("Karan",))
    for m in members:
        if m.first in ceilings:
            trip.constraints[m.id].budget = ceilings[m.first]
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
    engine, members, trip, (_, _, logistics) = booked(Riya=21_500)               # cap ₹21,500: ₹2,020 of headroom
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


# ------------------------------------------------------------------ every payer has their own limit; the pool pays
def test_each_share_is_checked_against_its_payers_own_ceiling():
    engine, members, trip, _ = make(silent=("Karan",))
    assert trip.ceiling(members[2].id) == 20_000                                    # stated
    assert trip.ceiling(members[4].id) == 22_000 == trip.limit()                   # silent: the organiser's ₹20,000 + 10%
    assert trip.quote_limit(members[2].id) == 20_000 and trip.quote_limit(members[0].id) == 22_000   # min(limit, own)
    assert not trip.scoped                                                          # a proposal: nobody is in yet
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.scoped
    p = trip.plan()
    assert len(p.travellers) == 4 and p.heads() == 5 and p.total() == 93_400
    assert p.per_head(members[0].id) == 19_980 and p.share(members[1].id) == 39_960   # 3 rooms among 5, not 6
    assert all(p.share(t) <= trip.cap(t) for t in p.travellers)
    post = group_posts(trip)[-1]
    assert "every share fits the ceiling its payer gave me" in post and "4 paying for 5" in post and "comes to ₹93,400" in post


def test_one_payer_over_their_own_ceiling_sends_the_plan_back_and_is_never_averaged_or_named():
    engine, members, trip, _ = make(silent=("Karan",))
    trip.constraints[members[2].id].budget = 13_000        # Aditi: ₹12,700 fits among 6; ₹13,980 among 5 does not
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.state == TripState.VOTING and trip.plan().version == 2
    assert trip.plan().note == "same stay, one night fewer" and len(trip.plan().travellers) == 4
    assert trip.plan().share(members[2].id) == 12_060 <= trip.cap(members[2].id) == 13_000
    post = next(e.text for e in trip.events if e.channel == "group" and "over your own ceiling" in e.text)
    assert "puts 1 of you" in post and "Aditi" not in post and "₹13,000" not in post   # the group never learns whose
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
    engine, members, trip, (_, _, logistics) = booked(Riya=21_500)
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


def test_asking_for_time_gets_the_facts_and_no_nudge_but_the_deadline_stands():
    engine, members, trip, _ = make(LEISURE)
    karan = members[4]
    vote_all(engine, trip, members, skip=("Karan",))
    n = len(trip.events)
    engine.member_takes_time(trip, karan.id, "Need to check with work and my bank, give me a day?")
    ack = [e.text for e in trip.events if e.channel == f"dm:{karan.id}" and e.actor == "agent"][-1]
    assert "The deadline stays" in ack and "nothing is charged by a yes" in ack and "won't nudge you again" in ack
    clock.advance(12); engine.tick(trip)
    assert not any("a nudge" in e.text for e in trip.events if e.channel == f"dm:{karan.id}")
    assert not any(e.priority == "P0" for e in trip.events[n:])                      # never a call to chase
    clock.advance(12); engine.tick(trip)
    assert trip.state == TripState.AUTHORISING and karan.id not in [m.id for m in trip.in_members()]
    assert "4 yes, 0 no, 1 didn't reply" in group_posts(trip)[-2] or "1 didn't reply" in " ".join(group_posts(trip))
    aditi = members[3]
    engine.member_takes_time(trip, aditi.id)
    clock.advance(24); engine.tick(trip)
    assert not any("still waiting" in e.text for e in trip.events if e.channel == f"dm:{aditi.id}")
    engine.member_approves(trip, aditi.id)                                            # time taken, then a yes
    assert trip.authorisations[aditi.id].status.value == "BLOCKED"
