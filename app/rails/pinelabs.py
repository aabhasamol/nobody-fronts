"""Pine Labs Online — UPI One-Time Mandate (OTM) adapter.

Maps our three verbs onto Pine Labs' documented OT direct-execution flow
(https://www.pinelabs.com/docs/online-payments/one-time-mandate/integration-steps):

    create_block  →  POST /api/v1/customer (once per member)
                     POST /api/v1/public/subscriptions/ot        (amount = share, validity_days)
                     POST /api/pay/v1/orders/{order_id}/payments  (mandate_info.request_type=CREATE_MANDATE, UPI INTENT)
    refresh       →  GET  /api/v1/subscriptions/ot/{subscription_id}   (CREATED → ACTIVE once the member approves)
    capture       →  POST /api/v1/public/presentations               (amount ≤ subscription max)
    release       →  no documented cancel in the OTM guide; we let the mandate expire at validity_days
                     and record RELEASED locally. TODO: confirm cancel endpoint with Pine Labs sandbox team.

What Pine Labs gives us today: one payer blocks, merchant debits later, within a cap. Exactly the IPO
primitive, opened to merchants.
What it does not give us (our Round-2 ask): N mandates bound to ONE merchant order with a quorum rule,
an expiry, and an atomic capture. Today `Engine.book` loops `capture()` N times; a failure on the 4th
of 5 leaves 3 people charged for a trip that cannot be booked. See docs/rails.md.

Environment:
    PINELABS_BASE=https://pluraluat.v2.pinepg.in        (UAT — free developer keys from dashboardv2.pluralonline.com)
    PINELABS_SUB_BASE=<subscription-base-url>            (given with the OTM enablement; defaults to PINELABS_BASE)
    PINELABS_CLIENT_ID, PINELABS_CLIENT_SECRET, PINELABS_MERCHANT_ID
    PINELABS_CALLBACK_URL=https://<your-tunnel>/webhooks/pinelabs

Amounts: Pine Labs public subscription APIs take paisa. We hold INR everywhere else and convert here.
"""
from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
import httpx
from ..clock import clock
from ..models import Member, Authorisation, AuthStatus
from .base import PaymentsRail


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PineLabsPayments(PaymentsRail):
    def __init__(self):
        self.base = os.environ.get("PINELABS_BASE", "https://pluraluat.v2.pinepg.in").rstrip("/")
        self.sub_base = os.environ.get("PINELABS_SUB_BASE", self.base).rstrip("/")
        self.client_id = os.environ["PINELABS_CLIENT_ID"]
        self.client_secret = os.environ["PINELABS_CLIENT_SECRET"]
        self.merchant_id = os.environ["PINELABS_MERCHANT_ID"]
        self.callback_url = os.environ.get("PINELABS_CALLBACK_URL", "https://example.invalid/webhooks/pinelabs")
        self._token: str | None = None
        self.http = httpx.Client(timeout=20)

    # ------------------------------------------------------------ auth
    def _headers(self, subscription: bool = False) -> dict:
        h = {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json",
             "Accept": "application/json", "Request-ID": str(uuid.uuid4()), "Request-Timestamp": _now_iso()}
        if subscription:
            h["Merchant-ID"] = self.merchant_id
        return h

    def token(self) -> str:
        if self._token:
            return self._token
        r = self.http.post(f"{self.base}/api/auth/v1/token",
                           headers={"Content-Type": "application/json", "Request-ID": str(uuid.uuid4()),
                                    "Request-Timestamp": _now_iso()},
                           json={"client_id": self.client_id, "client_secret": self.client_secret,
                                 "grant_type": "client_credentials"})
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    # ------------------------------------------------------------ customer
    def ensure_customer(self, member: Member) -> str:
        if member.pinelabs_customer_id:
            return member.pinelabs_customer_id
        first, _, last = member.name.partition(" ")
        r = self.http.post(f"{self.base}/api/v1/customer", headers=self._headers(),
                           json={"merchant_customer_reference": f"quorum-{member.id}",
                                 "first_name": first, "last_name": last or "-",
                                 "country_code": "91", "mobile_number": member.phone,
                                 "email_id": f"{member.id}@quorum.invalid"})
        r.raise_for_status()
        member.pinelabs_customer_id = r.json().get("customer_id") or r.json()["data"]["customer_id"]
        return member.pinelabs_customer_id

    # ------------------------------------------------------------ block / refresh / capture / release
    def create_block(self, member: Member, amount: int, validity_days: int, reference: str) -> Authorisation:
        customer_id = self.ensure_customer(member)
        paisa = amount * 100
        r = self.http.post(f"{self.sub_base}/api/v1/public/subscriptions/ot", headers=self._headers(subscription=True),
                           json={"merchant_subscription_reference": f"quorum-{reference}-{member.id}-{uuid.uuid4().hex[:6]}",
                                 "customer_id": customer_id,
                                 "plan_details": {"amount": paisa, "currency": "INR", "validity_days": validity_days,
                                                  "description": f"Quorum trip share — blocked, debited only at quorum"},
                                 "callback_url": self.callback_url,
                                 "merchant_metadata": {"source": "quorum", "bundle": reference, "member": member.id}})
        r.raise_for_status()
        sub = r.json().get("data", r.json())
        subscription_id, order_id = sub["subscription_id"], sub["order_id"]
        # Register the mandate against the order. In UAT the simulator may auto-approve; in production the
        # member gets a UPI intent / challenge_url and approves in their app.
        r2 = self.http.post(f"{self.base}/api/pay/v1/orders/{order_id}/payments", headers=self._headers(),
                            json={"payments": [{
                                "payment_method": "UPI",
                                "merchant_payment_reference": f"pay-{uuid.uuid4().hex[:10]}",
                                "payment_amount": {"value": paisa, "currency": "INR"},
                                "payment_option": {"upi_details": {"txn_mode": "INTENT"}},
                                "mandate_info": {"request_type": "CREATE_MANDATE"}}]})
        r2.raise_for_status()
        return Authorisation(member_id=member.id, bundle_id=reference, amount=amount, status=AuthStatus.PENDING,
                             rail_ref=subscription_id, order_ref=order_id, created_at=clock.now())

    def refresh(self, auth: Authorisation) -> Authorisation:
        r = self.http.get(f"{self.sub_base}/api/v1/subscriptions/ot/{auth.rail_ref}", headers=self._headers(subscription=True))
        r.raise_for_status()
        status = r.json().get("data", r.json()).get("status", "").upper()
        if status in ("ACTIVE", "RESUMED") and auth.status == AuthStatus.PENDING:
            auth.status = AuthStatus.BLOCKED
        elif status in ("EXPIRED", "COMPLETED") and auth.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
            auth.status = AuthStatus.EXPIRED
        return auth

    def capture(self, auth: Authorisation, amount: int) -> Authorisation:
        assert amount <= auth.amount, "presentation above ceiling"
        r = self.http.post(f"{self.sub_base}/api/v1/public/presentations", headers=self._headers(subscription=True),
                           json={"subscription_id": auth.rail_ref,
                                 "amount": {"value": amount * 100, "currency": "INR"},
                                 "merchant_presentation_reference": f"present-{uuid.uuid4().hex[:10]}",
                                 "merchant_retry_id": f"retry-{uuid.uuid4().hex[:10]}"})
        r.raise_for_status()
        # Presentation returns PENDING; the webhook / fetch moves it to SUCCESS. We record CAPTURED optimistically
        # and let the webhook handler (app/main.py) reconcile. TODO: block on fetch-until-SUCCESS for the demo.
        auth.status, auth.captured_amount = AuthStatus.CAPTURED, amount
        return auth

    def release(self, auth: Authorisation) -> Authorisation:
        # No cancel documented for OTM; mandate lapses at validity_days. Record locally.
        auth.status = AuthStatus.RELEASED
        return auth
