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
* Voice has two jobs (`call_supplier` + `notify_late_arrival`, and `escalate_member`). If a design needs a
  third call, re-read the table in `docs/round2-answers.md` §0 first. Members are never called to chase.
* Every user-visible sentence lives in `app/messages.py`. Short sentences, one ask, always a default and a
  deadline, never a group poll. The group gets four kinds of post: kickoff, tally, booking, disruption.
* Money is INR integers in the engine; adapters convert to paisa at the edge. A member is never debited
  more than `Authorisation.amount` (+ a top-up they approved themselves).
* Time comes from `app/clock.py`. Never call `datetime.now()` in engine code.
* `Event` log is the transcript. If it happened and the group or a member saw it, it goes through `_say`.
* Every phone-only stay that says no or never answers goes in `trip.stays_out` and is not called again.
  Calls cost money; the mock hides that.

## Next tasks, in order

1. **Pine Labs UAT end to end** (highest value; unblocks the Round-2 demo on a real rail)
   * Sign up at the Pine Labs Online dashboard, get UAT client id/secret/merchant id, ask for OTM enablement.
   * Set `QUORUM_PAYMENTS=pinelabs`, run `demo.py`. Fix field names in `app/rails/pinelabs.py` against the
     live responses (token field, `data` envelope, `customer_id` path) — they were written from docs.
   * Make `capture()` block on presentation status until `SUCCESS` and raise `CaptureFailed` on `FAILED`,
     instead of recording `CAPTURED` optimistically. Wire `/webhooks/pinelabs` to reconcile.
   * **Verify three things the engine assumes:** (a) a *second* presentation on the same OT mandate — the
     share at booking, then a re-booking difference inside the headroom (`Engine._present`). If OTM is one
     debit only, present share + headroom together and refund, or move the difference to a top-up mandate.
     (b) The refund endpoint and body (`PineLabsPayments.refund`, marked [verify]). (c) Whether a merchant
     can cancel an `ACTIVE` OT subscription, and the minimum `validity_days`.
   * Then run the wall for real: `payments.fail_capture_for` has no UAT equivalent, so use a test VPA that
     declines. Showing the rollback fail honestly is worth more than pretending it cannot.

2. **Gnani — speech first, calls second**
   * The key we hold is speech-scoped (`vach_`). Run `python scripts/speech_demo.py` once: the supplier
     call's four Hindi questions synthesised into `cache/tts/`, then `--stt` on a phone recording of a
     teammate answering as the homestay owner (code-switching, a price that changes mid-sentence, a
     "haan… matlab nahi"). Budget ≈ ₹2; `CallBudget` stops you at 40 calls regardless. Fill the three voice
     failure rows in `docs/round2-answers.md` §3 from what breaks.
   * For outbound calls you need an Inya platform key with `agents` permission and a `botId`; whitelist one
     teammate's number as the homestay. Run `GnaniVoice().setup_agent()` once. Confirm the platform accepts
     the Jinja `{% if purpose %}` prompt in `app/rails/gnani.py`; if not, split into three bots.
   * Set `QUORUM_VOICE=gnani`, run one `call_supplier`. Fix response parsing in `_record` / `_stats` against
     real payloads (`overallCallDisposition`, `extractedVariables`, `utteranceAnalytics` are guesses).
   * Expose `QUORUM_PUBLIC_URL` via a tunnel (ngrok/cloudflared) so the pre-call variables resolve.

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
