"""The demo cast: five friends, Goa in October. Edit freely."""
from datetime import date
from .models import Member


def five_friends() -> list[Member]:
    return [
        Member(name="Sayan Das", phone="9830011111", home_city="Kolkata", past_trips=["Darjeeling 2025", "Puri 2024"]),
        Member(name="Aabhas Amol", phone="9830022222", home_city="Kolkata", past_trips=["Hampta Pass 2025", "Singapore 2024"]),
        Member(name="Aditi Rao", phone="9830033333", home_city="Bengaluru", past_trips=["Gokarna 2025"]),
        Member(name="Riya Sen", phone="9830044444", home_city="Delhi", past_trips=["Manali 2024"]),
        Member(name="Karan Mehta", phone="9830055555", home_city="Mumbai", past_trips=[]),
    ]


# Two ways to run the same five people. The occasion changes what the agent optimises and which stays it
# ranks first; the budget and overshoot are the organiser's two numbers.
WEDDING = dict(name="Priya's wedding, Goa", destination="Goa", venue="Assagao", start=date(2026, 10, 2),
               end=date(2026, 10, 6), occasion="wedding", budget=20000, overshoot=0.10)
LEISURE = dict(name="Goa, October", destination="Goa", venue="", start=date(2026, 10, 2),
               end=date(2026, 10, 6), occasion="leisure", budget=20000, overshoot=0.10)
TRIP = WEDDING

# Canned gathering replies, one per member, so a demo is one click per person.
REPLIES = {
    "Sayan":  dict(start_city="Kolkata", return_city="Kolkata", must_haves=["no hostels"],
                   text="Kolkata both ways. Dates fine. No hostels please."),
    "Aabhas": dict(start_city="Kolkata", return_city="Kolkata", must_haves=[],
                   text="Kolkata and back. All dates work."),
    "Aditi":  dict(start_city="Bengaluru", return_city="Mumbai", must_haves=["no hostels"],
                   text="I'm in Bengaluru but flying back to Mumbai after — work. Not a hostel."),
    "Riya":   dict(start_city="Delhi", return_city="Delhi", must_haves=[],
                   text="Delhi both ways. Nothing fancy."),
    "Karan":  dict(start_city="Mumbai", return_city="Mumbai", must_haves=[],
                   text="Mumbai. ok."),
}
