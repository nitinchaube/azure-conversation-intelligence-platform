"""Smoke test for the per-utterance compliance scorer.

Run from repo root:
    python scripts/test_rules.py

Expected: pass/fail for each of the 6 rules with a one-line rationale.
"""
from __future__ import annotations

import asyncio

from custom_extensions.live_voice.rules import score


SAMPLE = """\
Agent: Thanks for calling BankCo, this is Sarah. How can I help you today?
Customer: I'm trying to dispute a charge of $450 from yesterday.
Agent: I'm sorry to hear that — let me pull up your account. Can you confirm the last four of your card?
Customer: 1234.
Agent: Got it. I see the charge. I've opened a dispute case for you. Is there anything else?
Customer: No that's all, thank you.
Agent: You're welcome. Have a good day.
"""


async def main() -> None:
    result = await score(SAMPLE)
    print(f"Overall: {result.overall}")
    for r in result.per_rule:
        icon = "✓" if r.passed else ("·" if r.not_applicable else "✗")
        print(f" {icon} [{r.severity}] {r.name}: {r.rationale}")


if __name__ == "__main__":
    asyncio.run(main())
