"""Pine Labs as the world sees it: a ledger that plays the documented API and enforces its rules.

The person behind the curtain still confirms or overrides every response, but the ledger computes what Pine Labs
would actually return given everything that has happened, so a run cannot contain a state that never happened:

    * no mandate is ACTIVE until the agent created it, the approval link reached the member, and the member approved
    * no debit without an ACTIVE mandate, none above its cap, and only one per one-time mandate
    * no capture above a card hold, no refund above what was taken, no payout above the pool

Endpoints (documented, as read from Pine Labs' docs by the team; [verify] where marked in curtain/templates.json):
    POST /api/v1/customer                              create the member as a customer
    POST /api/v1/public/subscriptions/ot               one-time mandate; plan_details.amount = the most ever debited
    POST /api/pay/v1/orders/{order_id}/payments        register it (mandate_info.request_type CREATE_MANDATE) → approval link
    GET  /api/v1/subscriptions/ot/{subscription_id}    status CREATED / ACTIVE
    POST /api/v1/public/presentations                  debit, amount ≤ the mandate's cap
    GET  /api/v1/public/presentations/{presentation_id}
    POST /api/pay/v1/paymentlink                       alternative: a card pre-authorisation link
    PUT  /api/pay/v1/orders/{order_id}/capture         capture a card hold [verify]
    PUT  /api/pay/v1/orders/{order_id}/cancel          release a card hold [verify]
    POST /api/pay/v1/refunds/{order_id}                refund
    POST /payouts/v3/payments                          Create Payout (bank account + IFSC)
    GET  /payouts/v3/payments/funding-account          Get Account Balance
    GET  /payouts/v3/payments                          Get Payouts
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def err(status: int, code: str, message: str) -> tuple[int, dict]:
    return status, {"code": code, "message": message}


@dataclass
class Mandate:
    subscription_id: str
    order_id: str
    member_id: str
    customer_id: str
    cap_paisa: int
    reference: str
    status: str = "CREATED"                  # CREATED → ACTIVE (member approved) → CANCELLED / EXPIRED
    challenge_url: Optional[str] = None      # set when the agent registers the mandate payment
    debited_paisa: int = 0
    refunded_paisa: int = 0
    presentations: list[str] = field(default_factory=list)


@dataclass
class CardHold:
    payment_link_id: str
    member_id: str
    amount_paisa: int
    reference: str
    url: str
    status: str = "CREATED"                  # CREATED → AUTHORIZED (member paid on the page) → PROCESSED / CANCELLED
    order_id: Optional[str] = None
    captured_paisa: int = 0
    refunded_paisa: int = 0


class PineLabsLedger:
    def __init__(self):
        self.customers: dict[str, dict] = {}         # member_id → customer
        self.mandates: dict[str, Mandate] = {}       # subscription_id → mandate
        self.holds: dict[str, CardHold] = {}         # payment_link_id → card hold
        self.presentations: dict[str, dict] = {}     # presentation_id → debit
        self.payouts: list[dict] = []
        self.pool_paisa = 0                          # Quorum's merchant account for this trip
        self.fail_next_debit: set[str] = set()       # members whose bank will decline the next debit (operator-armed)

    # ---------------------------------------------------------------- lookups
    def mandate_by_order(self, order_id: str) -> Optional[Mandate]:
        return next((m for m in self.mandates.values() if m.order_id == order_id), None)

    def hold_by_order(self, order_id: str) -> Optional[CardHold]:
        return next((h for h in self.holds.values() if h.order_id == order_id), None)

    def latest_for(self, member_id: str) -> Optional[Mandate | CardHold]:
        items: list[Any] = [m for m in self.mandates.values() if m.member_id == member_id]
        items += [h for h in self.holds.values() if h.member_id == member_id]
        return items[-1] if items else None

    # ---------------------------------------------------------------- customers and mandates
    # Each create/cancel has a pure step (what Pine Labs would answer) and an apply step that commits only the final,
    # confirmed response, so a response the curtain turned into an error leaves no trace.
    def create_customer(self, member_id: str, body: dict) -> tuple[int, dict]:
        cust = self.customers.get(member_id)
        if cust is not None:
            return 200, dict(cust)
        return 200, {"customer_id": _id("cust-v1-"), "merchant_customer_reference": body.get("merchant_customer_reference"),
                     "status": "ACTIVE"}

    def apply_customer(self, member_id: str, status: int, resp: Any) -> None:
        if status < 400 and isinstance(resp, dict) and resp.get("customer_id"):
            self.customers[member_id] = dict(resp)

    def create_subscription(self, member_id: str, customer_id: str, cap_paisa: int, reference: str) -> tuple[int, dict]:
        cust = self.customers.get(member_id)
        if not cust or cust["customer_id"] != customer_id:
            return err(404, "CUSTOMER_NOT_FOUND", f"no customer {customer_id} for this merchant")
        if cap_paisa <= 0:
            return err(400, "INVALID_AMOUNT", "plan_details.amount must be positive")
        return 200, {"subscription_id": _id("sub-v1-"), "order_id": _id("v1-"), "status": "CREATED",
                     "plan_details": {"amount": cap_paisa, "currency": "INR"}}

    def apply_subscription(self, member_id: str, customer_id: str, reference: str, status: int, resp: Any) -> None:
        if status >= 400 or not isinstance(resp, dict) or not resp.get("subscription_id"):
            return
        cap = int((resp.get("plan_details") or {}).get("amount", 0))
        m = Mandate(subscription_id=resp["subscription_id"], order_id=resp.get("order_id") or _id("v1-"),
                    member_id=member_id, customer_id=customer_id, cap_paisa=cap, reference=reference,
                    status=str(resp.get("status", "CREATED")).upper())
        self.mandates[m.subscription_id] = m

    def register_mandate(self, order_id: str) -> tuple[int, dict]:
        m = self.mandate_by_order(order_id)
        if not m:
            return err(404, "ORDER_NOT_FOUND", f"no order {order_id}")
        if m.status != "CREATED":
            return err(409, "INVALID_STATE", f"mandate is {m.status}")
        return 200, {"data": {"order_id": order_id, "status": "PENDING", "challenge_url": f"upi://mandate?ref={m.subscription_id}"}}

    def apply_register(self, order_id: str, status: int, resp: Any) -> None:
        m = self.mandate_by_order(order_id)
        d = resp.get("data", resp) if isinstance(resp, dict) else {}
        if m and status < 400 and d.get("challenge_url"):
            m.challenge_url = d["challenge_url"]

    def get_subscription(self, subscription_id: str) -> tuple[int, dict]:
        m = self.mandates.get(subscription_id)
        if not m:
            return err(404, "SUBSCRIPTION_NOT_FOUND", f"no subscription {subscription_id}")
        return 200, {"subscription_id": m.subscription_id, "order_id": m.order_id, "status": m.status,
                     "plan_details": {"amount": m.cap_paisa, "currency": "INR"}}

    def cancel_subscription(self, subscription_id: str) -> tuple[int, dict]:
        m = self.mandates.get(subscription_id)
        if not m:
            return err(404, "SUBSCRIPTION_NOT_FOUND", f"no subscription {subscription_id}")
        if m.debited_paisa:
            return err(409, "INVALID_STATE", "mandate already debited; refund instead")
        return 200, {"subscription_id": m.subscription_id, "status": "CANCELLED"}

    def apply_cancel_subscription(self, subscription_id: str, status: int) -> None:
        m = self.mandates.get(subscription_id)
        if m and status < 400:
            m.status = "CANCELLED"

    def approve(self, member_id: str, method: str = "UPI") -> tuple[Optional[dict], str]:
        """The member approves in their UPI app (or pays on the card page). Returns (webhook payload, why-not)."""
        item = self.latest_for(member_id)
        if item is None:
            return None, "nothing was created for this member"
        if isinstance(item, Mandate):
            if item.challenge_url is None:
                return None, "the mandate was never registered, so there is no approval link to approve"
            if item.status != "CREATED":
                return None, f"the mandate is already {item.status}"
            item.status = "ACTIVE"
            return {"event_type": "SUBSCRIPTION_ACTIVATED",
                    "data": {"subscription_id": item.subscription_id, "order_id": item.order_id, "status": "ACTIVE",
                             "plan_details": {"amount": item.cap_paisa, "currency": "INR"}}}, ""
        if item.status != "CREATED":
            return None, f"the card hold is already {item.status}"
        # (a card hold's link exists from creation; the toolbox checks it reached the member)
        item.status, item.order_id = "AUTHORIZED", _id("v1-")
        return {"event_type": "ORDER_AUTHORIZED",
                "data": {"order_id": item.order_id, "payment_link_id": item.payment_link_id, "status": "AUTHORIZED",
                         "pre_auth": True, "order_amount": {"value": item.amount_paisa, "currency": "INR"},
                         "payments": [{"status": "AUTHORIZED", "payment_method": method}]}}, ""

    # ---------------------------------------------------------------- debits (presentations)
    def present(self, subscription_id: str, amount_paisa: int, reference: str) -> tuple[int, dict]:
        m = self.mandates.get(subscription_id)
        if not m:
            return err(404, "SUBSCRIPTION_NOT_FOUND", f"no subscription {subscription_id}")
        if m.status != "ACTIVE":
            return err(422, "MANDATE_NOT_ACTIVE", f"mandate is {m.status}; the member has not approved it")
        if m.debited_paisa:
            return err(422, "MANDATE_ALREADY_USED", "a one-time mandate allows a single debit")
        if amount_paisa > m.cap_paisa:
            return err(422, "AMOUNT_EXCEEDS_MANDATE", f"amount {amount_paisa} exceeds the mandate cap {m.cap_paisa}")
        pid = _id("pres-v1-")
        status = "FAILED" if m.member_id in self.fail_next_debit else "SUCCESS"
        self.fail_next_debit.discard(m.member_id)
        p = {"presentation_id": pid, "subscription_id": subscription_id, "merchant_presentation_reference": reference,
             "amount": {"value": amount_paisa, "currency": "INR"}, "status": status}
        if status == "FAILED":
            p["failure_reason"] = "Debit declined by the payer's bank"
        return 200, p

    def apply_presentation(self, body: dict) -> None:
        """Commit a debit exactly as the response says (the operator may have edited it)."""
        pid, sid = body.get("presentation_id"), body.get("subscription_id")
        m = self.mandates.get(sid or "")
        if not pid or not m:
            return
        self.presentations[pid] = dict(body)
        m.presentations.append(pid)
        if str(body.get("status", "")).upper() in ("SUCCESS", "PROCESSED"):
            amt = int((body.get("amount") or {}).get("value", 0))
            m.debited_paisa += amt
            self.pool_paisa += amt

    def get_presentation(self, presentation_id: str) -> tuple[int, dict]:
        p = self.presentations.get(presentation_id)
        return (200, dict(p)) if p else err(404, "PRESENTATION_NOT_FOUND", f"no presentation {presentation_id}")

    # ---------------------------------------------------------------- card holds (payment link alternative)
    def create_link(self, member_id: str, amount_paisa: int, reference: str) -> tuple[int, dict]:
        lid = _id("pl-v1-")
        return 200, {"payment_link_id": lid, "payment_link": f"https://pbl.v2.pinepg.in/{lid}", "status": "CREATED",
                     "amount": {"value": amount_paisa, "currency": "INR"}, "merchant_payment_link_reference": reference}

    def apply_link(self, member_id: str, reference: str, status: int, resp: Any) -> None:
        if status >= 400 or not isinstance(resp, dict) or not resp.get("payment_link_id"):
            return
        amt = int((resp.get("amount") or {}).get("value", 0))
        self.holds[resp["payment_link_id"]] = CardHold(payment_link_id=resp["payment_link_id"], member_id=member_id,
                                                       amount_paisa=amt, reference=reference, url=resp.get("payment_link", ""))

    def get_link(self, payment_link_id: str) -> tuple[int, dict]:
        h = self.holds.get(payment_link_id)
        if not h:
            return err(404, "PAYMENT_LINK_NOT_FOUND", f"no payment link {payment_link_id}")
        body = {"payment_link_id": h.payment_link_id, "status": h.status, "amount": {"value": h.amount_paisa, "currency": "INR"}}
        if h.order_id:
            body["order_id"] = h.order_id
        return 200, body

    def capture(self, order_id: str, amount_paisa: int) -> tuple[int, dict]:
        h = self.hold_by_order(order_id)
        if not h:
            return err(404, "ORDER_NOT_FOUND", f"no order {order_id}")
        if h.status != "AUTHORIZED":
            return err(422, "ORDER_NOT_AUTHORIZED", f"order is {h.status}")
        if amount_paisa > h.amount_paisa:
            return err(422, "AMOUNT_EXCEEDS_AUTHORIZED", f"capture {amount_paisa} exceeds the hold {h.amount_paisa}")
        if h.member_id in self.fail_next_debit:
            self.fail_next_debit.discard(h.member_id)
            return err(422, "CAPTURE_FAILED", "Capture could not be processed: payment declined by issuer")
        return 200, {"data": {"order_id": order_id, "status": "PROCESSED",
                              "captured_amount": {"value": amount_paisa, "currency": "INR"}}}

    def apply_capture(self, order_id: str, status: int, body: dict) -> None:
        h = self.hold_by_order(order_id)
        d = body.get("data", body) if isinstance(body, dict) else {}
        if h and status < 400 and str(d.get("status", "")).upper() == "PROCESSED":
            amt = int((d.get("captured_amount") or {}).get("value", 0))
            h.captured_paisa += amt
            h.status = "PROCESSED"
            self.pool_paisa += amt

    def cancel_order(self, order_id: str) -> tuple[int, dict]:
        h = self.hold_by_order(order_id)
        if not h:
            return err(404, "ORDER_NOT_FOUND", f"no order {order_id}")
        if h.captured_paisa:
            return err(409, "INVALID_STATE", "order already captured; refund instead")
        return 200, {"data": {"order_id": order_id, "status": "CANCELLED"}}

    def apply_cancel_order(self, order_id: str, status: int) -> None:
        h = self.hold_by_order(order_id)
        if h and status < 400:
            h.status = "CANCELLED"

    # ---------------------------------------------------------------- refunds and the pool
    def refund(self, order_id: str, amount_paisa: int) -> tuple[int, dict]:
        m, h = self.mandate_by_order(order_id), self.hold_by_order(order_id)
        taken = (m.debited_paisa - m.refunded_paisa) if m else (h.captured_paisa - h.refunded_paisa) if h else None
        if taken is None:
            return err(404, "ORDER_NOT_FOUND", f"no order {order_id}")
        if amount_paisa > taken:
            return err(422, "REFUND_EXCEEDS_CAPTURED", f"refund {amount_paisa} exceeds the {taken} taken on this order")
        return 200, {"data": {"order_id": _id("v1-"), "parent_order_id": order_id, "type": "REFUND", "status": "PENDING",
                              "order_amount": {"value": amount_paisa, "currency": "INR"}}}

    def apply_refund(self, order_id: str, status: int, amount_paisa: int) -> None:
        if status >= 400:
            return
        m, h = self.mandate_by_order(order_id), self.hold_by_order(order_id)
        if m:
            m.refunded_paisa += amount_paisa
        elif h:
            h.refunded_paisa += amount_paisa
        self.pool_paisa -= amount_paisa

    def payout(self, body: dict) -> tuple[int, dict]:
        amt = int(body["amount"]["value"])
        if amt > self.pool_paisa:
            return err(422, "INSUFFICIENT_BALANCE", f"funding account holds {self.pool_paisa}, payout needs {amt}")
        return 201, {"paymentReferenceId": _id("pay-"), "clientReferenceId": body["clientReferenceId"],
                     "status": "SUCCESS", "amount": dict(body["amount"]), "payeeName": body["payeeName"], "mode": "IMPS"}

    def apply_payout(self, status: int, body: dict) -> None:
        if status < 400 and str(body.get("status", "")).upper() in ("SUCCESS", "PROCESSED", "PENDING"):
            self.pool_paisa -= int((body.get("amount") or {}).get("value", 0))
            self.payouts.append(dict(body))

    def balance(self) -> tuple[int, dict]:
        return 200, {"balance": {"value": self.pool_paisa, "currency": "INR"}}

    def list_payouts(self, client_reference: Optional[str]) -> tuple[int, dict]:
        rows = [p for p in self.payouts if not client_reference or p.get("clientReferenceId") == client_reference]
        return 200, {"data": rows}

    # ---------------------------------------------------------------- the operator's view
    def summary(self) -> dict:
        return {"pool_inr": self.pool_paisa / 100,
                "mandates": {m.subscription_id: {"member": m.member_id, "status": m.status, "cap_inr": m.cap_paisa / 100,
                                                 "debited_inr": m.debited_paisa / 100, "refunded_inr": m.refunded_paisa / 100,
                                                 "link_sent": m.challenge_url is not None} for m in self.mandates.values()},
                "card_holds": {h.payment_link_id: {"member": h.member_id, "status": h.status, "amount_inr": h.amount_paisa / 100,
                                                   "captured_inr": h.captured_paisa / 100} for h in self.holds.values()},
                "payouts": self.payouts}
