# Round 3 simulation: run it tonight

The agent is Claude (`claude-opus-5-5` by default) following `sim/system_prompt.md`. It makes every decision. The people
behind the curtain only play the world: Pine Labs and Delhivery answer with their documented responses, fares and
stays come from real sites, and the phone line plays a teammate's recorded reply. Gnani calls are real.

## Setup (10 minutes)

```bash
pip install -r requirements.txt anthropic
export ANTHROPIC_API_KEY=...            # or `ant auth login`
python -m pytest -q                     # 64 tests, no network
```

Before recording:

1. **Pine Labs and Delhivery templates.** Open `sim/curtain/templates.json`. For each `pinelabs.*` and `delhivery.*`
   entry, replace `response` with the sample response from the official docs, set `doc` to the page URL and
   `verified: true`. Check method and path too (capture and cancel default to `PUT`; override with
   `PINELABS_CAPTURE_METHOD` / `PINELABS_CANCEL_METHOD` if the docs say `POST`).
2. **Real fares and stays.** When the agent calls `search_fares` / `search_stays`, the curtain shows a template. Press
   `e` (edit) or `p` (paste) and put in what the real site shows right now. Screenshot the site.
3. **The homestay reply.** Record a teammate answering the agent's Hindi question as the homestay owner (rooms, rate,
   refund terms, a 96-hour hold) as a WAV ≤ 60 s. When the agent dials, type its path at the `phone line:` prompt.
4. **Channel.** Console by default. For real phones: create a Telegram bot (BotFather), add it to a group with the
   five members, have each member open the bot and press Start, fill `telegram_user_id` and
   `telegram_group_chat_id` in `sim/config.json`, and run with `SIM_CHANNEL=telegram TELEGRAM_BOT_TOKEN=...`.
   (Production is WhatsApp; say so in the submission and show WhatsApp mockups.)

## Run

```bash
python -m sim.agent                                    # type the world in at the world> prompt
python -m sim.agent --script sim/scenario/rehearsal.txt   # chat lines from a file; curtain still asks you
```

At `world>`:

| You type | What the agent receives |
|---|---|
| `@Sayan group: Priya's wedding in Assagao…` | Sayan's post in the group |
| `@Riya: Yes` | Riya's DM |
| `/advance 24h` | a clock tick (the deadline passes) |
| `/webhook Aabhas AUTHORIZED CARD` | Pine Labs' webhook for Aabhas's latest payment link |
| `/event IndiGo SMS \| 6E 523 on 20 Nov is cancelled` | a real-world notice, with its source |
| `/status` | (operator only) links, orders, pool |
| `/export`, `/quit` | writes the Markdown tables |

The curtain prompt (`curtain [Enter] send · e edit · p paste JSON · x documented error`) appears on every
Pine Labs / Delhivery / fare / stay call. `x` on a capture plays the documented failure, which exercises R18 (refund
the earlier captures, LAPSED).

`SIM_PINELABS=uat` sends the same requests to Pine Labs UAT instead (needs `PINELABS_CLIENT_ID`,
`PINELABS_CLIENT_SECRET`, `PINELABS_MERCHANT_ID`).

Other knobs: `QUORUM_MODEL`, `QUORUM_EFFORT` (default `high`), `SIM_FALLBACKS=0` to drop the server-side refusal
fallback, `SIM_CURTAIN=auto` to accept every template unedited (rehearsal only, not for the recording).

## What you get for the form

Every run writes `sim/runs/<stamp>/`:

| File | Form question |
|---|---|
| `decisions.md` | Part 1 Q2: every decision, in order, with when, what it received, from where, what it decided, the rule, what it said to whom, and through what |
| `rail_calls.md` | Part 1 Q4: every Pine Labs / Delhivery / Gnani call with endpoint, request and response |
| `transcript.md` | every message the agent sent (mockups, the story) |
| `system_prompt.md` | the exact prompt the model ran with (submit it) |
| `*.wav` | Gnani's TTS audio and the recorded replies |

## Recording tips

Split the screen: this terminal on one side, the chat (phones or Telegram desktop) on the other. Start recording
before the organiser's first message and stop when "Booked" lands in the group. Fast-forward the model's thinking.
The colours: cyan = input arriving, magenta = call to a rail, green = its response, yellow ◆ = a decision, bold =
a message to a person, dim = the model's summarised reasoning.
