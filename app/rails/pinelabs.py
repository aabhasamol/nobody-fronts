"""Pine Labs Online — the payments rail, mapped onto what their API has.

Read from pinelabs.com/docs (Sept 2026). The docs host is blocked from the sandbox this was written in, so the
payment-link request below is copied from the reference page and everything else is marked [verify].

The block is a PAYMENT LINK
    One `POST /api/pay/v1/paymentlink` per member, for share × (1 + overshoot) in paisa, `pre_auth: "true"`,
    `allowed_payment_methods: ["CARD", "UPI"]`, `expire_by` = the authorisation deadline. The member opens the
    link and chooses on Pine Labs' hosted page: a credit card (a pre-authorisation, one capture within 5–7 days,
    EMI tenures offered on the page) or UPI (a one-time mandate: blocked in the account, one capture, partial
    allowed, up to ₹1 lakh, up to 60 days). The link dies with the deadline; nothing is charged until Quorum
    captures. Merchant ID must be configured for Pay by Link and pre-authorization.
    [verify] whether pay-by-link with pre_auth runs UPI as a one-time mandate or as an immediate intent debit;
    if the latter, keep UPI on the OTM subscription flow (`_create_otm`, below).

    UPI Reserve Pay — block once, debit many times — is the instrument the engine's headroom idea needs for
    UPI (the share at booking, then a re-booking difference without a new tap). It is a separate product; the
    subscription-style flow in `_create_otm` is the closest code we have. [verify] its endpoints.

What exists (and how Quorum uses it)
    Card pre-authorisation   Orders / payment links with pre_auth; Capture Order; Cancel Order   → CARD_PREAUTH
    UPI One-Time Mandate     block, one capture (partial ok), release the rest, ₹1 lakh, 60 days  → UPI_OTM
    UPI Reserve Pay          block once, multiple debits against the reserved amount               → UPI_RESERVE
    Credit / Debit / Cardless EMI   allowed_payment_methods CREDIT_EMI etc.; Offer Discovery by BIN + amount;
                             Quorum is settled in full at once, the issuer collects instalments     → emi_months
    BNPL (LazyPay)           eligibility by mobile/email + amount, OTP; needs MID config             → not used
    Tokenisation             card-on-file tokens: a top-up on a card is one tap, not a re-entry
    Payouts                  IMPS / NEFT / RTGS / UPI to verified beneficiaries, instant or scheduled → pay_supplier
    Split settlements        split_info on a link/order, per-sub-merchant amounts, on_hold + Release Settlement
                             — the nearest existing thing to the escrow we ask for
    P3P                      Pine Labs' agentic payment protocol on UPI SBMD/OTM: one consumer, one mandate, an
                             agent spends inside it. Quorum is the N-payer case of that.

What does not exist (the walls)
    * One capture per card hold and per UPI OTM: only Reserve Pay keeps headroom live after the share is taken.
      On a card, a re-booking beyond the carrier's refund is a fresh authorisation (one tap on the saved token).
    * EMI and BNPL are checkout-time methods, not blocks: a member who wants EMI is pre-authorised like any card,
      and at booking the hold is voided and an EMI checkout for the exact share is completed (AFA) [verify].
    * No atomic capture across N payers and no per-order escrow across payers: `Engine._capture_all` loops and
      refunds on failure. Split settlement holds funds per sub-merchant, not per group.
    * The member cannot revoke a mandate or a hold from their own app; they ask Quorum (`Engine.member_withdraws`).
    * Cards carry MDR (≈ 1.5–2.5 % on credit); UPI P2M is free. Quorum eats it or prices it.
    * Card holds live 5–7 days: the vote + authorisation window (≤ 72 h) fits; a plan that drags does not.

Verbs → endpoints ([verify] every path and field against pinelabs.com/docs/online-payments/api)
    create_block   POST /api/pay/v1/paymentlink                       (below; the member picks card or UPI)
    refresh        GET  /api/pay/v1/paymentlink/{payment_link_id}     → status, order_id, payment method used
    capture        POST /api/pay/v1/orders/{order_id}/capture          (Capture Order; partial allowed on OTM)
    release        POST /api/pay/v1/orders/{order_id}/cancel           (Cancel Order / release the block)
    refund         POST /api/pay/v1/refunds/{order_id}
    pay_supplier   POST /api/payouts/v1/payouts                       (Payouts API; beneficiary registered first)
    emi_offers     POST /api/pay/v1/offers/discovery                  (Offer Discovery)

Environment
    PINELABS_BASE=https://pluraluat.v2.pinepg.in   (UAT — free developer keys from dashboardv2.pluralonline.com)
    PINELABS_CLIENT_ID, PINELABS_CLIENT_SECRET, PINELABS_MERCHANT_ID
    PINELABS_CALLBACK_URL=https://<your-tunnel>/webhooks/pinelabs

Amounts: Pine Labs takes paisa. We hold INR everywhere else and convert here.
"""
from __future__ import annotations
import os
import uuid
from datetime import datetime, timedelta, timezone
import httpx
from ..clock import clock
from ..models import Member, Authorisation, AuthStatus, Payout, INSTRUMENTS, inr
from .base import PaymentsRail, CaptureFailed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(d: datetime) -> str:
    return d.replace(microsecond=0).isoformat() + "Z"


class PineLabsPayments(PaymentsRail):
    def __init__(self):
        self.base = os.environ.get("PINELABS_BASE", "https://pluraluat.v2.pinepg.in").rstrip("/")
        self.client_id = os.environ["PINELABS_CLIENT_ID"]
        self.client_secret = os.environ["PINELABS_CLIENT_SECRET"]
        self.merchant_id = os.environ["PINELABS_MERCHANT_ID"]
        self.callback_url = os.environ.get("PINELABS_CALLBACK_URL", "https://example.invalid/webhooks/pinelabs")
        self._token: str | None = None
        self.http = httpx.Client(timeout=20)
        self._pool = 0                                         # local mirror; the truth is the settlement report
        self.payouts: list[Payout] = []
        self.links: dict[str, dict] = {}                       # rail_ref → what we know about the link / order

    # ------------------------------------------------------------ auth
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json",
                "Accept": "application/json", "Request-ID": str(uuid.uuid4()), "Request-Timestamp": _now_iso()}

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

    @staticmethod
    def _data(r: httpx.Response) -> dict:
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else body

    # ------------------------------------------------------------ the block: one payment link per member
    def create_block(self, member: Member, amount: int, validity_days: int, reference: str) -> Authorisation:
        """A pre-authorised payment link for the cap. The member chooses card or UPI on the hosted page."""
        first, _, last = member.name.partition(" ")
        expire_by = clock.now() + timedelta(days=validity_days)
        body = {
            "amount": {"value": amount * 100, "currency": "INR"},
            "description": f"Quorum — your share of the trip, blocked now, debited only when everyone is in",
            "expire_by": _iso(expire_by),
            "allowed_payment_methods": ["CARD", "UPI"],
            "pre_auth": "true",
            "is_mcc_transaction": "false",
            "merchant_payment_link_reference": f"quorum-{reference}-{member.id}-{uuid.uuid4().hex[:6]}",
            "customer": {"email_id": f"{member.id}@quorum.invalid", "first_name": first, "last_name": last or "-",
                         "mobile_number": member.phone, "country_code": "91",
                         "merchant_customer_reference": f"quorum-{member.id}"},
            "callback_url": self.callback_url,
            "failure_callback_url": self.callback_url,
            "merchant_metadata": {"source": "quorum", "plan": reference, "member": member.id},
        }
        r = self.http.post(f"{self.base}/api/pay/v1/paymentlink", headers=self._headers(), json=body)
        r.raise_for_status()
        d = self._data(r)
        link_id = d.get("payment_link_id") or d.get("id") or body["merchant_payment_link_reference"]   # [verify]
        self.links[link_id] = {"url": d.get("payment_link_url") or d.get("url"), "order_id": d.get("order_id"),
                               "member": member.id, "amount": amount}
        return Authorisation(member_id=member.id, plan_id=reference, amount=amount, status=AuthStatus.PENDING,
                             rail_ref=link_id, order_ref=d.get("order_id"), created_at=clock.now(), expires_at=expire_by)

    def link_url(self, auth: Authorisation) -> str | None:
        """What goes in the member's DM: the hosted page where they pick card or UPI."""
        return self.links.get(auth.rail_ref, {}).get("url")

    def refresh(self, auth: Authorisation) -> Authorisation:
        r = self.http.get(f"{self.base}/api/pay/v1/paymentlink/{auth.rail_ref}", headers=self._headers())   # [verify]
        r.raise_for_status()
        d = self._data(r)
        status = str(d.get("status") or d.get("payment_link_status") or "").upper()
        auth.order_ref = d.get("order_id") or auth.order_ref
        method = str(d.get("payment_method") or (d.get("payments") or [{}])[0].get("payment_method") or "").upper()
        if status in ("PROCESSED", "PAID", "CAPTURED", "SUCCESS") and auth.captured_amount == 0:
            auth.instrument = "PREPAID"                        # a method that cannot hold paid at once: it is in the pool
            self._pool += auth.amount
        elif method.startswith("CARD") or "EMI" in method:
            auth.instrument = "CARD_PREAUTH"
            auth.expires_at = min(auth.expires_at, clock.now() + timedelta(days=7)) if auth.expires_at else None
        elif method.startswith("UPI"):
            auth.instrument = "UPI_OTM"                        # Reserve Pay needs its own flow; see module docstring
        if status in ("AUTHORIZED", "ACTIVE", "PAID_PENDING_CAPTURE", "PROCESSED", "PAID", "CAPTURED", "SUCCESS") \
                and auth.status == AuthStatus.PENDING:
            auth.status = AuthStatus.BLOCKED
        elif status in ("EXPIRED",) and auth.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
            auth.status = AuthStatus.EXPIRED
        elif status in ("CANCELLED", "VOIDED") and auth.status in (AuthStatus.PENDING, AuthStatus.BLOCKED):
            auth.status = AuthStatus.RELEASED
        return auth

    def emi_offers(self, amount: int) -> list[tuple[int, int]]:
        """Offer Discovery: tenures for this amount. Without a BIN it returns the generic bank tenures. [verify]"""
        try:
            r = self.http.post(f"{self.base}/api/pay/v1/offers/discovery", headers=self._headers(),
                               json={"amount": {"value": amount * 100, "currency": "INR"}, "payment_method": "CREDIT_EMI"})
            r.raise_for_status()
            out = []
            for t in self._data(r).get("tenures", []):
                out.append((int(t.get("tenure_value", 0)), int(round(int(t.get("monthly_emi", {}).get("value", 0)) / 100))))
            return [o for o in out if o[0]]
        except httpx.HTTPError:
            return []

    # ------------------------------------------------------------ debit / release / refund
    def capture(self, auth: Authorisation, amount: int) -> Authorisation:
        assert auth.captured_amount + amount <= auth.amount, "debit above the authorised cap"
        assert auth.multi_debit or auth.captured_amount == 0, f"{auth.instrument} allows one capture"
        assert auth.order_ref, "no order behind this block yet — refresh first"
        if auth.prepaid:                                       # already paid in full; keep what is owed, refund the rest later
            auth.status = AuthStatus.CAPTURED
            auth.captured_amount += amount
            return auth
        r = self.http.post(f"{self.base}/api/pay/v1/orders/{auth.order_ref}/capture", headers=self._headers(),   # [verify]
                           json={"merchant_capture_reference": f"cap-{uuid.uuid4().hex[:10]}",
                                 "capture_amount": {"value": amount * 100, "currency": "INR"}})
        if r.status_code >= 400:
            raise CaptureFailed(f"capture on {auth.order_ref} rejected: {r.status_code} {r.text[:120]}")
        # TODO: poll the order until PROCESSED; a capture that ends FAILED must raise CaptureFailed so the engine
        # rolls the others back. Recorded optimistically for now; the webhook (app/main.py) reconciles.
        auth.status = AuthStatus.CAPTURED
        auth.captured_amount += amount
        self._pool += amount
        return auth

    def release(self, auth: Authorisation) -> Authorisation:
        """Cancel Order releases a card hold or the uncaptured balance of a UPI OTM. [verify]"""
        if auth.order_ref:
            r = self.http.post(f"{self.base}/api/pay/v1/orders/{auth.order_ref}/cancel", headers=self._headers(),
                               json={"merchant_cancel_reference": f"rel-{uuid.uuid4().hex[:10]}"})
            r.raise_for_status()
        auth.status = AuthStatus.RELEASED
        return auth

    def refund(self, auth: Authorisation) -> Authorisation:
        """Return everything captured on this block. [verify] endpoint and body."""
        r = self.http.post(f"{self.base}/api/pay/v1/refunds/{auth.order_ref}", headers=self._headers(),
                           json={"merchant_refund_reference": f"refund-{uuid.uuid4().hex[:10]}",
                                 "refund_amount": {"value": auth.captured_amount * 100, "currency": "INR"},
                                 "merchant_metadata": {"source": "quorum", "reason": "group capture rolled back"}})
        r.raise_for_status()
        self._pool -= auth.captured_amount
        auth.status, auth.captured_amount = AuthStatus.REFUNDED, 0
        return auth

    # ------------------------------------------------------------ the pool → suppliers
    def pay_supplier(self, supplier: str, amount: int, purpose: str, reference: str) -> Payout:
        """Payouts API: IMPS/NEFT/UPI to a registered beneficiary — a homestay, a driver, a local vendor. Airlines
        and listed hotels are paid through a B2B travel wallet topped up from the settlement account instead.
        Until beneficiaries are registered this records the instruction so the ledger stays honest. [verify]"""
        assert amount <= self._pool, f"pool ₹{inr(self._pool)} cannot cover ₹{inr(amount)} to {supplier}"
        self._pool -= amount
        p = Payout(supplier=supplier, amount=amount, purpose=purpose, reference=reference,
                   method="INSTRUCTED", status="INSTRUCTED", at=clock.now())
        self.payouts.append(p)
        return p

    def receive_refund(self, supplier: str, amount: int, reference: str) -> int:
        self._pool += amount
        return self._pool

    def pool(self) -> int:
        return self._pool
