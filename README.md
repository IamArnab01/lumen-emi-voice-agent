# Lumen Finance: EMI reminder voice agent

An outbound voice agent that calls a borrower about an upcoming or overdue EMI. It
checks who it is talking to, reminds them of the amount and due date, finds out what
they plan to do, offers a payment link, and saves a structured outcome for each call.
English and Hindi.

- **Results and failures:** [docs/REPORT.md](docs/REPORT.md)
- **Every issue found while building, with the fix or why it's still open:** [docs/FINDINGS.md](docs/FINDINGS.md)
- **Mock borrowers:** [data/borrowers.json](data/borrowers.json) (8 borrowers, B001 to B008)
- **Eval scenarios:** [evals/scenarios.yaml](evals/scenarios.yaml) (17 scenarios)

## Run it

Requirements: Windows with WSL Ubuntu (or any Linux/macOS), Python 3.12, a LiveKit Cloud
project (free tier), and an Ultravox key. A Sarvam key is needed for the cascade pipeline and
the voice bot.

```powershell
.\start.ps1 setup     # creates the venv in WSL, installs requirements, copies .env.example to .env
# put your keys in .env
.\start.ps1 keys      # checks each key against its provider
.\start.ps1 check     # self-checks, no keys needed
.\start.ps1 app       # THE DEMO: open http://localhost:8090, click "Call" next to a borrower
```

The call page lists the borrowers. "Call" starts the call: the agent joins a LiveKit room and
speaks first, and you answer in the browser as the borrower (mic on). The live transcript and
the saved outcome show on the same page. No phone numbers or telephony are involved.

On Linux/macOS, run the same commands as `bash scripts/run.sh <command>`. `start.ps1` only
forwards to that script inside WSL (Windows Smart App Control blocks livekit's native
libraries on the machine this was built on).

Other commands:

| Command | What it does |
|---|---|
| `evals --runs 3` | full eval suite: simulated borrower, rule checks, LLM judge. In PowerShell quote lists: `--only 'on_time,hardship'` |
| `score <results dir>` | re-score a results folder |
| `smoke [ultravox]` | two scripted text conversations (happy path, family member) |
| `console B003` | talk to the agent with your laptop mic, no browser |
| `voicebot cascade happy` | a voice bot phones the agent and measures reply latency and barge-in |

### Settings (.env)

| Variable | Default | Meaning |
|---|---|---|
| `PIPELINE` | `ultravox` | `ultravox` (speech-to-speech) or `cascade` (STT, LLM, TTS) |
| `TODAY` | `2026-11-14` | pinned date so due dates and "next Friday" are reproducible; empty means the real date |
| `FAIL_PAYMENT_LINK` | `0` | `1` makes the mock payment link fail |
| `ULTRAVOX_VOICE` | `Priya` | Indian-accented English; `Riya` is Hindi-first |
| `CASCADE_LLM` | `google/gemini-2.5-flash` | any LiveKit Inference model, or `groq/<model>` |
| `CASCADE_STT` | `deepgram/nova-3` | or `sarvam` |

## How it works

```
browser (call page) ──WebRTC──> LiveKit Cloud room <──── agent.py worker (LiveKit Agents)
                                                          │
                                PIPELINE=ultravox  ──>  Ultravox realtime (speech to speech)
                                PIPELINE=cascade   ──>  Deepgram STT → Gemini 2.5 Flash → Sarvam TTS
                                                          │
                                       LumenAgent: one prompt + four tools
                                       verify_identity · send_payment_link · record_outcome · end_call
                                                          │
                                                outcomes.jsonl, logs/calls/<id>.json
```

One agent (`lumen/assistant.py`) runs on either pipeline and in the text-mode evals, so the
evals test the same prompt and tools as the live calls.

**Call flow** (`lumen/prompts.py`): greet and ask for the borrower by name → verify (date of
birth or last 4 digits of the registered number, 2 attempts) → remind (amount and due date, or
days overdue) → listen and respond (pay on time, already paid, promise a date, hardship,
dispute, callback, stop) → record the outcome → short goodbye → hang up. Someone else answering
gets no loan details, only a request for a better time.

**Outcome record**, one JSON line per call in `outcomes.jsonl`:

```json
{"call_id": "9f2c41aa03b1", "borrower_id": "B002", "verified": true, "outcome": "promise_to_pay",
 "promise_to_pay_date": "2026-11-20", "payment_link_sent": true, "escalate_to_human": false,
 "notes": "Pays on salary day.", "language": "en-IN", "recorded_at": "...", "recorded_by": "agent"}
```

Outcomes: `will_pay_on_time, already_paid, promise_to_pay, hardship, dispute, wrong_number,
third_party, callback_requested, stop_requested, verification_failed, incomplete`.

### Guardrails are in code, not only in the prompt

| Rule | How it is enforced |
|---|---|
| No loan details before verification | The prompt holds only the borrower's name. Amount, due date and loan type reach the model only inside a successful `verify_identity` result. Nothing to leak before that. |
| Max 2 verification attempts | Counted in `CallState`; the tool refuses after that. |
| No link to an unverified caller | `send_payment_link` refuses. |
| Hardship and disputes go to a person | `build_outcome` forces `escalate_to_human` for these, whatever the model asked for. |
| Pay dates are classified one way | A date on or before max(due date, today) is `will_pay_on_time`; later is `promise_to_pay`; past dates are rejected; more than 30 days out is flagged for review. |
| No hang-up on an unanswered question | `end_call` refuses while the agent's last line is an open question. |
| Every call gets an outcome | If the caller hangs up before the agent recorded one, a post-call pass reads the transcript and fills it (`lumen/postcall.py`, marked `recorded_by: post_call`). Verified and link-sent always come from the call state, not the model. |
| Silence and length | 7 s silence → "are you still there?" twice → goodbye; 8 minute cap (`lumen/guards.py`). |

No threats, no waiver promises, staying on topic and stopping when asked are prompt rules,
checked by the eval judge and keyword checks.

## Design decisions

- **LiveKit Agents + LiveKit Cloud.** Rooms, WebRTC, turn detection, interruption handling and
  a worker model come for free, and the same agent code runs over Ultravox or a cascade. Cloud
  free tier instead of self-hosting: nothing to operate for a POC.
- **Ultravox as the main pipeline, cascade as fallback.** Ultravox is one speech-to-speech model,
  so there is no STT/TTS handoff to tune. The cascade exists because the Ultravox free plan
  allows one call before it needs a subscription, and the evals need many calls. Measured with
  the voice bot (single runs, high variance): Ultravox via LiveKit median reply 2.84 s, tuned
  cascade 2.34 s. Through LiveKit, Ultravox adds an extra audio hop.
- **One agent, not a verifier-to-reminder handoff.** Same guarantee (no facts before
  verification) with fewer parts and no handoff risk on a realtime model.
- **No LangGraph.** The call is one conversation with four tools; the hard rules live in tool
  code. A graph would add structure without adding a guarantee.
- **Spoken forms computed in code.** Amounts and dates are turned into words in Indian style
  ("one lakh twenty-five thousand rupees", "this Tuesday, the seventeenth") before the model
  sees them, so it never reads digits or ISO dates aloud.

## Evals

`evals/run.py` plays each scenario against the real agent in text mode: an LLM
(gpt-4.1-mini) plays the borrower from a persona and script beats in `scenarios.yaml`, the
agent runs on the cascade LLM with its real tools, and each run is saved with the full
transcript, tool calls and outcome. `evals/score.py` then scores:

- **Rule checks (trusted):** outcome, promise date, escalation and link fields against the
  expected record; any amount, date or loan word said before verification succeeded; loan
  mentioned to an unverified caller; threat and waiver phrases; turns and turn time.
- **LLM judge (gpt-4.1, a different model from the agent):** tone, empathy, spoken style (1 to
  5), pressure or threat, false promise, off-topic handling, worst moment.

Results go to `evals/results/<run id>/summary.md`. Text mode doesn't test speech recognition,
voice or timing; `evals/voice_bot.py` covers latency and barge-in on real audio, but only for
two scripts. See [docs/REPORT.md](docs/REPORT.md) for the numbers and the failures.

## Layout

```
agent.py              LiveKit worker: one job per call
lumen/  data.py       borrowers, spoken amounts and dates
        prompts.py    instructions, calendar
        assistant.py  the agent and its four tools
        tools.py      identity check, outcome rules, mock payment link
        postcall.py   outcome from the transcript if the caller hung up first
        guards.py     silence nudges, call cap, hang-up
        pipelines.py  Ultravox and cascade sessions
web/                  call page (aiohttp + livekit-client)
evals/                scenarios, runner, scorer, voice bot, transcript viewer
docs/                 REPORT.md, FINDINGS.md
```
