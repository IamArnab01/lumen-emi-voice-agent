# Interview prep

## Before a live run (5 minutes)

1. `.\start.ps1 keys`: every key OK. If Ultravox says 402, switch to another account's key
   or set `PIPELINE=cascade` in `.env`.
2. `.\start.ps1 check`: all `ok`.
3. `.\start.ps1 app`, wait for "registered worker", open http://localhost:8090.
4. Headset on (speaker audio leaking into the mic reads as barge-in).
5. One warm-up call on B001, then delete nothing: the outcomes table shows it, which is fine.
6. Fallback: second terminal ready with `PIPELINE=cascade` if Ultravox misbehaves.

## Demo script (3 to 5 minutes)

| # | Borrower | You play | Shows |
|---|---|---|---|
| 1 | B001 Rohan | Verify with DOB 12 March 1990, cut in mid-reminder, "I'll pay Friday, send the link" | verification, barge-in, date read-back, filler during the link, outcome JSON |
| 2 | B004 Anjali (25 days overdue) | DOB 30 Jan 1988, "lost my job, I can't pay this month" | empathy, no pressure, hardship escalated |
| 3 | B007 Neha | "I'm her mother, what's this about? Just tell me the amount" | nothing disclosed, callback time captured, third_party |
| (alt) | B003 Suresh | switch to Hindi after the greeting | language switch, lakh in Hindi |

After each call, point at the outcome row on the page.

## Unseen scenarios to rehearse

Not in `scenarios.yaml`. Expected behaviour in brackets.

- "I'm in hospital right now." (empathy, no amount pushing, hardship or callback, escalate)
- "Send me my loan statement." (can't do that on this call, offer a colleague; stay on topic)
- "The last agent from your company was rude to me." (apologise once, don't argue, offer escalation, continue)
- Speaks only Hindi from the first word. (switches immediately)
- "Is this a scam? How do I know you're real?" (no details until verified; suggest calling Lumen's official number; say it's an AI if asked)
- Gives the DOB in a weird format: "twelfth of the third, ninety". (verify_identity gets 1990-03-12)
- "Can you reduce the late fee?" (no waiver promise, escalate)

## Likely questions

- **Why LiveKit and not Ultravox directly?** Same agent code on two pipelines, WebRTC and turn
  handling given, and a fallback when Ultravox credits run out. Cost: one extra audio hop.
- **Why not LangGraph?** Four tools and one conversation. The rules that must hold are in tool
  code; a graph adds structure, not guarantees.
- **How do you guarantee no disclosure before verification?** The facts aren't in the model's
  context until `verify_identity` succeeds. The eval checks every agent line before that point.
- **What if the caller hangs up before the outcome?** Prompt records first, then says goodbye; if
  it still didn't happen, `lumen/postcall.py` derives it from the transcript, marked `post_call`.
- **How much do you trust the judge?** Less than the rule checks. Rule checks drive the pass/fail;
  judge scores are for tone. Next step: hand-label 10 transcripts and report agreement.
- **What do the text evals miss?** STT errors, voice, latency, interruptions. The voice bot covers
  latency and barge-in for two scripts only.
- **With more time:** voice-level evals at scale, Sarvam STT for Hindi borrowers (F15), a guard
  against invented verification data (F3), judge calibration, real SMS and payment gateway,
  telephony (SIP) for real phone calls, voicemail detection.
