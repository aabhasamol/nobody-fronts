from pathlib import Path
import pytest
from app.rails.gnani_speech import CallBudget, BudgetExceeded, estimate


def test_budget_refuses_past_cap(tmp_path: Path):
    b = CallBudget(cap=2, path=tmp_path / "b.json")
    b.charge("tts", 400, "q1"); b.charge("stt", 90, "reply")
    assert b.remaining == 0
    with pytest.raises(BudgetExceeded):
        b.charge("tts", 10)
    b2 = CallBudget(cap=2, path=tmp_path / "b.json")          # persisted across processes
    assert b2.state["used"] == 2 and b2.state["spent_inr"] == pytest.approx(400 * 27 / 10000 + 90 * 27 / 3600, abs=1e-3)


def test_cost_arithmetic():
    assert estimate("tts", 10_000) == pytest.approx(27.0)
    assert estimate("stt", 3600) == pytest.approx(27.0)
    assert estimate("tts", 400) + estimate("stt", 120) < 2.0     # one full hotel verification ≈ ₹2
