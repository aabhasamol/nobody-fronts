"""Check a run against the rules before anyone else does.

    python -m sim.audit sim/runs/<stamp>        (also runs on every /export and /quit)

Writes audit.md: one line per check, PASS / WARN / FAIL, with the evidence. A FAIL means the recording would show
something our own rules or Pine Labs' documented behaviour forbid; fix it (or re-run) before submitting.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

P0 = {"SUPPLIER_AVAILABILITY", "LATE_ARRIVAL", "DISRUPTION_UNANSWERED"}
AMOUNT = re.compile(r"(₹|Rs\.?\s?)\s?\d")
NEGATIVE = re.compile(r"\b(voted no|said no|declined|didn't vote|did not vote|over (?:their|his|her|your) ceiling|can't afford|dropped out)\b", re.I)


def _rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _data(resp) -> dict:
    return resp.get("data", resp) if isinstance(resp, dict) else {}


def _paisa(obj, *keys) -> int:
    for k in keys:
        v = (obj or {}).get(k)
        if isinstance(v, dict) and "value" in v:
            return int(v["value"])
    return 0


def audit(run_dir: Path) -> list[tuple[str, str, str]]:
    run_dir = Path(run_dir)
    rails = _rows(run_dir / "rail_calls.jsonl")
    decisions = _rows(run_dir / "decisions.jsonl")
    messages = _rows(run_dir / "messages.jsonl")
    roster = json.loads((run_dir / "roster.json").read_text(encoding="utf-8")) if (run_dir / "roster.json").exists() else []
    out: list[tuple[str, str, str]] = []

    def check(ok: bool, name: str, detail: str, warn: bool = False) -> None:
        out.append(("PASS" if ok else ("WARN" if warn else "FAIL"), name, detail))

    # 1. nothing arrives that the agent did not ask for
    stray = [r["n"] for r in rails if r.get("initiated_by") not in ("agent", "world")
             or (r.get("initiated_by") == "world" and not r.get("in_reply_to"))]
    check(not stray, "Every rail response answers a call the agent made",
          "all responses are replies to agent calls or webhooks for objects the agent created" if not stray
          else f"rail rows without an agent request: {stray}")

    # 2-6. money, replayed in order
    caps: dict[str, int] = {}                 # subscription_id → cap (paisa)
    sub_order: dict[str, str] = {}            # subscription_id → order_id
    debited: dict[str, int] = {}              # order_id → paisa taken (mandate debits and card captures)
    refunded: dict[str, int] = {}
    debits_per_sub: dict[str, int] = {}
    pool, over_cap, double, payout_over, payouts = 0, [], [], [], []
    failure_at, payout_after_failure = None, []
    for r in rails:
        if r.get("partner") != "Pine Labs" or r.get("status", 500) >= 400 and "presentations" not in r.get("endpoint", "") \
                and "capture" not in r.get("endpoint", ""):
            continue
        ep, req, resp, st = r["endpoint"], (r.get("request") or {}).get("body") or {}, r.get("response"), r.get("status", 500)
        d = _data(resp)
        if ep.endswith("/subscriptions/ot") and st < 400:
            caps[d.get("subscription_id")] = int((d.get("plan_details") or {}).get("amount", 0))
            sub_order[d.get("subscription_id")] = d.get("order_id")
        elif ep.endswith("/public/presentations") and ep.startswith("POST"):
            sid = req.get("subscription_id")
            ok = st < 400 and str(d.get("status", "")).upper() in ("SUCCESS", "PROCESSED")
            if ok:
                amt = _paisa(d, "amount")
                debits_per_sub[sid] = debits_per_sub.get(sid, 0) + 1
                if debits_per_sub[sid] > 1:
                    double.append(sid)
                if sid in caps and amt > caps[sid]:
                    over_cap.append(f"{sid}: {amt} > cap {caps[sid]}")
                oid = sub_order.get(sid, sid)
                debited[oid] = debited.get(oid, 0) + amt
                pool += amt
            elif failure_at is None:
                failure_at = r["n"]
        elif "/capture" in ep:
            oid = ep.split("/orders/")[1].split("/")[0]
            if st < 400 and str(d.get("status", "")).upper() == "PROCESSED":
                amt = _paisa(d, "captured_amount")
                debited[oid] = debited.get(oid, 0) + amt
                pool += amt
            elif failure_at is None:
                failure_at = r["n"]
        elif "/refunds/" in ep and st < 400:
            oid = ep.split("/refunds/")[1]
            amt = _paisa(req, "order_amount")
            refunded[oid] = refunded.get(oid, 0) + amt
            pool -= amt
        elif ep == "POST /payouts/v3/payments" and st < 400:
            amt = _paisa(req, "amount")
            if amt > pool:
                payout_over.append(f"row {r['n']}: payout {amt} with {pool} in the pool")
            if failure_at is not None:
                payout_after_failure.append(r["n"])
            pool -= amt
            payouts.append(amt)
    check(not over_cap, "No debit above the payer's mandate cap", "every debit ≤ its cap" if not over_cap else "; ".join(over_cap))
    check(not double, "At most one debit per one-time mandate", "one debit each" if not double else f"repeated: {double}")
    check(not payout_over, "No payout above what the pool holds", "the pool never went negative" if not payout_over else "; ".join(payout_over))
    if failure_at is not None:
        unrefunded = {o: debited[o] - refunded.get(o, 0) for o in debited if debited[o] - refunded.get(o, 0) > 0}
        check(not unrefunded and not payout_after_failure, "A failed debit rolled everyone back (R18)",
              "every earlier debit refunded and no supplier paid" if not unrefunded and not payout_after_failure
              else f"still held: {unrefunded}; payouts after the failure: {payout_after_failure}")
    if payouts or debited:
        check(pool == 0, "The pool ends at ₹0", f"pool ends at ₹{pool / 100:,.0f}" if pool else "every rupee in went out to a supplier or back to a payer",
              warn=True)

    # 7. calls are P0 only
    calls = [r for r in rails if r.get("partner") == "Phone line"]
    bad = [r["n"] for r in calls if (r.get("request") or {}).get("p0_reason") not in P0]
    check(not bad, "Every call is for a P0 reason", f"{len(calls)} call(s), all P0" if not bad else f"non-P0 calls at rows {bad}")

    # 8. Gnani output is real
    gnani = [r for r in rails if r.get("partner") == "Gnani"]
    fake = [r["n"] for r in gnani if "real" not in str(r.get("played_by", ""))]
    if gnani:
        check(not fake, "Gnani responses come from Gnani's API", f"{len(gnani)} Gnani call(s), all real" if not fake else f"rows {fake}")

    # 9. every decision cites a rule
    norule = [d["n"] for d in decisions if not re.search(r"R\d+", str(d.get("why", "")))]
    check(not norule, "Every decision cites a rule from the system prompt",
          f"{len(decisions)} decisions, all with a rule id" if not norule else f"decisions without a rule: {norule}")

    # 10. privacy in the group
    group = [m for m in messages if m.get("to") == "group"]
    names = [n.split()[0] for n in roster]
    leaks = [g["text"][:80] for g in group if NEGATIVE.search(g["text"]) and any(n in g["text"] for n in names)]
    check(not leaks, "No one's vote, dropout or ceiling is named in the group",
          f"{len(group)} group post(s) checked" if not leaks else f"posts to review: {leaks}", warn=True)
    amounts = [g["text"][:80] for g in group[1:] if AMOUNT.search(g["text"])]
    check(not amounts, "No amounts in group posts after the kickoff", "none" if not amounts else f"posts to review: {amounts}", warn=True)
    return out


def write(run_dir: Path) -> Path:
    rows = audit(run_dir)
    lines = ["# Run audit\n", "| Result | Check | Evidence |", "|---|---|---|"]
    lines += [f"| {s} | {c} | {d.replace('|', '/')} |" for s, c, d in rows]
    fails = sum(1 for s, _, _ in rows if s == "FAIL")
    lines.append(f"\n{fails} failure(s), {sum(1 for s, _, _ in rows if s == 'WARN')} warning(s).")
    path = Path(run_dir) / "audit.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    p = write(Path(sys.argv[1]))
    print(p.read_text(encoding="utf-8"))
