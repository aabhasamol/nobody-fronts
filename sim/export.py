"""Turn a run directory into what the Round 3 form asks for.

    python -m sim.export sim/runs/<stamp>

decisions.md     Q2: every decision, in order — when, received, from, decided, why, did/said to whom, through
rail_calls.md    Q4: every Pine Labs / Delhivery / Gnani call with endpoint, request and response
transcript.md    every message the agent sent, in order (for the mockups and the story)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def _rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _cell(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", "<br>")


def write(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    d = _rows(run_dir / "decisions.jsonl")
    out = ["# Decisions, in order\n",
           "| # | When | What the agent received | Where it came from | What it decided | Why (rule) | What it did or said, to whom | Through |",
           "|---|---|---|---|---|---|---|---|"]
    for r in d:
        out.append(f"| {r['n']} | {_cell(r['when'])} | {_cell(r['received'])} | {_cell(r['from'])} | {_cell(r['decided'])} | "
                   f"{_cell(r['why'])} | {_cell(r['action'])} → {_cell(r['to'])}: {_cell(r['did_or_said'])} | {_cell(r['through'])} |")
    (run_dir / "decisions.md").write_text("\n".join(out) + "\n", encoding="utf-8")

    rc = _rows(run_dir / "rail_calls.jsonl")
    out = ["# Rail calls (every call to Pine Labs, Delhivery, Gnani and the world)\n"]
    for r in rc:
        out += [f"## {r['n']}. {r['partner']} — `{r['endpoint']}`", f"*{r['at']} · played by: {r['played_by']} · status {r['status']}*\n",
                "Request:", "```json", json.dumps(r.get("request"), indent=2, ensure_ascii=False), "```",
                "Response:", "```json", json.dumps(r.get("response"), indent=2, ensure_ascii=False), "```\n"]
    (run_dir / "rail_calls.md").write_text("\n".join(out), encoding="utf-8")

    msgs = _rows(run_dir / "messages.jsonl")
    out = ["# Messages the agent sent\n"]
    for m in msgs:
        out += [f"**{m['at']} → {m['to']}**", "", m["text"], ""]
    (run_dir / "transcript.md").write_text("\n".join(out), encoding="utf-8")
    return run_dir


if __name__ == "__main__":
    print(write(Path(sys.argv[1])))
