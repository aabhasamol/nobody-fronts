# Rails — what exists, what we built on it, what we are asking for

The Ken's Round-2 brief: look at what Pine Labs, Delhivery and Gnani already offer, and picture the agent
running on them. This is that picture, with the seams marked.

## 1. Payments — Pine Labs Online (load-bearing)

### What exists today (pinelabs.com/docs, Sept 2026)

| Product | What it does | Where Quorum uses it |
|---|---|---|
| **Payment links** (`POST /api/pay/v1/paymentlink`) | A hosted page per payer: `amount`, `expire_by`, `allowed_payment_methods` (CARD, UPI, EMI, BNPL…), `pre_auth`, `customer`, `callback_url`, optional `split_info` | **The block.** One link per member for share × (1 + overshoot), `pre_auth: "true"`, CARD + UPI, expiring at the authorisation deadline. The member picks card or UPI on the page. Nothing is charged until Quorum captures |
| **Card pre-authorisation** | `pre_auth` on an order or link; Capture Order; Cancel Order; the hold lives 5–7 days; cards and PayByPoints only | Credit-card members. One capture at booking; the rest of the hold is released |
| **UPI One-Time Mandate** | Block up to ₹1 lakh for up to 60 days with one UPI PIN; one capture, partial allowed; merchant releases the rest; the customer cannot revoke it from their app | UPI members who want one debit |
| **UPI Reserve Pay** | Block once, debit many times against the reserved amount | UPI members by default: the share at booking, then a re-booking difference inside the headroom, no new tap |
| **Credit / Debit / Cardless EMI** | `CREDIT_EMI` etc. as payment methods; Offer Discovery by card BIN + amount returns tenures; **the merchant is settled in full at once**, the issuer collects instalments | A card member's EMI choice. The pool is whole either way |
| **BNPL** (LazyPay) | Eligibility by mobile/email + amount, OTP; MID must be enabled | Not used: checkout-time, small limits |
| **Tokenisation** | Card-on-file tokens under RBI's rules | A top-up on a card is one tap on the saved token, not a re-entry |
| **Settlements** | Captures settle to the merchant account; reconciliation APIs | **The pool** is that account |
| **Payouts** | IMPS / NEFT / RTGS / UPI to verified beneficiaries, instant or scheduled, bulk | The pool pays a homestay, a driver, a local vendor. Airlines and listed hotels go through a B2B travel wallet |
| **Split settlements** | `split_info` on a link or order: per-sub-merchant amounts, `on_hold`, a Release Settlement call | A stay that is onboarded as a sub-merchant could be paid straight from the members' captures and held until release — the nearest existing thing to the escrow we ask for |
| **P3P** | Pine Labs' agentic payment protocol on UPI's block-now-debit-later mandates: one consumer authorises a mandate, an agent spends inside it | Quorum is exactly this, for N consumers on one plan |

### How the prototype uses it

`app/rails/pinelabs.py` maps our verbs onto those (every path marked [verify] until run against UAT):

| Quorum verb | Pine Labs call | Notes |
|---|---|---|
| `create_block(member, amount, validity_days)` | `POST /api/pay/v1/paymentlink` — `pre_auth: "true"`, `allowed_payment_methods: ["CARD","UPI"]`, `expire_by` = deadline | One link **per yes-voter**; the member chooses the instrument on the page. Only people who voted yes get one |
| `refresh(auth)` | `GET /api/pay/v1/paymentlink/{id}` | `AUTHORIZED` ⇒ `BLOCKED`, and the method used tells us the instrument (card hold / UPI mandate) |
| `capture(auth, amount)` | `POST /api/pay/v1/orders/{order_id}/capture` | One capture on a card hold or an OTM (partial allowed); many on Reserve Pay |
| `release(auth)` | `POST /api/pay/v1/orders/{order_id}/cancel` | Releases a card hold or the uncaptured balance of a mandate |
| `refund(auth)` | `POST /api/pay/v1/refunds/{order_id}` | When a later member's capture fails and the successful ones must be returned |
| `emi_offers(amount)` | Offer Discovery | The tenures quoted in the authorisation DM |
| `pay_supplier(...)` | Payouts API | Pool → homestay / vendor. Recorded as an instruction until beneficiaries are registered |

The overshoot the organiser sets is the block's headroom: one number governs both the proposal ("nothing
above the rough budget × (1 + overshoot) a head") and the money ("block share × (1 + overshoot), debit the
share"). After the vote the binding number is the yes-voters' own: their count × the lowest ceiling among
them is the trip's budget, and the plan re-sized for exactly them must fit it. A fare that moved since the
vote, or a cancelled flight, is absorbed inside the headroom **on UPI Reserve Pay without a new tap**; on a
card hold or a one-time mandate the headroom is released at capture, so anything beyond the carrier's refund
is a fresh authorisation asked of that member alone (`Engine._ask_top_up`), one tap on the saved token.

### The pool

Quorum is the merchant of record. Every block is created by Quorum, so every capture settles into Quorum's
merchant account: that account is the pool. From it Quorum pays each supplier (`Engine._pay`, one logged
payout per booking: the stay's rooms × rate × nights, each ticket, each re-booked ticket). A carrier's refund
on a cancelled leg comes back into the pool and the replacement ticket is paid from it. The pool can never go
negative — `pay_supplier` refuses — so nobody fronts, Quorum included. The organiser is never in the money
path; pooling in a friend's account would lose block-then-debit, hit UPI limits, and move the trust problem
rather than remove it.

Two more things the pool does. A **payer may pay for several heads** (a family): their share is per head × their
party, their legs carry that many seats, and the budget is heads × the lowest per-head ceiling. And when a
member pays a **link with a method that cannot hold** (UPI intent, netbanking, a wallet), the money arrives at
once and waits in the pool until booking, refunded in full if the trip lapses; the spare above their share is
cash headroom, so a re-booking needs no tap. That prepaid **float is Quorum's**: the engine prices it at a
liquid-fund rate in the rail log (`FLOAT_RATE`) as an idea, gains and losses both Quorum's, not a promise.

### The ask

A **group order with escrow**: N blocks (card holds, UPI mandates, Reserve Pay) bound to **one** merchant
order, carrying

* a shared expiry,
* an **atomic** capture: when the merchant presents, either every active block is captured or none is,
* settlement into a per-order escrow, released to the suppliers (or to the merchant's wallet) only when the
  order is secured, back to every payer when it is not,
* and a single webhook: `GROUP_ORDER_SECURED` when everyone is in, `GROUP_ORDER_LAPSED` when not.

Framed for Pine Labs: split settlement already holds one payer's money per sub-merchant and releases it on
a call, and P3P already lets an agent spend inside one consumer's mandate. Put the two together for N payers
and you have Quorum's rail. The order object already holds one block; let it hold five, with a rule.

### The wall — what breaks today

`Engine._capture_all` loops `capture()` once per member who is in. In production a capture can fail (bank
timeout, insufficient funds after a parallel debit, a card issuer reversing the hold). If the fourth of four
fails, three people have paid for a trip that cannot be booked at the quoted group rate. The engine does the
only thing it can: refund the three, release the rest, tell the group, and offer a re-run. `python demo.py`
shows it (the third transcript). That is a compensating rollback with three counterparties and three refund
timelines (card refunds take days), not atomicity. Nothing in the documented API lets us express "all or
nothing" across payers. That is the sentence we would put in front of Pine Labs.

Smaller walls, confirmed from the docs rather than guessed: one capture per card hold and per OTM (only
Reserve Pay keeps headroom live); EMI and BNPL are checkout-time methods, not blocks; card holds live 5–7 days;
a UPI mandate caps at ₹1 lakh; the member cannot revoke a mandate or a hold from their own app, they ask
Quorum; cards carry MDR (≈ 1.5–2.5 % on credit) where UPI is free.

## 2. Voice — Gnani / Inya (two jobs)

### Where we call, and where we don't

One test, applied to every place a call was tempting: **call only when the other side cannot be reached by
an API or a message in time, and the answer changes what the agent does next.** Members' constraints,
reminders before a deadline, facts about a listed hotel, flight status: all fail the test (WhatsApp or an API
already does it, and calling friends to chase them is the chasing the product removes). Two things pass:

1. **Supplier calls.** A homestay, a small guesthouse, a wedding's room block: no online inventory, phone
   only. Before the vote: rooms for the party, the group rate, refund terms, and a 48-hour hold. On the
   travel day: a late-arrival notice so the room does not go to a walk-in at 2 am.
2. **Escalation.** A member's flight is cancelled and every alternative costs more than they authorised.
   Text first, with the options. If there is no reply in 20 minutes, call, read the options, take the choice.

`VoiceRail` has exactly those methods (`call_supplier`, `notify_late_arrival`, `escalate_member`) and the
engine calls them in exactly those places (`Engine._supplier_call`, `_rebook`, `_escalate`).

### What exists today

Gnani's API is two verbs. The **Speech APIs** (brand: Vachana; key prefix `vach_`): **text-to-speech**
(Timbre v2, `POST /api/v1/tts/inference`, Hindi and Indian-English voices) and **speech-to-text** (Prisma
v2.5, `POST /stt/v3`, `language_code` per request), over REST, with WebSocket variants for streaming.
Pricing: ₹27 per audio-hour transcribed, ₹27 per 10,000 characters synthesised, 60 requests a minute. It does
not dial phones, run a conversation, or extract fields. We hold this key (`app/keys.py`).

### One key, two verbs, and a phone line that is not Gnani's

A P0 call is assembled in `app/rails/gnani.py`:

    telephony dials → play the TTS of each line the agent says → record the other side → STT
    → read the fields out of the words

* **Speak.** Each line the agent says is synthesised once and cached by content (`gnani_speech.py`). The
  supplier script is four Hindi questions (419 characters ≈ ₹1.13 the first time, ₹0 after); the late-arrival
  notice is one line; the escalation script is English with the options read out.
* **Listen.** Each reply is transcribed (`language_code` hi-IN for suppliers, en-IN for members). A
  one-minute answer ≈ ₹0.45.
* **Read.** Rules in `gnani.py` turn words into the fields the engine acts on: yes/no with the last verdict
  winning ("haan… matlab nahi" is a no, "koi dikkat nahi" is a yes), rupee amounts from digits and
  hazaar/sau, refund terms as the sentence that mentions a refund, a 48-hour hold unless refused, and
  option one/two/ek/do/pehla/doosra for an escalation. Number-words ("teen hazaar") defeat them today.
* **The phone line** is a separate seam, `Telephony` (dial → play → listen → hang up), because it is a
  different vendor: Exotel or Twilio, a day of work. The default `FileTelephony` writes the agent's audio to
  `cache/calls/<ref>/` and takes the reply from recordings in `cache/replies/<callee>/` (one per question, or
  one for the whole call; a `.txt` is a typed reply). No recordings ⇒ the call is honestly NO_ANSWER.

That is how the demo runs the real Gnani loop for about ₹2 without a carrier: a teammate records the homestay
owner's answers on a phone, drops the files in, `python scripts/speech_demo.py --call …` prints Gnani's
transcript and the fields, and `QUORUM_VOICE=gnani python demo.py` runs the whole product on it. Every
billable call goes through `CallBudget` (40 per checkout) because the key is committed.

### How the engine uses it

Only through `Engine._call`, which takes one of three `P0_CALLS` reasons and refuses anything else:
`SUPPLIER_AVAILABILITY` (retry once on no answer; UNAVAILABLE ⇒ next stay and never call this one again;
CONFIRMED ⇒ the rate from the call replaces the listing and the hold is recorded), `LATE_ARRIVAL`, and
`DISRUPTION_UNANSWERED` (the choice comes back as a number; the engine then asks that member alone for a
top-up mandate). Every call is a P0 event in the transcript; nothing else is.

### The ask

1. **Normalised entities in the transcript.** Amounts, dates, times, yes/no, ordinal choices, returned as
   values alongside the words — so "teen hazaar do sau" arrives as 3200 and "haan… matlab nahi" as a no —
   instead of the rules we maintain in `gnani.py`. This is the structured-extraction ask, sized to the API
   Gnani actually sells.
2. **Streaming, duplex STT/TTS** on the WebSocket endpoints, so a live call does not wait for whole clips
   and the agent can be interrupted.
3. **Code-switch robustness** for Konkani/Hindi/English in one sentence, and a per-request hint that the
   speaker is a small-business owner quoting prices.

### The wall

Gnani gives us the voice, not the phone line. Placing the call, and the consent and DND rules for calls to
businesses and transactional calls to members, sit with the carrier and with us. That is fine for Round 2 —
the demo is honest about it — and it is the production question to raise: a hold agreed on a call is only as
good as the recording and the transcript we keep of it.

## 3. Logistics — Delhivery (distances, not parcels)

Delhivery moves parcels. Nothing in this opening is a parcel: the "logistics" of a group trip is seat
inventory and re-booking on cancellation, which the agent treats as execution behind `LogisticsRail`
(`MockLogistics` fills it until a fare API or an OTA's B2B feed is dropped in).

What Delhivery does have that the plan needs is **Maps**: geocoding and distances. The occasion decides how
stays are ranked — a wedding or an offsite ranks by distance to the venue, a pilgrimage by walking distance,
and the arrival buffer from airport to stay has to be real for "arrive before the first function" to mean
anything. `Stay.km_to_venue` carries that number; today it is hand-written. That is the one integration we
would ask for. Last-mile road accessibility (can a cab reach this homestay?) is today a question on the
supplier call. Wedding logistics — outfits or gifts shipped to the venue — is a natural parcel use we are
*not* claiming for Round 2.

## 4. Where value moves (for the strategy write-up)

* **Drains from:** the OTA checkout. MakeMyTrip's funnel monetises one card, one click, and the indecision
  before it (fare locks, price alerts, pay-later). A private vote and five separate mandates on one plan is
  a different funnel.
* **Drains from:** the organiser's credit-card float and the reward points that came with fronting
  ₹80,000. Interviews should test whether some organisers *like* fronting for that reason; it is the
  adoption cost of "nobody fronts".
* **Pools at:** the payment rail that owns the group mandate — whoever holds the block holds the decision.
  That is why the primitive is worth more than the travel vertical it starts in.
