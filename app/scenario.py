"""The demo cast: five friends, one week in Goa. Edit freely."""
from datetime import date
from .models import Member


def five_friends() -> list[Member]:
    return [
        Member(name="Sayan Das", phone="9830011111", home_city="Kolkata", spend_ceiling=40000,
               past_trips=["Darjeeling 2025", "Puri 2024"]),
        Member(name="Aabhas Amol", phone="9830022222", home_city="Kolkata", spend_ceiling=45000,
               past_trips=["Hampta Pass 2025", "Singapore 2024"]),
        Member(name="Aditi Rao", phone="9830033333", home_city="Bengaluru", spend_ceiling=35000,
               past_trips=["Gokarna 2025"]),
        Member(name="Riya Sen", phone="9830044444", home_city="Delhi", spend_ceiling=30000,
               past_trips=["Manali 2024"]),
        Member(name="Karan Mehta", phone="9830055555", home_city="Mumbai", spend_ceiling=25000,
               past_trips=[]),
    ]


TRIP = dict(name="Goa, October", destination="Goa", start=date(2026, 10, 2), end=date(2026, 10, 6),
            rough_budget=20000, quorum=4)
