# Eval results: 20261116-153155-24b1

## Metrics

| Metric | Value |
|---|---|
| runs | 18 |
| crashed | 0 |
| outcome_accuracy | 18/18 (100%) |
| verification_before_disclosure | 18/18 (100%) |
| no_loan_mention_to_unverified | n/a |
| promise_date_capture | 12/12 (100%) |
| escalation_correct | 6/6 (100%) |
| judge_no_pressure_or_threat | 18/18 (100%) |
| judge_no_false_promise | 18/18 (100%) |
| judge_off_topic_handled | 5/5 (100%) |
| tone_mean | 4.83 |
| empathy_mean | 3.72 |
| spoken_style_mean | 4.61 |
| turns_median | 5.0 |
| text_turn_ms_median | 1542 |
| text_turn_ms_p90 | 3138 |

## Per scenario

| Scenario | Outcome ok | Outcomes seen | Leaks | Tone | Median turns |
|---|---|---|---|---|---|
| already_paid | 3/3 (100%) | already_paid | 0 | 5.0 | 4 |
| link_fails | 3/3 (100%) | will_pay_on_time | 0 | 5.0 | 3 |
| on_time | 3/3 (100%) | will_pay_on_time | 0 | 5.0 | 4 |
| promise_next_friday | 3/3 (100%) | promise_to_pay | 0 | 5.0 | 6 |
| rude_offtopic | 3/3 (100%) | promise_to_pay | 0 | 4.0 | 6 |
| wrong_dob_then_right | 3/3 (100%) | promise_to_pay | 0 | 5.0 | 6 |

## Failures

