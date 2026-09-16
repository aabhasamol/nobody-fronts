# Handoff — for the next builder (human or Claude Code)

Read `README.md`, then `app/engine.py`, then `docs/rails.md`. Then this.

## The one thing to protect

The product is the loop in `Engine`: private capture → verified bundles → default-and-clock → per-member block
→ quorum → book or lapse → re-book inside the cap. Every change should make that loop more real (a real
rail, a real channel, real fares), not wider. Do not add features the six steps do not need.

## Conventions

* Rails are the only seam. New capability goes behind `VoiceRail` / `PaymentsRail` / `LogisticsRail` in
  `app/rails/base.py`, with a mock first and the real adapter second. The engine must keep passing
  `tests/` against mocks.
* Every user-visible sentence lives in `app/messages.py`. Short sentences, one ask, always a default and a
  deadline, never a group poll.
* Money is INR integers in the engine; adapters convert to paisa at the edge.
* Time comes from `app/clock.py`. Never call `datetime.now()` in engine code.
* `Event` log is the transcript. If it happened and the group or a member saw it, it goes through `_say`.

## Next tasks, in order

1. **Pine Labs UAT end to end** (highest value, unblocks the Round-2 demo on a real rail)
   * Sign up at the Pine Labs Online dashboard, get UAT client id/secret/merchant id, ask for OTM enablement.
   * Set `QUORUM_PAYMENTS=pinelabs`, run `demo.py`. Fix field names in `app/rails/pinelabs.py` against the
     live responses (token field, `data` envelope, `customer_id` path) — they were written from docs, not
     from a live call.
   * Make `capture()` block on presentation status until `SUCCESS` (fetch by `presentation_id`) instead of
     recording `CAPTURED` optimistically. Wire `/webhooks/pinelabs` to reconcile.
   * Confirm: minimum `validity_days`, and whether an `ACTIVE` OT subscription can be cancelled by the merchant.
   * Then write the partial-failure case: capture 1–3 succeed, 4 fails ⇒ refund 1–3, mark trip `LAPSED`,
     tell the group. This is the wall we are describing to Pine Labs; showing it fail honestly is worth
     more than pretending it cannot.

2. **Gnani — speech first, calls second**
   * The key we hold is speech-scoped (`vach_`). Run `python scripts/speech_demo.py` once: four Hindi
     questions synthesised into `cache/tts/`, then `--stt` on a phone recording of a teammate answering as
     the hotel. Budget ≈ ₹2; the `CallBudget` guard stops you at 40 calls regardless. Check the numbers on
     the dashboard afterwards and write them in `docs/rails.md`.
   * For the outbound call you need an Inya platform key with `agents` permission (ask The Ken / Gnani —
     the email said "platform credits", which may be this) and a `botId`; whitelist one teammate's number as
     the "hotel".
   * Run `GnaniVoice().setup_agent()` once. Set `QUORUM_VOICE=gnani`, run one `verify_property`.
   * Fix response parsing in `_wait_for_conversation` / `_stats` against real payloads (field names guessed
     from docs: `conversationId`, `callStatus`, `overallCallDisposition`, `utteranceAnalytics`).
   * Expose `QUORUM_PUBLIC_URL` via a tunnel (ngrok/cloudflared) so the pre-call variables resolve.

3. **A real channel.** The demo UI stands in for WhatsApp. Options in order of realism vs effort:
   Telegram bot (an evening), WhatsApp Cloud API with a test number (a day plus Meta approval), or keep the UI
   and record a screen demo. Whatever the channel, the engine does not change: a channel adapter turns
   inbound text into `engine.capture` / `engine.member_approves` and outbound `Event`s into sends.

4. **Fares.** Replace `MockLogistics.search_legs/search_stays` with anything live — a public flight-search API,
   or scraped fares — behind the same interface. Keep the mock for tests.

5. **Member exit after booking.** A member drops out post-quorum. Today nothing handles it. Design:
   their share is not refundable by us (airline rules), but the stay's twin-sharing math changes for the
   others. Decide whether the agent re-prices or eats it. This is the interview finding from Anoushka and
   Paridhi (unpredictable jobs) and it will come up.

6. **Persistence.** `Engine.trips` is a dict. SQLite via a tiny repo class is enough for the competition.

## What not to do

* Don't build a generic "AI trip planner". Research is done by chatbots already; that is The Ken's own
  framing. We do consensus, verification and execution.
* Don't put the organiser's card anywhere in the flow. If a design needs one payer to front, it is wrong.
* Don't remove the 48-hour clock or the default to make demos faster — use `clock.advance`.
* Don't add a group poll. Preferences are captured privately; that is a design rule, not a UI choice.
