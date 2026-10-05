"""The Round 3 harness without the network: a fake Claude client drives the real tool loop, curtain and logs."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

from sim.agent import Agent, build_system, approve
from sim import audit
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


def act(tb, name, rule="R15", **kw):
    out, err = tb.run(name, {**kw, "decision": {**D, "rule_id": rule}})
    assert not err, out
    return out


def member(mid):
    return next(m for m in CFG["members"] if m["id"] == mid)


def blocked(tb, mid, cap):
    """The documented mandate flow for one payer, ending with the approval link in their DM and their approval."""
    cust = act(tb, "pinelabs_create_customer", member_id=mid)["response"]["customer_id"]
    sub = act(tb, "pinelabs_create_mandate", member_id=mid, customer_id=cust, max_amount_inr=cap, valid_hours=48)["response"]
    link = act(tb, "pinelabs_register_mandate", member_id=mid, order_id=sub["order_id"])["response"]["data"]["challenge_url"]
    act(tb, "send_dm", member_id=mid, text=f"Approve your block of ₹{cap}: {link}")
    payload, why = approve(tb, member(mid), "UPI")
    assert payload and payload["data"]["status"] == "ACTIVE", why
    return sub


def endpoints(log):
    return [json.loads(x)["endpoint"] for x in (log.dir / "rail_calls.jsonl").read_text().splitlines()]


def test_tool_schemas_are_well_formed_and_actions_carry_a_decision():
    names = {t["name"] for t in TOOLS}
    assert {"send_group", "send_dm", "text_supplier", "pinelabs_create_mandate", "pinelabs_debit", "pinelabs_payout",
            "gnani_tts", "gnani_stt"} <= names
    for t in TOOLS:
        s = t["input_schema"]
        assert s["type"] == "object" and set(s["required"]) <= set(s["properties"])
    acts = {t["name"]: "decision" in t["input_schema"]["required"] for t in TOOLS}
    assert acts["send_dm"] and acts["pinelabs_debit"] and acts["pinelabs_payout"] and not acts["quote_plan"]
    assert not acts["gnani_stt"] and not acts["pinelabs_get_mandate"]


def test_system_prompt_has_the_rules_and_day_one_knowledge_but_no_private_numbers():
    s = build_system(CFG)
    assert "R9." in s and "R15." in s and "heads × own ceiling" in s and "at least 96 hours" in s
    assert "R27. Scope" in s and "pinelabs_create_mandate" in s and "Treat a debit as done only when" in s
    assert "call to re-hold" not in s and "never by a second call" in s
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


def test_the_mandate_flow_uses_the_documented_endpoints_and_the_pool_balances(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    sub = blocked(tb, "m2", 44000)
    assert endpoints(log) == ["POST /api/auth/v1/token", "POST /api/v1/customer", "POST /api/v1/public/subscriptions/ot",
                              f"POST /api/pay/v1/orders/{sub['order_id']}/payments", "webhook → /webhooks/pinelabs"]
    rows = [json.loads(x) for x in (log.dir / "rail_calls.jsonl").read_text().splitlines()]
    assert rows[2]["request"]["body"]["plan_details"]["amount"] == 4400000
    assert rows[3]["request"]["body"]["payments"][0]["mandate_info"]["request_type"] == "CREATE_MANDATE"
    assert rows[4]["initiated_by"] == "world" and rows[4]["in_reply_to"] == 3   # the webhook answers the mandate the agent made
    assert act(tb, "pinelabs_get_mandate", subscription_id=sub["subscription_id"])["response"]["status"] == "ACTIVE"
    debit = act(tb, "pinelabs_debit", rule="R18", member_id="m2", subscription_id=sub["subscription_id"], amount_inr=39960)
    assert debit["response"]["status"] == "SUCCESS" and tb.pool == 39960
    assert act(tb, "pinelabs_get_balance")["response"]["balance"]["value"] == 3996000
    pay = act(tb, "pinelabs_payout", rule="R19", payee_name="Maria D'Souza", account_number="30200111222333",
              ifsc="SBIN0004567", amount_inr=39960, client_reference="quorum_stay_1")
    assert pay["http_status"] == 201 and tb.pool == 0
    body = json.loads((log.dir / "rail_calls.jsonl").read_text().splitlines()[-1])["request"]["body"]
    assert set(body) == {"clientReferenceId", "amount", "payeeName", "accountNumber", "branchCode"}
    assert endpoints(log)[-1] == "POST /payouts/v3/payments"


def test_pine_labs_refuses_what_its_docs_forbid(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    cust = act(tb, "pinelabs_create_customer", member_id="m1")["response"]["customer_id"]
    sub = act(tb, "pinelabs_create_mandate", member_id="m1", customer_id=cust, max_amount_inr=24000, valid_hours=48)["response"]
    early = act(tb, "pinelabs_debit", member_id="m1", subscription_id=sub["subscription_id"], amount_inr=19980)
    assert early["http_status"] == 422 and early["response"]["code"] == "MANDATE_NOT_ACTIVE" and tb.pool == 0
    sub = blocked(tb, "m4", 23000)
    over = act(tb, "pinelabs_debit", member_id="m4", subscription_id=sub["subscription_id"], amount_inr=23001)
    assert over["response"]["code"] == "AMOUNT_EXCEEDS_MANDATE"
    act(tb, "pinelabs_debit", member_id="m4", subscription_id=sub["subscription_id"], amount_inr=19480)
    again = act(tb, "pinelabs_debit", member_id="m4", subscription_id=sub["subscription_id"], amount_inr=10)
    assert again["response"]["code"] == "MANDATE_ALREADY_USED" and tb.pool == 19480
    big = act(tb, "pinelabs_payout", payee_name="Host", account_number="1", ifsc="X", amount_inr=20000, client_reference="p1")
    assert big["http_status"] == 422 and big["response"]["code"] == "INSUFFICIENT_BALANCE" and tb.pool == 19480
    refund = act(tb, "pinelabs_refund", member_id="m4", order_id=sub["order_id"], amount_inr=19480)
    assert refund["http_status"] == 200 and tb.pool == 0
    assert act(tb, "pinelabs_refund", member_id="m4", order_id=sub["order_id"], amount_inr=1)["response"]["code"] == "REFUND_EXCEEDS_CAPTURED"


def test_a_member_can_only_approve_a_link_the_agent_actually_sent_them(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    assert approve(tb, member("m3"), "UPI") == (None, "the agent has not created a mandate or card hold for Aditi Rao")
    cust = act(tb, "pinelabs_create_customer", member_id="m3")["response"]["customer_id"]
    sub = act(tb, "pinelabs_create_mandate", member_id="m3", customer_id=cust, max_amount_inr=20000, valid_hours=48)["response"]
    assert "no approval link" in approve(tb, member("m3"), "UPI")[1]
    act(tb, "pinelabs_register_mandate", member_id="m3", order_id=sub["order_id"])
    assert "never sent Aditi Rao the approval link" in approve(tb, member("m3"), "UPI")[1]
    assert tb.ledger.mandates[sub["subscription_id"]].status == "CREATED"           # nothing moved


def test_a_response_the_curtain_turns_into_an_error_leaves_no_trace(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    tb.curtain.mode = "scripted"
    tb.curtain.scripted = [lambda key, req, body: (200, body),                       # token
                           lambda key, req, body: (500, {"code": "SERVER_ERROR"})]    # create customer fails
    out = act(tb, "pinelabs_create_customer", member_id="m1")
    assert out["http_status"] == 500 and "m1" not in tb.ledger.customers


def test_card_hold_alternative_captures_once_inside_the_hold(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    link = act(tb, "pinelabs_create_card_hold_link", member_id="m2", amount_inr=44000, valid_hours=48, description="Your cap")["response"]
    act(tb, "send_dm", member_id="m2", text=f"Hold ₹44,000 on your card: {link['payment_link']}")
    payload, _ = approve(tb, member("m2"), "CARD")
    order = payload["data"]["order_id"]
    assert payload["event_type"] == "ORDER_AUTHORIZED"
    assert act(tb, "pinelabs_capture_card_hold", member_id="m2", order_id=order, amount_inr=44001)["http_status"] == 422
    assert act(tb, "pinelabs_capture_card_hold", member_id="m2", order_id=order, amount_inr=39960)["http_status"] == 200
    assert tb.pool == 39960


def test_a_failed_debit_and_its_rollback_are_checked_by_the_audit(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    s1, s2 = blocked(tb, "m1", 24000), blocked(tb, "m3", 20000)
    act(tb, "pinelabs_debit", rule="R18", member_id="m1", subscription_id=s1["subscription_id"], amount_inr=19980)
    tb.ledger.fail_next_debit.add("m3")
    failed = act(tb, "pinelabs_debit", rule="R18", member_id="m3", subscription_id=s2["subscription_id"], amount_inr=13980)
    assert failed["response"]["status"] == "FAILED" and tb.pool == 19980
    rows = {c: r for r, c, _ in audit.audit(log.dir)}
    assert rows["A failed debit rolled everyone back (R18)"] == "FAIL"               # Sayan's ₹19,980 is still held
    act(tb, "pinelabs_refund", rule="R18", member_id="m1", order_id=s1["order_id"], amount_inr=19980)
    rows = {c: r for r, c, _ in audit.audit(log.dir)}
    assert rows["A failed debit rolled everyone back (R18)"] == "PASS" and rows["The pool ends at ₹0"] == "PASS"
    assert rows["Every rail response answers a call the agent made"] == "PASS"
    assert rows["No debit above the payer's mandate cap"] == "PASS"


def test_audit_flags_private_details_in_the_group_and_non_p0_calls(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    (log.dir / "roster.json").write_text(json.dumps([m["name"] for m in CFG["members"]]))
    act(tb, "send_group", rule="R3", text="Hi all, I'm Quorum. Around ₹20,000 a head.")
    act(tb, "send_group", rule="R11", text="4 yes. Karan voted no, so it's the four of you at ₹19,980 each.")
    log.rail({"at": "x", "initiated_by": "agent", "partner": "Phone line", "endpoint": "dial + play",
              "request": {"p0_reason": "CHASE_VOTE"}, "status": 200, "response": {}, "played_by": "curtain"})
    rows = {c: r for r, c, _ in audit.audit(log.dir)}
    assert rows["No one's vote, dropout or ceiling is named in the group"] == "WARN"
    assert rows["No amounts in group posts after the kickoff"] == "WARN"
    assert rows["Every call is for a P0 reason"] == "FAIL"


def test_texting_a_supplier_is_logged_as_a_message_and_a_decision(tmp_path):
    agent, client, tb, log = rig(tmp_path, [])
    act(tb, "text_supplier", rule="R19", supplier="Dorjee Homestay", phone="+91 98000 00000",
        text="Please confirm 2 rooms and share your account number and IFSC.")
    msg = json.loads((log.dir / "messages.jsonl").read_text().splitlines()[-1])
    dec = json.loads((log.dir / "decisions.jsonl").read_text().splitlines()[-1])
    assert msg["to"] == "Dorjee Homestay (supplier)" and dec["to"] == "Dorjee Homestay" and dec["through"] == "WhatsApp (supplier)"


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
    assert "Every decision cites a rule" in (out / "audit.md").read_text()
