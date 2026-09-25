"""What makes people say yes: the place, not the price.

Every nudge the agent sends carries one hook about the destination — a film that was shot there, the road from
every reel, the week's weather, the quiet beach for the parents — chosen for the person (their interests, the
group's persona) and never repeated to the same person. Hooks are P2 texts; they ride along with a message
that had to be sent anyway. This is content, not a rail: replace the table with a real source (a destination
guide, a reels feed) and nothing else changes.
"""
from __future__ import annotations
from typing import Optional

INTERESTS = ["trekking", "water sports", "heritage", "nightlife", "food", "quiet beaches", "reels spots", "wildlife"]

HOOKS: dict[str, list[dict]] = {
    "Goa": [
        dict(tag="film", text="Chapora Fort is the Dil Chahta Hai fort. The sunset scene is a 15-minute walk from Vagator."),
        dict(tag="reel", interest="reels spots", text="The palm-lined Parra road from every second reel is a 10-minute scooter ride from Assagao."),
        dict(tag="season", text="Early October: the monsoon greens are still on, Dudhsagar is at full flow, and the crowds aren't here yet."),
        dict(tag="food", interest="food", text="Thursday is the Anjuna flea, Saturday night the Arpora market, and the ros omelette carts open at 7 pm."),
        dict(tag="family", persona="families", text="Morjim and Ashwem are the quiet beaches: shallow, lifeguarded, turtle nesting in season."),
        dict(tag="family", persona="families", text="Old Goa's churches and the spice farms at Ponda are a slow, shaded day for the parents."),
        dict(tag="bachelors", persona="bachelors", text="Curlies to Hilltop is one night. The Arambol drum circle at sunset is another."),
        dict(tag="secret", text="Fontainhas at 8 am: the Latin quarter is empty, painted, and 20 minutes off the airport road."),
        dict(tag="trek", interest="trekking", text="The rail-track walk to Dudhsagar is banned now; the jeep-and-hike from Collem is the legal way, and it's worth it."),
        dict(tag="water", interest="water sports", text="Palolem's kayaks at sunrise are ₹300 an hour and there's nobody else on the water."),
        dict(tag="heritage", interest="heritage", text="Reis Magos is the fort nobody visits, and it has the best view of the Mandovi."),
        dict(tag="night", interest="nightlife", text="Vagator's clubs run past 3 on Saturdays; the cab back to Assagao is ₹250 if you book it before midnight."),
        dict(tag="quiet", interest="quiet beaches", text="Galgibaga in the south is turtle beach: no shacks, no music, one café."),
        dict(tag="wild", interest="wildlife", text="Salim Ali bird sanctuary at Chorao opens at 6; the mangrove boat is ₹150 a head."),
        dict(tag="reel", interest="reels spots", text="Butterfly Beach is the reel spot with no road; a ₹500 boat from Palolem gets you there for sunset."),
    ],
}


def persona_of(parties: list[int], occasion: str) -> str:
    """Families when anyone pays for three or more, or the occasion says so; bachelors otherwise."""
    if occasion in ("family", "pilgrimage") or any(p >= 3 for p in parties):
        return "families"
    return "bachelors"


def hook(destination: str, persona: str, interests: list[str], used: list[str]) -> Optional[str]:
    """One line for this person: their interests first, then the group's persona, then the general ones, then
    other interests as a last resort. Never another persona's line, never a repeat."""
    pool = [h for h in HOOKS.get(destination, []) if h["text"] not in used and h.get("persona", persona) == persona]
    for pick in (lambda h: h.get("interest") in interests,
                 lambda h: h.get("persona") == persona,
                 lambda h: "interest" not in h and "persona" not in h,
                 lambda h: True):
        for h in pool:
            if pick(h):
                used.append(h["text"])
                return h["text"]
    return None
