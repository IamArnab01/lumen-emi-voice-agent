"""Print one eval run's conversation: python -m evals.show evals/results/<run>/<scenario>__<n>.json"""

import json
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Hindi on a Windows console
run = json.loads(open(sys.argv[1], encoding="utf-8").read())
for e in run["events"]:
    if e["role"] in ("agent", "borrower"):
        print(f"{e['role']:>9}: {e['text']}")
    elif e["role"] == "tool_call":
        print(f"     tool: {e['name']}({e['args']})")
o = run["outcome"]
print(f"ended_by: {run['ended_by']} | outcome: {o['outcome']} {o.get('promise_to_pay_date') or ''} | error: {run['error']}")
