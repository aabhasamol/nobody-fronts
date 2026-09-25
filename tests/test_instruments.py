"""How a member blocks their share: UPI Reserve Pay keeps headroom live; a card hold or a UPI one-time mandate
takes one capture; EMI is a card choice settled to Quorum in full; nobody can revoke from their own app."""
import pytest
from app.clock import clock
from app.models import TripState, AuthStatus, INSTRUMENTS
from tests.test_engine import make, vote_all, group_posts


def authorising(**kw):
    engine, members, trip, rails = make(silent=("Karan",), **kw)
    vote_all(engine, trip, members, no=("Karan",))
    assert trip.state == TripState.AUTHORISING
    return engine, members, trip, rails


def test_the_member_chooses_the_instrument_and_a_card_hold_lives_seven_days():
    engine, members, trip, _ = authorising()
    sayan, aabhas, aditi, riya = members[:4]
    engine.member_approves(trip, sayan.id, "UPI_RESERVE")
    engine.member_approves(trip, aabhas.id, "CARD_PREAUTH", emi_months=3)
    engine.member_approves(trip, riya.id, "CARD_PREAUTH")
    a = trip.authorisations
    assert a[sayan.id].instrument == "UPI_RESERVE" and a[sayan.id].multi_debit
    assert a[aabhas.id].instrument == "CARD_PREAUTH" and a[aabhas.id].emi_months == 3 and not a[aabhas.id].multi_debit
    assert a[riya.id].expires_at <= clock.now().replace(hour=23) + __import__("datetime").timedelta(days=7)
    assert "credit card hold, 3-month EMI at capture" in "\n".join(e.text for e in trip.events if e.channel == "rail:payments")
    assert any("UPI Reserve Pay" in e.text and "nothing charged" in e.text for e in trip.events if e.channel == f"dm:{sayan.id}")


def test_dm_offers_upi_or_card_with_emi_tenures():
    engine, members, trip, (_, payments, _) = authorising()
    dm = next(e.text for e in reversed(trip.events) if e.channel == f"dm:{members[0].id}" and "Block ₹" in e.text)
    assert "UPI mandate: approve the request" in dm and "Payment link: pay with what you like" in dm
    assert "Anything that can't hold pays now" in dm and "refunded in full if the trip doesn't happen" in dm
    months, monthly = payments.emi_offers(22_000)[0]
    assert months == 3 and f"₹{monthly:,} × 3" in dm


def test_card_hold_takes_one_capture_and_keeps_no_headroom_upi_reserve_does():
    engine, members, trip, (_, payments, _) = authorising()
    sayan, aabhas, aditi, riya = members[:4]
    for m, via in ((sayan, "UPI_RESERVE"), (aabhas, "UPI_RESERVE"), (aditi, "UPI_RESERVE"), (riya, "CARD_PREAUTH")):
        engine.member_approves(trip, m.id, via)
    assert trip.state == TripState.BOOKED
    assert trip.authorisations[sayan.id].headroom() == 2_020                     # cap 22,000 − share 19,980, still live
    assert trip.authorisations[riya.id].headroom() == 0                          # one capture; the rest was released
    assert trip.authorisations[riya.id].captured_amount == 19_480 and payments.pool() == 0
    rails = "\n".join(e.text for e in trip.events if e.channel == "rail:payments")
    assert "remaining ₹2,020 of the hold is released — one capture per credit card hold" in rails
    assert "₹2,020 headroom stays live for a re-booking" in rails


def test_emi_is_settled_to_quorum_in_full_and_shown_on_the_receipt():
    engine, members, trip, (_, payments, _) = authorising()
    for m, via, emi in ((members[0], "UPI_RESERVE", None), (members[1], "CARD_PREAUTH", 3),
                        (members[2], "UPI_RESERVE", None), (members[3], "UPI_RESERVE", None)):
        engine.member_approves(trip, m.id, via, emi_months=emi)
    assert trip.state == TripState.BOOKED
    assert trip.authorisations[members[1].id].captured_amount == 39_960          # the whole share for two, into the pool
    assert sum(x.amount for x in trip.payouts) == 93_400 and payments.pool() == 0
    receipt = next(e.text for e in trip.events if e.channel == f"dm:{members[1].id}" and "Your confirmations" in e.text)
    assert "your issuer collects it as 3 monthly instalments" in receipt


def test_card_member_rebook_within_the_refund_needs_no_tap_beyond_it_needs_one():
    engine, members, trip, (_, payments, logistics) = authorising()
    sayan, aabhas, aditi, riya = members[:4]
    for m, via in ((sayan, "UPI_RESERVE"), (aabhas, "CARD_PREAUTH"), (aditi, "UPI_RESERVE"), (riya, "CARD_PREAUTH")):
        engine.member_approves(trip, m.id, via)
    p = trip.plan()
    # Aabhas: the ₹12,800 refund for two seats covers the ₹11,800 re-book — no approval, even on a card
    engine.disrupt(trip, next(l for l in p.legs_for(aabhas.id) if l.destination == "Goa").id)
    assert aabhas.id not in trip.pending and payments.pool() == 1_000
    # Riya: every alternative is over the refund and her hold has no headroom — text, then a fresh card tap
    logistics.drift["Delhi"] = 1.6
    engine.disrupt(trip, next(l for l in p.legs_for(riya.id) if l.destination == "Goa").id)
    d = trip.pending[riya.id]
    assert d.kind == "REBOOK" and d.shortfall == 9_120 - 6_100                  # nothing from the hold
    dm = next(e.text for e in reversed(trip.events) if e.channel == f"dm:{riya.id}")
    assert "Your card hold was used up at booking" in dm
    engine.member_chooses(trip, riya.id, 1)
    top = trip.top_ups[riya.id]
    assert top.amount == 3_100 and "tap the card link to authorise ₹3,100" in trip.events[-1].text
    engine.member_approves(trip, riya.id, "CARD_PREAUTH")
    assert top.captured_amount == 3_020 and trip.authorisations[riya.id].captured_amount == 19_480
    assert payments.pool() == 1_000                                              # refund 6,100 + top-up 3,020 − 9,120, plus Aabhas's ₹1,000


def test_upi_mandates_cap_at_one_lakh_and_emi_is_a_card_thing():
    engine, members, trip, (_, payments, _) = authorising()
    a = trip.authorisations[members[0].id]
    with pytest.raises(AssertionError, match="EMI is a card thing"):
        payments.approve(a, "UPI_RESERVE", emi_months=3)
    a.amount = 120_000
    with pytest.raises(AssertionError, match="at most ₹1,00,000"):
        payments.approve(a, "UPI_OTM")
    assert INSTRUMENTS["UPI_OTM"]["max_validity_days"] == 60 and INSTRUMENTS["CARD_PREAUTH"]["max_validity_days"] == 7


def test_nobody_revokes_from_their_own_app_they_ask_quorum():
    engine, members, trip, (_, payments, _) = authorising()
    assert not hasattr(payments, "revoke")
    engine.member_approves(trip, members[3].id, "CARD_PREAUTH")
    engine.member_withdraws(trip, members[3].id, "Something's come up, please release my hold.")
    rails = [e.text for e in trip.events if e.channel == "rail:payments"]
    assert any("released — Riya is out, nothing charged" in t for t in rails)      # Quorum released it, not her app
    assert any("asked to be let out" in e.text for e in trip.events if e.channel == f"dm:{members[3].id}")
    assert trip.authorisations[members[3].id].status == AuthStatus.RELEASED and members[3].id in trip.dropped
    assert trip.state == TripState.AUTHORISING and trip.plan().heads() == 4       # the other three carry on


def test_prepaid_link_money_sits_in_the_pool_and_comes_back_if_the_trip_lapses():
    engine, members, trip, (_, payments, _) = authorising()
    sayan, aabhas, aditi, riya = members[:4]
    engine.member_approves(trip, aditi.id, "PREPAID")                             # a method that can't hold: paid now
    a = trip.authorisations[aditi.id]
    assert a.prepaid and a.status == AuthStatus.BLOCKED and payments.pool() == 15_400
    assert any("Received ₹15,400 via the payment link, paid now" in e.text for e in trip.events if e.channel == f"dm:{aditi.id}")
    for m, via in ((sayan, "UPI_RESERVE"), (aabhas, "CARD_PREAUTH"), (riya, "UPI_RESERVE")):
        engine.member_approves(trip, m.id, via)
    assert trip.state == TripState.BOOKED
    assert a.captured_amount == 13_980 and a.headroom() == 1_420                  # her prepayment's spare stays as cash
    assert payments.pool() == 1_420                                               # everything else paid out to the rupee
    rails = "\n".join(e.text for e in trip.events if e.channel == "rail:payments")
    assert "it was already in the pool" in rails and "(idea) Float:" in rails and "Quorum's to keep and Quorum's to lose" in rails
    assert "₹1,420 left of your prepayment comes back after the trip" in "\n".join(e.text for e in trip.events if e.channel == f"dm:{aditi.id}")
    # and a prepayment released before booking is refunded in full
    engine, members, trip, (_, payments, _) = authorising()
    engine.member_approves(trip, members[2].id, "PREPAID")
    assert payments.pool() == 15_400
    engine.member_withdraws(trip, members[2].id)
    assert payments.pool() == 0 and trip.authorisations[members[2].id].status == AuthStatus.RELEASED
