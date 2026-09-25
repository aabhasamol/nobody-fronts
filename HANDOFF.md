# Handoff — for the next builder (human or Claude Code)

Read `README.md`, then `docs/round2-answers.md`, then `app/engine.py`, then `docs/rails.md`. Then this.

## The one thing to protect

The product is the loop in `Engine`: budget + overshoot → private gathering → one itinerary that fits →
private vote, tally only → yes-voters authorise share × (1 + overshoot) → debit each share, all or nothing →
book → re-book inside the cap. Every change should make that loop more real (a real rail, a real channel,
real fares), not wider. Do not add features the loop does not need.

## Conventions

* Rails are the only seam. New capability goes behind `VoiceRail` / `PaymentsRail` / `LogisticsRail` in
  `app/rails/base.py`, with a mock first and the real adapter second. The engine must keep passing
  `tests/` against mocks.
* Calls are for P0 things. The engine dials only through `Engine._call`, which takes a reason from
  `Engine.P0_CALLS` (three entries) and refuses anything else; every event carries P0/P1/P2. Adding a fourth
  reason is a product decision — re-read the table in `docs/round2-answers.md` §0 first. Members are never
  called to chase a vote or an approval.
* Gnani is text-to-speech and speech-to-text, nothing more. `app/rails/gnani.py` builds a call from those two
  verbs plus a `Telephony` seam; the carrier is a different vendor and goes behind that seam. The key is
  committed in `app/keys.py` on purpose (private repo, one month, one holder, budget-capped) — revoke it on the
  Gnani dashboard when the competition ends.
* Every user-visible sentence lives in `app/messages.py`. Short sentences, one ask, always a default and a
  deadline, never a group poll. The group gets four kinds of post: kickoff, tally, booking, disruption.
* Money is INR integers in the engine; adapters convert to paisa at the edge. A member is never debited
  more than `Authorisation.amount` (+ a top-up they approved themselves).
* The pool is Quorum's merchant account. Captures settle into it, `Engine._pay` pays suppliers from it, a
  carrier's refund comes back into it, and `PaymentsRail.pay_supplier` must refuse to take it negative. The
  organiser is never in the money path; neither is Quorum's own money.
* A member is a payer; `Constraint.party` is how many heads they pay for. `Plan.per_head` is one of their
  people, `Plan.share` is what they owe, legs carry `seats`. Votes and majority stay per payer.
* Blocks come in four instruments (`models.INSTRUMENTS`): UPI Reserve Pay (multi-debit), UPI OTM (one
  capture), a card hold (one capture, 7 days) and a prepaid link (money in the pool now, refunded on lapse).
  Only the engine's `_headroom` needs to know the difference. The prepaid float is priced in the rail log at
  `FLOAT_RATE` as an idea; nothing invests anything.
* Personas are deduced in `lore.persona_of` (anyone paying for three or more, or a family occasion ⇒
  families) and only ever shape defaults. Place lore lives in `app/lore.py`; `Engine._hook` picks one line
  per person per message and never repeats it. Replace the table with a real source (a destination guide,
  a reels feed) and nothing else changes. Nudges are P2 texts riding on messages that had to go anyway.
* BNPL is a lender's product on the rail (LazyPay via Pine Labs), never Quorum fronting: the instrument
  `BNPL` settles Quorum in full at capture. Its limit (₹30,000 in the mock) is [verify] with LazyPay.
* The yes-voters set the budget: `Trip.set_budget` = the heads they pay for × the lowest ceiling among them. Before
  the vote the proposal is held to the organiser's rough figure + overshoot per head; after it, the total is
  the rule (`Engine._fit`). Re-sizing for whoever is in happens in `Engine._retarget`: rooms today; a car or
  a group activity would re-size in the same place.
* Time comes from `app/clock.py`. Never call `datetime.now()` in engine code.
* `Event` log is the transcript. If it happened and the group or a member saw it, it goes through `_say`.
* Every phone-only stay that says no or never answers goes in `trip.stays_out` and is not called again.
  Calls cost money; the mock hides that.

## Next tasks, in order

1. **Pine Labs UAT end to end** (highest value; unblocks the Round-2 demo on a real rail)
   * Sign up at the Pine Labs Online dashboard, get UAT client id/secret/merchant id; ask for Pay by Link,
     pre-authorization, UPI OTM / Reserve Pay and Payouts to be enabled on the MID.
   * Set `QUORUM_PAYMENTS=pinelabs`, run `demo.py`. The adapter creates one pre-authorised payment link per
     member; fix response field names (`payment_link_id`, `payment_link_url`, `order_id`, `status`, the
     method used) and the Capture / Cancel Order paths against the live reference — every one is [verify].
   * Confirm whether pay-by-link with `pre_auth` runs UPI as a one-time mandate or as an immediate debit; if
     immediate, route UPI to the OTM / Reserve Pay flow and keep the link for cards.
   * EMI: confirm the flow for a member who chose EMI — void the hold and complete a `CREDIT_EMI` checkout
     for the exact share at booking (needs the member's AFA), or capture and let the issuer convert.
   * Make `capture()` block on presentation status until `SUCCESS` and raise `CaptureFailed` on `FAILED`,
     instead of recording `CAPTURED` optimistically. Wire `/webhooks/pinelabs` to reconcile.
   * **Confirmed from the docs, no longer to verify:** an OTM takes one capture (partial allowed) and the
     merchant releases the rest; the member cannot revoke from their app; Reserve Pay is the multi-debit
     instrument. **Still to verify:** Reserve Pay's endpoints, the refund body, and Payouts beneficiary
     registration.
   * Then run the wall for real: `payments.fail_capture_for` has no UAT equivalent, so use a test VPA that
     declines. Showing the rollback fail honestly is worth more than pretending it cannot.
   * **Payouts.** `PineLabsPayments.pay_supplier` only records an instruction. The real thing is a B2B travel
     wallet (TBO / Cleartrip B2B) topped up from the settlement account for flights and listed hotels, and
     UPI or a bank transfer for a homestay. Wire one; the engine's `_pay` does not change.

2. **Gnani — run the real loop on recordings, then put a phone line behind it**
   * `python scripts/speech_demo.py --dry-run`, then without the flag: the four Hindi questions go through
     Gnani TTS once (≈ ₹1.13, cached after). Listen to `cache/tts/*.wav`; fix wording the voice mangles.
   * Have a teammate play the homestay owner on a phone: four short recordings answering the four questions
     (code-switching, a price that changes mid-sentence, a "haan… matlab nahi"). Drop them in
     `cache/replies/dona-maria-homestay-assagao/1.wav … 4.wav`. Run
     `python scripts/speech_demo.py --call cache/replies/dona-maria-homestay-assagao` and read the transcript
     and the fields. Then `QUORUM_VOICE=gnani python demo.py` runs the whole product on real speech. Fill the
     three voice failure rows in `docs/round2-answers.md` §3 from what the parsers in `gnani.py` got wrong
     (number-words, negation, mixed languages). Budget ≈ ₹2; `CallBudget` stops you at 40 calls regardless.
   * The phone line: implement `Telephony.dial` for Exotel (Indian numbers, a webhook per call) or Twilio —
     play the TTS clip, record the reply, return it. Three methods; the rest of the rail does not change.
     Streaming STT/TTS over Gnani's WebSocket endpoints makes the call feel live; the REST loop is fine for
     a recorded demo.
   * Note: from a Claude Code cloud session `api.vachana.ai` is blocked by the egress policy; run the speech
     steps on a laptop.

3. **A real channel.** The demo UI stands in for WhatsApp. Telegram bot (an evening), WhatsApp Cloud API with
   a test number (a day plus Meta approval), or keep the UI and record a screen demo. The engine does not
   change: a channel adapter turns inbound text into `engine.gather` / `vote` / `member_chooses` and
   outbound `Event`s into sends. Votes and options must arrive as DMs, never in the group.

4. **Fares.** Replace `MockLogistics.search_legs/search_stays` with anything live behind the same interface.
   Keep the mock for tests. `Leg.quoted` vs `Leg.price` is already the fare-drift record.

5. **Distances.** `Stay.km_to_venue` is hand-written. Delhivery Maps (or any geocoder) makes the wedding /
   offsite ranking real. This is Delhivery's whole role in this opening.

6. **What the answers promise that the code only half does** — pick up whichever the demo needs:
   * §3 #2: the member whose own travel breaks the limit is *told*, not offered trains or other dates.
   * §3 #9: the booking-time top-up has no short clock; nothing happens if the member ignores it.
   * §3 #10: when a re-hold fails, the plan goes back to the group; the "quick yes/no on the next stay,
     only if it costs more" shortcut is not built.
   * §3 #15: `member_exits` after booking tells the group the new split and stops. Re-pricing the stay
     among the rest is a human decision by design; a one-tap "we'll absorb it" could follow.

7. **Persistence.** `Engine.trips` is a dict. SQLite via a tiny repo class is enough for the competition.

## What not to do

* Don't build a generic "AI trip planner". Research is done by chatbots already; that is The Ken's own
  framing. We do consensus, verification and execution.
* Don't put the organiser's card anywhere in the flow. If a design needs one payer to front, it is wrong.
* Don't remove the deadlines or the defaults to make demos faster — use `clock.advance`.
* Don't add a group poll. Votes are private; the group sees a tally. That is a research finding
  (people don't say no in front of the group), not a UI choice.
* Don't add a voice call the two-job test doesn't pass. No deadline-nudge calls, no calls to listed hotels.
