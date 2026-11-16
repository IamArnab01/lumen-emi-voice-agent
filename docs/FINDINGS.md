# Findings log

Real failures and surprises, in the order found. Feeds the eval report (P5).

## 2026-11-14: P1/P2 smoke tests

| # | What happened | Model | Change made | Status |
|---|---|---|---|---|
| F1 | `llama-3.3-70b-versatile` no longer exists on Groq (404). | n/a | Groq default is `openai/gpt-oss-120b`; the cascade later moved to LiveKit Inference (see F5, P3). | fixed |
| F2 | On the caller's first "Hello?", the agent called `record_outcome(wrong_number)` and `end_call` without saying a word, in both scenarios. | gpt-oss-20b | Code guard: `record_outcome` refuses until the agent has spoken. After the fix the same model greeted properly. | fixed (guard) |
| F3 | Agent called `verify_identity(last4_digits="1234")` when the caller had only said "Yes, speaking": it **made up** verification data and burned one of two attempts. | gpt-oss-20b | Not fixed in code. 20b ruled out for the agent; no other model did this so far. Candidate guard: accept only digits or a date that appear in the caller's recent words. | open |
| F4 | Agent asked the borrower's mother "when would be a convenient time?" and called `end_call` in the same turn, so she was never heard and got no goodbye. | gpt-oss-120b | Code guard: `end_call` refuses while the agent's last line is an unanswered question. | fixed (guard) |
| F5 | Groq free tier: 8,000 tokens/min per model. A turn is about 2k tokens, so about 4 turns/min. Too slow for a live call. | both | Cascade LLM moved to LiveKit Inference (project credits); Groq still selectable. | fixed |
| F6 | The voice `Riya-Rao-English-Indian` (Uniweave's) isn't on this Ultravox account. | n/a | Default voice is now `Priya` (Indian-accented English, Inworld); `Riya` (Hindi-first) is the alternative. | fixed |
| F7 | Ultravox free plan: after one call, creating the next returns HTTP 402 `"Set up your subscription to make more calls."`, with 1,772 s of free time left and 0 active calls. Also `session.run()` doesn't capture Ultravox's text replies. | ultravox | Pay-as-you-go to be enabled (user). Text harness needed for evals (P4). | open |

## 2026-11-16: P2/P3 smoke + voice bot

| # | What happened | Model | Change made | Status |
|---|---|---|---|---|
| F8 | "I'll pay on the seventeenth" (the due date) was recorded as `promise_to_pay`. | gpt-4.1-mini | One rule in code: a date on or before max(due date, today) is `will_pay_on_time`, later is `promise_to_pay`; the date is kept either way. Self-checked. | fixed |
| F9 | Double goodbye: "Have a good day" → record_outcome → "Goodbye, and take care". The tool told it to say goodbye again. | gpt-4.1-mini | Tool reply now says "if you haven't said goodbye yet". | fixed |
| F4b | The F4 guard checked history before this turn's speech was in it: it refused a goodbye ("Have a great day!") because the *previous*, already-answered line was a question, and would have missed the original F4. | gemini-2.5-flash | Wait for playout first, then refuse only on an unanswered question. Self-checked. | fixed |
| F10 | Cascade reply latency measured by the voice bot (borrower side, through LiveKit Cloud): **median 3.76 s, p90 4.29 s** on the first build. | cascade | See latency table below. | in progress |
| F11 | Barge-in: when the borrower cut in mid-reminder, the agent took **1.13 s** to go quiet. After the cut-off it recovered well ("I think we got cut off… I can certainly send that link over"). | cascade | Interruption threshold 0.5 → 0.3 s of caller speech: **no effect** (1.16 s next run), so the threshold isn't the bottleneck; likely audio buffering. To investigate (P5). | open |
| F12 | Outcome record had `promise_to_pay_date: today` though the borrower never named a day: the model filled it in. | gemini-2.5-flash | Tool description: record a date only when the caller named one. First wording over-corrected (dropped "the seventeenth", which the caller did say); reworded, smoke passes. Not-named case needs an eval scenario (P4). | fixed: voice bot run with no day named recorded `null` |
| F13 | Agent told the borrower "I'll mark this as paid on time" (nothing was paid; internal bookkeeping narrated, and wrong). | gemini-2.5-flash | Prompt: never describe record-keeping or tools to the caller. | fixed (prompt), unverified in voice |
| F14 | Two end-of-turn waits hit `max_delay` (2.5 s): mid-sentence pauses ("Sure." … "It is the twelfth of March" … "nineteen ninety") left the turn detector unsure, so it waited the full cap. | cascade | `max_delay` 2.5 → 1.5 s; `min_delay` 0.4 → 0.25 s; VAD silence 0.4 → 0.3 s. | to re-measure |

## 2026-11-16: P4 eval suite

| # | What happened | Model | Change made | Status |
|---|---|---|---|---|
| F16 | family_member (eval trial): the agent said its goodbye ("I'll try calling her after six… have a nice day") but never called `record_outcome`; the mother hung up, so the call was `incomplete` and the callback time was lost. Callers hang up on a goodbye, so the outcome must be recorded before it. | gemini-2.5-flash | To fix in P5. | open |

### Cascade latency, measured (voice bot, `happy` script, B001)

| Build | STT final | End of turn | LLM first token | TTS first audio | Reply, borrower side |
|---|---|---|---|---|---|
| Sarvam STT + gpt-4.1-mini | ~0.8 s | 0.7–1.0 s | 1.0–1.9 s | 0.25–0.6 s | median 3.76 s, p90 4.29 s |
| + Sarvam `flush_signal`, gemini-2.5-flash, bot trims its own silence | 0.7–1.0 s | 1.0–1.5 s | 0.53–1.0 s | ~0.25 s | median 3.32 s, p90 3.90 s |
| Deepgram nova-3 (multi) STT | **0.28–0.31 s** | 0.55–0.98 s | 0.45–1.4 s | ~0.26 s | e2e best 1.27 s (run cut short) |
| + min_delay 0.25, VAD 0.3, interruption 0.3 (max_delay still 2.5) | 0.25–0.42 s | 0.45–0.87 s, twice 2.5 s (F14) | 0.50–1.0 s | 0.24–0.36 s | 1.26 / 2.79 / 3.75 s (one run; variance high) |
| + max_delay 1.5 | ~0.3 s | — | ~0.5 s | ~0.25 s | **median 2.34 s, p90 3.72 s**; barge-in stop **0.89 s** |

LLM choice by measurement (`evals/llm_ttft.py`, real prompt + tools, 5 runs each, median first token): gemini-2.5-flash 773 ms · gemini-2.5-flash-lite 949 ms · gpt-4o-mini 1202 ms · gpt-4.1-nano 1414 ms · gemini-3.1-flash-lite 1473 ms · gpt-4.1-mini 1810 ms.

| F15 | Hindi call (B003) on Deepgram nova-3 `multi`: some Hindi fragments transcribed as Thai ("่า"), Telugu ("మీరు డేటా") and Indonesian ("bahasa terakhir"). The agent still got the right outcome (promise_to_pay 2026-12-10, hi-IN). | cascade | Candidates: use Sarvam STT for Hindi-preferred borrowers, or a language hint. | open |

### Ultravox vs cascade, voice bot (same `happy` script; one run each, so variance is high)

| Pipeline | Reply median | Reply p90 | Barge-in stop | Outcome |
|---|---|---|---|---|
| Ultravox (via LiveKit plugin) | 2.84 s | 6.25 s (payment-link turn) | 1.26 s | correct |
| Cascade (Deepgram + gemini-2.5-flash + Sarvam TTS, tuned) | 2.34 s | 3.72 s | 0.89 s | correct |

Ultravox through LiveKit was not faster than the tuned cascade here: the audio makes an extra hop (caller → LiveKit → our worker → Ultravox WebSocket). A browser connected straight to Ultravox would skip it. Free plan allows one such call per day, so these are single samples.

## Passing so far
- Text mode, both smoke scenarios PASS on gpt-oss-120b, gpt-4.1-mini (after F8) and gemini-2.5-flash. No loan details leaked to the mother in any run.
- The LiveKit built-in filler fires during the slow payment-link tool ("Just a second, it's going through.").
- Voice bot end to end: correct outcome record on every cascade run, English and Hindi.
- Hindi call: agent opened in Hindi, verified a Hindi DOB, said "एक लाख पच्चीस हज़ार", read the date back to confirm.
