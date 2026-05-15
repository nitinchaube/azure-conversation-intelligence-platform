"""Per-utterance compliance scoring.

Mirrors the 6-rule rubric from ``custom_extensions/04_compliance_check.py``
but runs incrementally on each final utterance. Inverted-logic rules
(e.g. PII disclosure where YES = violation) are normalized so that
``passed=True`` always means compliant.

Auth pattern matches the batch pipeline: if ``AZURE_OPENAI_API_KEY`` is
present we use API-key auth; otherwise we fall back to
``DefaultAzureCredential`` for AAD.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from openai import AsyncAzureOpenAI

from .config import SETTINGS

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ rule defs
@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    description: str
    severity: str        # "critical" | "major" | "minor"
    inverted: bool       # True = LLM "YES" means violation


RULES: List[Rule] = [
    Rule(
        rule_id="pii_disclosure",
        name="PII Disclosure",
        description=(
            "Did the AGENT inappropriately disclose customer PII (full SSN, "
            "full account number, full DOB) over the call? "
            "Answer YES only if the AGENT disclosed PII."
        ),
        severity="critical",
        inverted=True,
    ),
    Rule(
        rule_id="professional_greeting",
        name="Professional Greeting",
        description=(
            "Did the AGENT open the call with a professional greeting "
            "(company name + their name + offer to help)?"
        ),
        severity="major",
        inverted=False,
    ),
    Rule(
        rule_id="escalation_offer",
        name="Escalation Offer",
        description=(
            "If the customer was frustrated or the issue was complex, did "
            "the AGENT offer to escalate or transfer to a supervisor? "
            "Answer YES if escalation was correctly offered or the call did "
            "not warrant escalation."
        ),
        severity="major",
        inverted=False,
    ),
    Rule(
        rule_id="resolution_confirmation",
        name="Resolution Confirmation",
        description=(
            "Did the AGENT explicitly confirm with the customer that their "
            "issue was resolved before ending the call?"
        ),
        severity="major",
        inverted=False,
    ),
    Rule(
        rule_id="empathy_demonstration",
        name="Empathy Demonstration",
        description=(
            "Did the AGENT demonstrate empathy when the customer expressed "
            "frustration or distress (acknowledge feelings, apologize where "
            "appropriate)?"
        ),
        severity="minor",
        inverted=False,
    ),
    Rule(
        rule_id="inappropriate_upsell",
        name="Inappropriate Upsell",
        description=(
            "Did the AGENT attempt to upsell or cross-sell at an "
            "inappropriate moment (e.g. during an active complaint)? "
            "Answer YES only if an inappropriate upsell occurred."
        ),
        severity="minor",
        inverted=True,
    ),
]

SEVERITY_WEIGHT = {"critical": 3.0, "major": 2.0, "minor": 1.0}


# ------------------------------------------------------------------ scoring
@dataclass
class RuleResult:
    rule_id: str
    name: str
    severity: str
    passed: bool
    rationale: str
    not_applicable: bool = False


@dataclass
class ComplianceScore:
    overall: float                # 0–100
    per_rule: List[RuleResult] = field(default_factory=list)


_aoai: Optional[AsyncAzureOpenAI] = None


def _client() -> AsyncAzureOpenAI:
    global _aoai
    if _aoai is None:
        if SETTINGS.use_api_key:
            _aoai = AsyncAzureOpenAI(
                azure_endpoint=SETTINGS.aoai_endpoint,
                api_key=SETTINGS.aoai_api_key,
                api_version=SETTINGS.aoai_api_version,
            )
        else:
            # Match the batch pipeline auth path.
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

            credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )
            _aoai = AsyncAzureOpenAI(
                azure_endpoint=SETTINGS.aoai_endpoint,
                azure_ad_token_provider=token_provider,
                api_version=SETTINGS.aoai_api_version,
            )
    return _aoai


SYSTEM_PROMPT = """You are a contact-center compliance auditor. \
You will receive a partial conversation transcript and a list of compliance rules. \
For each rule, decide if the rule is met. Return strict JSON only.

Output schema:
{
  "results": [
    {"rule_id": "<id>", "answer": "YES" | "NO" | "NOT_APPLICABLE", "rationale": "<one sentence>"}
  ]
}

Notes:
- Use NOT_APPLICABLE when the conversation hasn't progressed far enough to evaluate the rule.
- "answer" reflects the literal question in the rule description. The caller will normalize inverted rules.
- Keep rationale under 25 words.
"""


def _build_user_prompt(transcript: str) -> str:
    rule_block = "\n".join(
        f"- {r.rule_id}: {r.description}" for r in RULES
    )
    return (
        f"Rules:\n{rule_block}\n\n"
        f"Transcript so far:\n---\n{transcript.strip()}\n---\n"
        f"Score each rule. JSON only."
    )


async def score(transcript: str) -> ComplianceScore:
    """Run the 6 rules against the current conversation buffer."""
    client = _client()
    resp = await client.chat.completions.create(
        model=SETTINGS.aoai_deployment,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(transcript)},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=600,
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Bad JSON from compliance scorer: %r", raw)
        data = {"results": []}

    results_by_id = {
        r["rule_id"]: r for r in data.get("results", []) if "rule_id" in r
    }
    per_rule: List[RuleResult] = []
    weighted_passed = 0.0
    weighted_total = 0.0

    for rule in RULES:
        r = results_by_id.get(
            rule.rule_id, {"answer": "NOT_APPLICABLE", "rationale": ""}
        )
        answer = (r.get("answer") or "").upper()
        rationale = r.get("rationale", "")
        not_app = answer == "NOT_APPLICABLE"
        if rule.inverted:
            passed = answer == "NO"
        else:
            passed = answer == "YES"
        per_rule.append(
            RuleResult(
                rule_id=rule.rule_id,
                name=rule.name,
                severity=rule.severity,
                passed=passed,
                rationale=rationale,
                not_applicable=not_app,
            )
        )
        if not not_app:
            w = SEVERITY_WEIGHT[rule.severity]
            weighted_total += w
            if passed:
                weighted_passed += w

    overall = (
        100.0 * (weighted_passed / weighted_total) if weighted_total else 100.0
    )
    return ComplianceScore(overall=round(overall, 1), per_rule=per_rule)
