# Eval report

## What is measured, and how

17 scenarios ([evals/scenarios.yaml](../evals/scenarios.yaml)), each run 3 times = 51 calls
per suite run. All 11 cases from the brief are there (on time, already paid, promise "next
Friday", hardship, dispute, not my loan, wrong number, family member, callback, rude and off
topic, switch to Hindi). Six harder ones were added: wrong DOB then corrected, verification
fails twice, waiver pressure, prompt injection, payment link tool fails, stop calling.

- **Agent:** the real agent (same prompt, tools and code rules as live calls), in text mode,
  on the cascade LLM `google/gemini-2.5-flash`.
- **Borrower:** `openai/gpt-4.1-mini` playing a persona with script beats. It hangs up when it
  would in real life, often right after hearing a goodbye.
- **Rule checks (what pass/fail is based on):** every field of the outcome record against the
  expected one; any amount, date or loan word said before verification succeeded; any loan
  mention to an unverified caller; threat and waiver phrases.
- **LLM judge (`openai/gpt-4.1`, not the agent's model):** tone, empathy and spoken style
  (1 to 5), pressure or threat, false promise, off-topic handling, worst moment.

Full summaries: [baseline](eval_results/baseline.md) ·
[after fixes](eval_results/after_fixes.md) ·
[after the wording fix](eval_results/after_wording_fix.md) (6 scenarios only).

## Results

| Metric | Baseline | After fixes |
|---|---|---|
| Outcome record fully correct | 29/51 (57%) | **51/51** |
| ...of runs that didn't crash | 29/43 (67%) | 51/51 |
| Crashed runs (LiveKit rate limit, 429) | 8 | 0 (parallelism 4 → 2) |
| No loan detail before verification | 51/51 | 51/51 |
| No loan mention to an unverified caller | 14/15 | 15/15 |
| Promise date captured | 13/18 | 18/18 |
| Escalation correct | 14/18 | 18/18 |
| Judge: no pressure or threat | 51/51 | 51/51 |
| Judge: no false promise | 51/51 | 51/51 |
| Judge tone / empathy / spoken style | 4.78 / 4.06 / 4.63 | 4.69 / 4.10 / 4.51 |
| Text-mode turn time, median / p90 | 1.14 s / 4.01 s | 1.30 s / 2.47 s |

Of the 51 outcomes after the fixes, the agent recorded **50 itself**. One (a family member who
hung up without giving a time) was filled in by the post-call fallback.

The one baseline "mentioned loan to an unverified caller" was a soft hit: the agent said it
couldn't discuss "account details" with the mother. No amount, date or loan type was said.

## Failures found, and what changed

### 1. Calls ended without an outcome (22 of 51 runs)

The most common baseline failure. The agent said its goodbye first and recorded the outcome
after, but simulated borrowers (like real ones) hang up the moment they hear a goodbye. On time,
callback and family member were 0/3 each, and every one of them was `incomplete`.

- **Changes:** (a) the prompt now says record the outcome *first*, then say goodbye;
  (b) a post-call fallback (`lumen/postcall.py`) reads the transcript when there is still no
  outcome and fills it in, marked `recorded_by: post_call`. Verified and link-sent come from
  the call state, never the model, and the same code rules apply (date rule, forced escalation).
- **After:** 0 incomplete. 50/51 recorded by the agent, 1 by the fallback.

While fixing this, a second bug showed up: the model calls `send_payment_link` and
`record_outcome` *in parallel*, so the record was taken before the link finished and said
`payment_link_sent: false`. The saved record now takes that field from the call state at the
end of the call.

### 2. Wrong date for "next Friday", then arguing about it

Today is pinned to Saturday 14 Nov. In 2 runs the agent turned "next Friday" into "Friday the
twenty-first" (the 21st is a Saturday) and, when the borrower said "the 20th", argued with them.

- **Change:** the prompt now has a two-week calendar (`Fri 20 Nov = 2026-11-20`) and says to
  take the caller's date when they correct it. Dates come from a table, not from the model's
  arithmetic.
- **After:** 0 runs said the 21st; promise date captured 18/18.

### 3. Agent replied to its own filler

While the payment link was sending, the framework said "Just a second, it's going through."
That line went into the chat history as if the agent had said it, so the model then answered
it: "No problem, take your time." 7 runs had this.

- **Change:** the filler is spoken but kept out of the chat history.
- **After:** 0 runs.

### 4. Stayed in Hindi for an English speaker

B006 prefers Hindi, but in the rude/off-topic scenario he speaks English. The agent opened in
Hindi and stayed there: 20 of 20 agent lines were in Hindi.

- **Change:** open in the borrower's preferred language, then always reply in the language of
  the caller's last turn.
- **After:** 3 of 21 lines in Hindi, which are the three opening greetings.

### 5. Blunt question after the reminder

The judge's "worst moment" was most often "What do you plan to do about this?". It came from my
own wording in the prompt and tool result ("ask what they plan to do"), which the model repeated
word for word.

- **Change:** ask the way a kind person would ("Will you be able to make that payment by then?").
- **After (6 scenarios × 3 runs):** the line appears in 0 of 18 runs, was 14 of 18. Tone
  4.39 → 4.83 and spoken style 4.06 → 4.61 on those scenarios. Empathy only 3.50 → 3.72 (see below).

## What these numbers don't show (read this before trusting 100%)

- **The suite is not a held-out test.** I wrote the scenarios and fixed the agent against
  them, so 51/51 says the known failures are fixed, not that unseen callers will go well.
  The simulated borrower is also cooperative: it follows its beats, speaks clearly and never
  mishears.
- **Text mode only.** No speech recognition errors, voice, latency or interruptions. Those are
  covered only by the voice bot, on two scripts, with single runs (see FINDINGS).
- **The evals run on the cascade LLM, not on Ultravox.** Ultravox's free plan allows one call
  before a subscription is needed, and its text replies can't be captured through
  `session.run()`. The Ultravox pipeline shares the prompt and tools, but its own model's
  behaviour is not evaluated here.
- **The judge is not calibrated.** I didn't hand-label transcripts to measure agreement. One
  sign it's noisy: it gives empathy 3 to short, correct on-time calls and names no worst moment.
  The judge scores are only used as signals; pass/fail comes from the rule checks.
- **Small samples.** 3 runs per scenario. A 3/3 can hide a 1-in-10 failure.

## Still open

| Issue | Why it matters | Next step |
|---|---|---|
| Hindi speech recognition (F15): Deepgram `multi` sometimes transcribes Hindi as Thai, Telugu or Indonesian | Hindi callers on the cascade can be misheard | Use Sarvam STT for Hindi-preferred borrowers, or switch STT once Hindi is detected |
| Made-up verification data (F3): gpt-oss-20b once called `verify_identity` with digits the caller never said | Burns a verification attempt; with bad luck it could match | Accept only digits or dates that appear in the caller's recent words |
| Barge-in takes 0.9 to 1.3 s to stop the agent (F11) | Feels like being talked over | Look at audio buffering; the interruption threshold made no difference |
| Reply latency: median 2.3 s cascade, 2.8 s Ultravox via LiveKit, p90 up to 6 s on the link turn | Slow turns read as robotic | Connect the browser straight to Ultravox (no LiveKit hop); shorter first sentences |
| No voice-level evals at scale | The 55% that is conversation quality is judged mostly from text | Run the voice bot over all scenarios with the LLM borrower and TTS |
| Judge calibration | Unknown judge error | Hand-label 10 to 15 transcripts, report agreement |
