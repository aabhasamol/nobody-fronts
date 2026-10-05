"""Quorum, Round 3: the agent is a Claude model following sim/system_prompt.md; it makes every decision.

    python -m sim.agent                      # console channel, curtain plays Pine Labs/Delhivery/fares
    SIM_CHANNEL=telegram python -m sim.agent # real group + DMs through a Telegram bot
    python -m sim.agent --script sim/scenario/goa_wedding.txt   # feed the operator's lines from a file
    python -m sim.agent --config sim/config.darjeeling.json --script sim/scenario/darjeeling_stay_only.txt

The operator ("world>" prompt) only feeds in the world. Commands:
    @Name: text            a DM from Name           (console channel; on Telegram people type on their phones)
    @Name group: text      Name posts in the group
    /advance 24h           the clock moves on (30m, 6h, 1d)
    /approve Name [UPI|CARD]  Name approves their mandate (or card hold) in their own app; Pine Labs' webhook follows.
                           Refused unless the agent created it, registered it, and DMed Name the approval link.
    /fail-debit Name       Name's bank will decline the next debit (to show the refund-everyone path)
    /event SOURCE | TEXT   anything else from a real source (e.g. "IndiGo SMS | 6E 523 on 20 Nov is cancelled")
    /status                the operator's ledger (mandates, card holds, pool, payouts) — never shown to the agent
    /export                write the decision table, rail-call log and transcript as Markdown
    /quit
    (empty line)           check the channel for new messages
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.world import SimClock, RunLog, Curtain, banner, paint, C_EVENT, C_THINK, C_WARN, C_DIM, C_RESP  # noqa: E402
from sim.channels import build_channel, Channel  # noqa: E402
from sim.tools import TOOLS, Toolbox  # noqa: E402
from sim import export  # noqa: E402

HERE = Path(__file__).resolve().parent
MODEL = os.environ.get("QUORUM_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("QUORUM_EFFORT", "high")
MAX_STEPS = 40                      # model turns per event before the harness stops the turn (a runaway guard)


def build_system(cfg: dict) -> str:
    """The rules, then day-one knowledge: the roster and the place hooks. Stable text, so it caches."""
    rules = (HERE / "system_prompt.md").read_text(encoding="utf-8")
    roster = [{k: m[k] for k in ("id", "name", "phone") if k in m} | ({"organiser": True} if m["id"] == cfg["organiser_id"] else {})
              for m in cfg["members"]]
    from app.lore import HOOKS
    hooks = {d: [h["text"] for h in HOOKS.get(d, [])] for d in cfg.get("hooks_for", [])}
    day_one = {"group": cfg["group_title"], "members": roster, "place_hooks": hooks,
               "note": "Place hooks come from destination guides the team compiled; use one only if it is true for the trip dates."}
    return rules + "\n```json\n" + json.dumps(day_one, indent=1, ensure_ascii=False) + "\n```\n"


class Agent:
    def __init__(self, client: Any, toolbox: Toolbox, system: str, log: RunLog, clock: SimClock,
                 model: str = MODEL, effort: str = EFFORT):
        self.client, self.toolbox, self.system, self.log, self.clock = client, toolbox, system, log, clock
        self.model, self.effort = model, effort
        self.messages: list[dict] = []
        self.n_event = 0
        self.fallbacks = os.environ.get("SIM_FALLBACKS", "1") == "1"

    def _create(self):
        kw: dict[str, Any] = dict(
            model=self.model, max_tokens=16000,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS, messages=self.messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
        )
        if self.fallbacks:
            kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        return self.client.beta.messages.create(**kw)

    def handle(self, via: str, source: str, text: str, who: str = "") -> None:
        """One event from the world. The model decides what to do until it has nothing left to do."""
        self.n_event += 1
        stamp = self.clock.stamp()
        header = f"[EVENT {self.n_event}] {stamp}\nvia: {via}" + (f" · from: {who}" if who else "") + f" · real source: {source}"
        body = f"{header}\n{text}"
        banner(f"EVENT {self.n_event} · {via}" + (f" · {who}" if who else ""), colour=C_EVENT)
        print(paint(C_EVENT, text))
        self.toolbox.current_event = {"summary": (f"{who}: " if who else "") + text[:300], "source": f"{via} · {source}"}
        self.log.event({"n": self.n_event, "at": stamp, "via": via, "source": source, "who": who, "text": text})
        self.messages.append({"role": "user", "content": body})

        for _ in range(MAX_STEPS):
            resp = self._create()
            u = getattr(resp, "usage", None)
            self.log.model({"event": self.n_event, "model": getattr(resp, "model", self.model), "stop_reason": resp.stop_reason,
                            "input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None),
                            "cache_read": getattr(u, "cache_read_input_tokens", None)})
            self.messages.append({"role": "assistant", "content": resp.content})
            for b in resp.content:
                if b.type == "thinking" and getattr(b, "thinking", ""):
                    print(paint(C_THINK, "… " + b.thinking.strip().replace("\n", "\n  ")))
                elif b.type == "text" and b.text.strip():
                    print(paint(C_DIM, "(agent note) " + b.text.strip()))
                elif b.type == "fallback":
                    print(paint(C_WARN, f"(served by fallback model {getattr(getattr(b, 'to', None), 'model', '?')})"))
            if resp.stop_reason == "refusal":
                print(paint(C_WARN, f"model declined this turn: {getattr(resp, 'stop_details', None)}"))
                return
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                if resp.stop_reason == "max_tokens":
                    print(paint(C_WARN, "hit max_tokens without a tool call; the turn ends here"))
                return
            results = []
            for b in uses:
                result, is_error = self.toolbox.run(b.name, b.input)
                content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                block = {"type": "tool_result", "tool_use_id": b.id, "content": content}
                if is_error:
                    block["is_error"] = True
                    print(paint(C_WARN, f"tool error ({b.name}): {content}"))
                results.append(block)
            self.messages.append({"role": "user", "content": results})        # all results in one message
        print(paint(C_WARN, f"stopped after {MAX_STEPS} model turns on one event"))


# ------------------------------------------------------------------ the operator's console
def find_member(cfg: dict, name: str) -> Optional[dict]:
    name = name.strip().lower()
    for m in cfg["members"]:
        if m["id"].lower() == name or m["name"].lower() == name or m["name"].split()[0].lower() == name:
            return m
    return None


def approve(toolbox: Toolbox, member: dict, method: str) -> tuple[Optional[dict], str]:
    """A member approves in their own app. Only possible for something the agent created and whose approval link the
    agent actually sent that member; otherwise nothing happens and the operator is told why."""
    from sim.pinelabs_world import Mandate
    item = toolbox.ledger.latest_for(member["id"])
    if item is None:
        return None, f"the agent has not created a mandate or card hold for {member['name']}"
    link = item.challenge_url if isinstance(item, Mandate) else item.url
    if not link:
        return None, f"the agent has not registered {member['name']}'s mandate, so there is no approval link"
    if not any(link in text for text in toolbox.dms.get(member["id"], [])):
        return None, f"the agent never sent {member['name']} the approval link, so they cannot approve it"
    payload, why = toolbox.ledger.approve(member["id"], method)
    if payload is None:
        return None, why
    created_by = toolbox.pinelabs.requests.get(getattr(item, "subscription_id", None) or getattr(item, "payment_link_id", ""))
    toolbox.log.rail({"at": toolbox.clock.now().isoformat(), "partner": "Pine Labs", "endpoint": "webhook → /webhooks/pinelabs",
                      "initiated_by": "world", "in_reply_to": created_by, "request": None, "status": 200,
                      "response": payload, "played_by": f"curtain: {member['name']} approved in their app (real action)"})
    return payload, ""


def lines(script: Optional[str]) -> Iterator[str]:
    if script:
        for line in Path(script).read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                print(paint(C_DIM, f"world> {line}"))
                yield line
        return
    while True:
        try:
            yield input(paint(C_DIM, "world> "))
        except EOFError:
            return


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.environ.get("SIM_CONFIG", str(HERE / "config.json")))
    ap.add_argument("--script", help="operator lines from a file instead of the keyboard")
    a = ap.parse_args(argv)
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))

    import anthropic
    clock = SimClock(cfg.get("start"))
    log = RunLog()
    curtain = Curtain(clock)
    channel: Channel = build_channel(cfg)
    toolbox = Toolbox(cfg, channel, curtain, log, clock)
    agent = Agent(anthropic.Anthropic(), toolbox, build_system(cfg), log, clock)
    (log.dir / "system_prompt.md").write_text(agent.system, encoding="utf-8")
    (log.dir / "roster.json").write_text(json.dumps([m["name"] for m in cfg["members"]], ensure_ascii=False), encoding="utf-8")
    banner(f"Quorum simulation · model {agent.model} · effort {agent.effort} · channel {channel.name} · "
           f"Pine Labs {toolbox.pinelabs.mode} · {clock.stamp()}", colour=C_RESP)
    print(paint(C_DIM, f"run log: {log.dir}"))

    for line in lines(a.script):
        for msg in channel.poll():
            m = toolbox.members[msg["member_id"]]
            agent.handle(f"Chat ({msg['where']})", f"{m['name']}'s phone", msg["text"], who=f"{m['name']} ({m['id']})")
        line = line.strip()
        if not line:
            continue
        try:
            if line.startswith("@"):
                head, _, text = line[1:].partition(":")
                where = "group" if head.strip().lower().endswith(" group") else "DM"
                m = find_member(cfg, head.strip()[:-6] if where == "group" else head)
                if not m:
                    print(paint(C_WARN, "who? use a first name or member id"))
                    continue
                agent.handle(f"Chat ({where})", f"{m['name']}'s phone", text.strip(), who=f"{m['name']} ({m['id']})")
            elif line.startswith("/advance"):
                clock.advance(line.split(maxsplit=1)[1])
                agent.handle("Clock", "system clock", f"It is now {clock.stamp()}.")
            elif line.startswith("/approve") or line.startswith("/webhook"):
                parts = line.split()
                m = find_member(cfg, parts[1]) if len(parts) > 1 else None
                if not m:
                    print(paint(C_WARN, "usage: /approve Name [UPI|CARD]"))
                    continue
                method = parts[2].upper() if len(parts) > 2 else "UPI"
                payload, why = approve(toolbox, m, method)
                if payload is None:
                    print(paint(C_WARN, f"refused: {why}"))
                    continue
                agent.handle("Pine Labs webhook", f"Pine Labs → our callback_url, after {m['name']} approved in their app",
                             json.dumps(payload, ensure_ascii=False, indent=1))
            elif line.startswith("/fail-debit"):
                parts = line.split()
                m = find_member(cfg, parts[1]) if len(parts) > 1 else None
                if not m:
                    print(paint(C_WARN, "usage: /fail-debit Name"))
                    continue
                toolbox.ledger.fail_next_debit.add(m["id"])
                print(paint(C_DIM, f"(operator) {m['name']}'s bank will decline the next debit"))
            elif line.startswith("/event"):
                src, _, text = line[len("/event"):].partition("|")
                agent.handle("World event", src.strip() or "operator", text.strip())
            elif line == "/status":
                print(json.dumps(toolbox.ledger.summary(), indent=1, ensure_ascii=False))
            elif line == "/export":
                print(paint(C_RESP, f"exported to {export.write(log.dir)}"))
            elif line in ("/quit", "/exit"):
                break
            else:
                print(paint(C_WARN, "unknown command; see the top of sim/agent.py"))
        except KeyboardInterrupt:
            print(paint(C_WARN, "interrupted this event"))
    print(paint(C_RESP, f"exported to {export.write(log.dir)}"))


if __name__ == "__main__":
    main()
