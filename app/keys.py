"""Keys committed on purpose.

This repository is private, lives for the month of the competition, and one person holds it. The Gnani
speech key below is scoped to text-to-speech and speech-to-text only (it cannot place calls or spend
anything but metered speech credit), and every call through it is counted and capped by `CallBudget`
(GNANI_CALL_BUDGET, default 40 billable calls per checkout). Revoke it on the Gnani dashboard when the
competition ends. An environment variable of the same name overrides it.
"""
GNANI_SPEECH_KEY = "vach_1ytE2CY5X2Dwn8OVPAB0fdu5W59Hs17luOJDVIrR0rXqzsnJDy73cEZeku3L3ZUNaNy3HlQXhCQb5esMZ5WQ62TCagR3R6RT_07b15cb2221b0f87ae81f7f5cee2edef"
