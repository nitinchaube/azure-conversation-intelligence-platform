"""
Automated compliance and conversation analytics pipeline.

Pulls call transcripts from Azure AI Search, runs each through Azure
OpenAI to extract structured operational metrics (sentiment, topic,
compliance violations, hold-time mentions, resolution status) and
persists results to SQLite for downstream reporting.

Usage:
    python 04_compliance_check.py
"""

import os
import re
import time
import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.identity import DefaultAzureCredential

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

AZURE_OPENAI_ENDPOINT = os.getenv(
    "AZURE_OPENAI_ENDPOINT", "https://aif-ckmv2inaoo.openai.azure.com/"
)
AZURE_OPENAI_DEPLOYMENT = os.getenv(
    "AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini"
)
AZURE_OPENAI_API_VERSION = "2024-08-01-preview"

SEARCH_ENDPOINT = "https://srch-ckmv2inaoo.search.windows.net"
SEARCH_INDEX = "call_transcripts_index"

DB_PATH = Path(__file__).parent / "compliance_results.db"

COMPLIANCE_RULES = [
    {
        "id": "PII_001",
        "name": "PII Disclosure",
        "severity": "critical",
        "prompt": (
            "Did the agent unnecessarily disclose or request personally "
            "identifiable information (full name, address, SSN, credit card number)? "
            "Answer YES if there was an unnecessary disclosure, NO if PII handling "
            "was appropriate, or N/A if no PII was involved."
        ),
    },
    {
        "id": "GREETING_001",
        "name": "Professional Greeting",
        "severity": "minor",
        "prompt": (
            "Did the agent open the call with a professional greeting that "
            "included their name and company? Answer YES if compliant, NO if not."
        ),
    },
    {
        "id": "ESCALATION_001",
        "name": "Escalation Offered",
        "severity": "major",
        "prompt": (
            "If the customer expressed dissatisfaction or frustration, did the "
            "agent offer to escalate to a supervisor or specialist? Answer YES, "
            "NO, or N/A if the customer was not dissatisfied."
        ),
    },
    {
        "id": "RESOLUTION_001",
        "name": "Resolution Confirmed",
        "severity": "major",
        "prompt": (
            "Before closing the call, did the agent confirm the customer's issue "
            "was resolved and ask if there was anything else they could help with? "
            "Answer YES, NO, or N/A."
        ),
    },
    {
        "id": "EMPATHY_001",
        "name": "Empathy Demonstrated",
        "severity": "minor",
        "prompt": (
            "Did the agent demonstrate empathy when the customer described their "
            "problem (e.g. acknowledging frustration, apologizing for inconvenience)? "
            "Answer YES, NO, or N/A."
        ),
    },
    {
        "id": "UPSELL_001",
        "name": "No Inappropriate Upsell",
        "severity": "major",
        "prompt": (
            "Did the agent attempt to upsell or cross-sell a product/service at "
            "an inappropriate time (e.g. while the customer was frustrated or "
            "reporting a problem)? Answer YES if there was an inappropriate upsell, "
            "NO if there was no inappropriate upsell, N/A if not applicable."
        ),
    },
]

ANALYSIS_PROMPT = """\
You are an operations analyst reviewing a telecom contact-centre call transcript.
Extract the following fields as a JSON object. Use only the information in the
transcript. If a field cannot be determined, use null.

{
  "summary": "<2-3 sentence summary of the call>",
  "sentiment": "positive | negative | neutral | mixed",
  "primary_topic": "<main reason for the call, e.g. billing dispute, device issue>",
  "sub_topic": "<more specific category, e.g. overcharge, screen freeze>",
  "customer_intent": "complaint | inquiry | feedback | cancellation | technical_support | other",
  "resolution_status": "resolved | unresolved | escalated | follow_up_needed",
  "agent_name": "<agent name if mentioned, else null>",
  "customer_name": "<customer first name if mentioned, else null>",
  "hold_mentioned": true | false,
  "transfer_occurred": true | false,
  "estimated_handle_time_minutes": <integer estimate based on conversation length and complexity, null if unsure>,
  "customer_effort_score": <1-5 integer, 1=very easy for customer, 5=very difficult>
}

Return ONLY valid JSON, no markdown fencing."""

credential = DefaultAzureCredential()


def _get_token():
    return credential.get_token("https://cognitiveservices.azure.com/.default").token


client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=AZURE_OPENAI_API_VERSION,
    azure_ad_token_provider=_get_token,
)


def call_openai(system: str, user: str, max_tokens: int = 300) -> str | None:
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0.05,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            log.warning("OpenAI attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def extract_analysis(transcript: str) -> dict:
    raw = call_openai(ANALYSIS_PROMPT, transcript[:4000], max_tokens=400)
    if not raw:
        return {}
    # strip markdown code fences if the model adds them anyway
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Failed to parse analysis JSON: %s", raw[:200])
        return {}


def check_rule(transcript: str, rule: dict) -> dict:
    system = (
        "You are a compliance auditor for a telecom contact centre. "
        "Evaluate the transcript against the rule and answer concisely."
    )
    user = f"TRANSCRIPT:\n{transcript[:3000]}\n\nRULE: {rule['prompt']}"
    answer = call_openai(system, user, max_tokens=120)

    if answer is None:
        status = "ERROR"
        detail = "OpenAI call failed after retries"
    elif answer.upper().startswith("YES"):
        # For PII_001 and UPSELL_001, YES means a violation occurred
        if rule["id"] in ("PII_001", "UPSELL_001"):
            status = "FAIL"
        else:
            status = "PASS"
    elif answer.upper().startswith("N/A"):
        status = "N/A"
    elif answer.upper().startswith("NO"):
        if rule["id"] in ("PII_001", "UPSELL_001"):
            status = "PASS"
        else:
            status = "FAIL"
    else:
        status = "FAIL"

    return {
        "rule_id": rule["id"],
        "rule_name": rule["name"],
        "severity": rule["severity"],
        "status": status,
        "detail": answer or "",
    }


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversation_analysis (
            conversation_id     TEXT PRIMARY KEY,
            summary             TEXT,
            sentiment           TEXT,
            primary_topic       TEXT,
            sub_topic           TEXT,
            customer_intent     TEXT,
            resolution_status   TEXT,
            agent_name          TEXT,
            customer_name       TEXT,
            hold_mentioned      INTEGER,
            transfer_occurred   INTEGER,
            handle_time_minutes INTEGER,
            customer_effort     INTEGER,
            analysed_at         TEXT
        );

        CREATE TABLE IF NOT EXISTS compliance_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            rule_id         TEXT,
            rule_name       TEXT,
            severity        TEXT,
            status          TEXT,
            detail          TEXT,
            checked_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS compliance_summary (
            conversation_id TEXT PRIMARY KEY,
            total_rules     INTEGER,
            passed          INTEGER,
            failed          INTEGER,
            critical_fails  INTEGER,
            major_fails     INTEGER,
            na_count        INTEGER,
            score_pct       REAL,
            checked_at      TEXT
        );
    """)
    conn.commit()


def save_analysis(conn: sqlite3.Connection, cid: str, analysis: dict):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO conversation_analysis VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid,
            analysis.get("summary"),
            analysis.get("sentiment"),
            analysis.get("primary_topic"),
            analysis.get("sub_topic"),
            analysis.get("customer_intent"),
            analysis.get("resolution_status"),
            analysis.get("agent_name"),
            analysis.get("customer_name"),
            1 if analysis.get("hold_mentioned") else 0,
            1 if analysis.get("transfer_occurred") else 0,
            analysis.get("estimated_handle_time_minutes"),
            analysis.get("customer_effort_score"),
            now,
        ),
    )
    conn.commit()


def save_compliance(conn: sqlite3.Connection, cid: str, results: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        conn.execute(
            "INSERT INTO compliance_results "
            "(conversation_id, rule_id, rule_name, severity, status, detail, checked_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, r["rule_id"], r["rule_name"], r["severity"], r["status"], r["detail"], now),
        )

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    na = sum(1 for r in results if r["status"] == "N/A")
    critical_fails = sum(1 for r in results if r["status"] == "FAIL" and r["severity"] == "critical")
    major_fails = sum(1 for r in results if r["status"] == "FAIL" and r["severity"] == "major")
    scorable = passed + failed
    score = round(passed / scorable * 100, 1) if scorable else 100.0

    conn.execute(
        "INSERT OR REPLACE INTO compliance_summary VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, len(results), passed, failed, critical_fails, major_fails, na, score, now),
    )
    conn.commit()


def main():
    log.info("Connecting to Azure AI Search (%s)", SEARCH_INDEX)
    search = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=credential,
    )

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    transcripts = list(search.search(search_text="*", top=20))
    log.info("Retrieved %d transcripts for analysis", len(transcripts))

    for item in transcripts:
        cid = item.get("id", "unknown")
        text = item.get("content", "")
        if not text:
            continue

        log.info("Processing %s ...", cid)

        analysis = extract_analysis(text)
        if analysis:
            save_analysis(conn, cid, analysis)
            log.info(
                "  topic=%s  sentiment=%s  resolution=%s",
                analysis.get("primary_topic", "-"),
                analysis.get("sentiment", "-"),
                analysis.get("resolution_status", "-"),
            )

        results = [check_rule(text, rule) for rule in COMPLIANCE_RULES]
        save_compliance(conn, cid, results)
        passed = sum(1 for r in results if r["status"] == "PASS")
        log.info("  compliance: %d/%d rules passed", passed, len(COMPLIANCE_RULES))

    conn.close()
    log.info("Complete. Results written to %s", DB_PATH)


if __name__ == "__main__":
    main()
