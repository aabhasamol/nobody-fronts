"""The Round 3 harness without the network: a fake Claude client drives the real tool loop, curtain and logs."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

from sim.agent import Agent, build_system, webhook
from sim.channels import Channel
from sim.tools import TOOLS, Toolbox
from sim.world import Curtain, RunLog, SimClock
from sim import export

CFG = json.loads((Path(__file__).resolve().parents[1] / "sim" / "config.json").read_text(encoding="utf-8"))
D = {"decided": "x", "rule_id": "R3", "trigger": "kickoff", "state": "GATHERING"}


def use(i, name, **inp):
    return NS(type="tool_use", id=f"tu_{i}", name=name, input=inp)


def reply(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, model="claude-opus-5-5",
              usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0))


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})      # snapshot: the agent keeps appending
        return self.script.pop(0)


class FakeSpeech:
    def tts(self, text):
        return b"RIFF....WAVEfake"

    def stt(self, path, language_code="hi-IN"):
        return "Haan, teen kamre hain. 3200 per night. 96 ghante hold kar denge."


def rig(tmp_path, script):
    clock = SimClock("2026-10-04T20:00:00+05:30")
    log = RunLog(tmp_path)
    curtain = Curtain(clock, mode="auto")
    tb = Toolbox(CFG, Channel({m["id"]: m for m in CFG["members"]}, CFG["group_title"]), curtain, log, clock,
                 speech=FakeSpeech(), player=lambda p: None)
    client = FakeClient(script)
    return Agent(client, tb, build_system(CFG), log, clock, effort="high"), client, tb, log


def test_tool_schemas_are_well_formed_and_actions_carry_a_decision():
    names = {t["name"] for t in TOOLS}
    assert {"send_group", "send_dm", "pinelabs_create_payment_link", "pinelabs_capture", "gnani_tts", "gnani_stt"} <= names
    for t in TOOLS:
        s = t["input_schema"]
        assert s["type"] == "object" and set(s["required"]) <= set(s["properties"])
    acts = {t["name"]: "decision" in t["input_schema"]["required"] for t in TOOLS}
    assert acts["send_dm"] and acts["pinelabs_capture"] and not acts["quote_plan"] and not acts["gnani_stt"]


def test_system_prompt_has_the_rules_and_day_one_knowledge_but_no_private_numbers():
    s = build_system(CFG)
    assert "R9." in s and "R15." in s and "heads × own ceiling" in s and "at least 96 hours" in s
    assert '"organiser": true' in s and "Chapora Fort" in s
    assert "telegram" not in s.lower() and "24000" not in s and "ceiling\": " not in s


def test_one_event_runs_tools_until_the_model_stops_and_logs_every_decision(tmp_path):
    script = [
        reply(NS(type="thinking", thinking="Kickoff has B and o; post kickoff, DM all five."),
              use(1, "send_group", text="Hi all, I'm Quorum…", decision=D),
              *[use(i + 2, "send_dm", member_id=m["id"], text=f"Hi {m['name'].split()[0]}, six questions…",
                    decision={**D, "rule_id": "R4"}) for i, m in enumerate(CFG["members"])]),
        reply(NS(type="text", text="Waiting for replies until tomorrow 20:00."), stop="end_turn"),
    ]
    agent, client, tb, log = rig(tmp_path, script)
    agent.handle("Chat (group)", "Sayan's phone", "Priya's wedding, Goa, 20–24 Nov, ₹20k a head, 10% over", who="Sayan Das (m1)")
    assert len(client.calls) == 2
    first, second = client.calls
    assert first["model"] == "claude-opus-5-5" and first["thinking"]["type"] == "adaptive" and first["fallbacks"] == "default"
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    results = second["messages"][-1]["content"]
    assert [r["type"] for r in results] == ["tool_result"] * 6 and results[0]["tool_use_id"] == "tu_1"
    assert "decision" in second["messages"][-2]["content"][1].input            # history is sent back unedited
    rows = [json.loads(x) for x in (log.dir / "decisions.jsonl").read_text().splitlines()]
    assert len(rows) == 6 and rows[0]["why"] == "R3" and rows[1]["through"] == "Chat (DM)" and rows[1]["to"] == "Sayan Das"
    assert rows[0]["from"].startswith("Chat (group)") and "Priya" in rows[0]["received"]


def test_pine_labs_calls_build_the_documented_request_and_log_both_sides(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    out, err = tb.run("pinelabs_create_payment_link", {"member_id": "m2", "amount_inr": 44000, "valid_hours": 48,
                                                       "description": "Your share", "decision": {**D, "rule_id": "R15"}})
    assert not err and out["status"] == 200
    calls = [json.loads(x) for x in (log.dir / "rail_calls.jsonl").read_text().splitlines()]
    assert [c["endpoint"] for c in calls] == ["POST /api/auth/v1/token", "POST /api/pay/v1/paymentlink"]
    body = calls[1]["request"]["body"]
    assert body["amount"] == {"value": 4400000, "currency": "INR"} and body["pre_auth"] == "true"
    link_id = out["response"]["payment_link_id"]
    assert out["response"]["amount"]["value"] == 4400000 and tb.pinelabs.links[link_id]["member_id"] == "m2"
    payload = webhook(tb, tb.curtain, next(m for m in CFG["members"] if m["id"] == "m2"), "AUTHORIZED", "CARD")
    order = payload["data"]["order_id"]
    assert payload["event_type"] == "ORDER_AUTHORIZED" and tb.pinelabs.orders[order]["authorised_inr"] == 44000
    out, err = tb.run("pinelabs_capture", {"member_id": "m2", "order_id": order, "amount_inr": 39960, "decision": {**D, "rule_id": "R18"}})
    assert not err and tb.pool == 39960 and tb.pinelabs.orders[order]["captured_inr"] == 39960
    out, err = tb.run("pinelabs_payout", {"beneficiary_name": "Homestay", "amount_inr": 38400, "purpose": "STAY",
                                          "reference": "HOLD-1", "decision": {**D, "rule_id": "R19"}})
    assert tb.pool == 1560


def test_gnani_tts_call_and_stt_flow_through_the_phone_line(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    reply_wav = tmp_path / "homestay.wav"
    reply_wav.write_bytes(b"RIFF....WAVEreply")
    tb.curtain.mode = "scripted"
    tb.curtain.scripted = [lambda *a: (200, str(reply_wav))]
    tts, _ = tb.run("gnani_tts", {"text": "Namaste…", "language": "hi-IN", "decision": {**D, "rule_id": "R8"}})
    call, err = tb.run("place_call", {"callee": "Dona Maria Homestay", "phone": "0832", "audio_file": tts["audio_file"],
                                      "p0_reason": "SUPPLIER_AVAILABILITY", "decision": {**D, "rule_id": "R8, R25"}})
    assert not err and call["disposition"] == "ANSWERED"
    stt, err = tb.run("gnani_stt", {"audio_file": call["reply_audio_file"], "language": "hi-IN"})
    assert "3200" in stt["transcript"]
    endpoints = [json.loads(x)["endpoint"] for x in (log.dir / "rail_calls.jsonl").read_text().splitlines()]
    assert endpoints[0].endswith("/api/v1/tts/inference") and endpoints[2].endswith("/stt/v3")


def test_calculators_match_the_engine():
    tb = Toolbox(CFG, Channel({}, ""), None, None, None, speech=FakeSpeech())
    q = tb.t_quote_plan(3200, 4, [{"member_id": "m1", "party": 1, "leg_prices_per_seat_inr": [6400, 5900]},
                                  {"member_id": "m2", "party": 2, "leg_prices_per_seat_inr": [6400, 5900]},
                                  {"member_id": "m3", "party": 1, "leg_prices_per_seat_inr": [3400, 2900]},
                                  {"member_id": "m4", "party": 1, "leg_prices_per_seat_inr": [6100, 5700]}])
    assert q["twin_rooms"] == 3 and q["stay_per_head_inr"] == 7680
    assert [p["share_inr"] for p in q["payers"]][:2] == [19980, 39960]          # the engine's wedding numbers
    c = tb.t_caps([{"member_id": "m2", "party": 2, "ceiling_per_head_inr": 22000, "charged_inr": 39960}])
    assert c["payers"][0] == {"member_id": "m2", "cap_inr": 44000, "headroom_inr": 4040}


def test_export_writes_the_three_tables(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    tb.current_event = {"summary": "Karan: No, can't get leave", "source": "Chat (DM) · Karan's phone"}
    tb.run("send_dm", {"member_id": "m5", "text": "One fact in case it changes anything…", "decision": {**D, "rule_id": "R13"}})
    out = export.write(log.dir)
    dec = (out / "decisions.md").read_text()
    assert "| 1 |" in dec and "R13" in dec and "Karan Mehta" in dec and "Chat (DM)" in dec
    assert (out / "transcript.md").exists() and (out / "rail_calls.md").exists()
