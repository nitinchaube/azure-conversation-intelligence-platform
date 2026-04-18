"""
QA review interface for contact-centre AI outputs.

Connects to Azure AI Search to display call transcripts and their
AI-generated analysis (from 04_compliance_check.py), then collects
human reviewer feedback on accuracy. Results feed into the analytics
dashboard.

Usage:
    streamlit run feedback_app.py
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from azure.search.documents import SearchClient
from azure.identity import DefaultAzureCredential

SEARCH_ENDPOINT = "https://srch-ckmv2inaoo.search.windows.net"
SEARCH_INDEX = "call_transcripts_index"

BASE_DIR = Path(__file__).parent
FEEDBACK_DB = BASE_DIR / "feedback_data.db"
COMPLIANCE_DB = BASE_DIR / "compliance_results.db"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_feedback_db():
    conn = _get_conn(FEEDBACK_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS human_feedback (
            feedback_id          TEXT PRIMARY KEY,
            conversation_id      TEXT,
            reviewer_id          TEXT,
            summary_accuracy     INTEGER,
            sentiment_accuracy   INTEGER,
            topic_accuracy       INTEGER,
            resolution_accuracy  INTEGER,
            compliance_agree     INTEGER,
            agent_tone_rating    INTEGER,
            issue_category       TEXT,
            corrected_sentiment  TEXT,
            comments             TEXT,
            submitted_at         TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_feedback(data: dict):
    conn = _get_conn(FEEDBACK_DB)
    conn.execute(
        "INSERT OR REPLACE INTO human_feedback VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            data["feedback_id"],
            data["conversation_id"],
            data["reviewer_id"],
            data["summary_accuracy"],
            data["sentiment_accuracy"],
            data["topic_accuracy"],
            data["resolution_accuracy"],
            data["compliance_agree"],
            data["agent_tone_rating"],
            data["issue_category"],
            data["corrected_sentiment"],
            data["comments"],
            data["submitted_at"],
        ),
    )
    conn.commit()
    conn.close()


def is_reviewed(conversation_id: str, reviewer_id: str) -> bool:
    conn = _get_conn(FEEDBACK_DB)
    row = conn.execute(
        "SELECT 1 FROM human_feedback WHERE conversation_id=? AND reviewer_id=?",
        (conversation_id, reviewer_id),
    ).fetchone()
    conn.close()
    return row is not None


def get_review_count() -> int:
    if not FEEDBACK_DB.exists():
        return 0
    conn = _get_conn(FEEDBACK_DB)
    row = conn.execute("SELECT COUNT(*) FROM human_feedback").fetchone()
    conn.close()
    return row[0] if row else 0


def load_analysis(conversation_id: str) -> dict | None:
    if not COMPLIANCE_DB.exists():
        return None
    conn = _get_conn(COMPLIANCE_DB)
    row = conn.execute(
        "SELECT * FROM conversation_analysis WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def load_compliance_for(conversation_id: str) -> list[dict]:
    if not COMPLIANCE_DB.exists():
        return []
    conn = _get_conn(COMPLIANCE_DB)
    rows = conn.execute(
        "SELECT rule_name, severity, status, detail FROM compliance_results "
        "WHERE conversation_id = ? ORDER BY severity DESC",
        (conversation_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Azure AI Search
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Connecting to Azure AI Search...")
def get_search_client():
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=DefaultAzureCredential(),
    )


@st.cache_data(ttl=300, show_spinner="Loading transcripts...")
def load_transcripts(limit: int = 30) -> list[dict]:
    results = get_search_client().search(search_text="*", top=limit)
    return [
        {"id": r.get("id", ""), "content": r.get("content", ""), "source": r.get("sourceurl", "")}
        for r in results
    ]


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="CKM - QA Review", layout="wide")
    init_feedback_db()

    # -- sidebar --
    with st.sidebar:
        st.header("QA Review Console")
        reviewer_id = st.text_input("Reviewer ID", value="reviewer_01")
        st.markdown("---")
        st.metric("Reviews submitted", get_review_count())
        st.markdown("---")
        st.caption(
            "Reviews are stored locally and aggregated by the analytics "
            "dashboard. Run 04_compliance_check.py first to populate AI "
            "analysis data."
        )

    st.title("Conversation QA Review")
    st.markdown(
        "Compare AI-generated analysis against the original transcript. "
        "Rate each output dimension so the model improvement pipeline can "
        "identify weak spots."
    )

    transcripts = load_transcripts()
    if not transcripts:
        st.warning("No transcripts found. Run process_sample_data.sh first.")
        return

    selected_id = st.selectbox(
        "Select transcript",
        [t["id"] for t in transcripts],
        format_func=lambda x: x[:50],
    )
    conv = next(t for t in transcripts if t["id"] == selected_id)

    reviewed = is_reviewed(selected_id, reviewer_id)
    if reviewed:
        st.info("Already reviewed by this reviewer. Select another transcript.")

    # -- transcript + analysis side by side --
    left, right = st.columns([1, 1])

    with left:
        st.subheader("Transcript")
        st.text_area(
            "Full transcript",
            value=conv.get("content", "(empty)"),
            height=420,
            disabled=True,
            label_visibility="collapsed",
        )
        if conv.get("source"):
            st.caption(f"Source: {conv['source']}")

    with right:
        analysis = load_analysis(selected_id)
        compliance = load_compliance_for(selected_id)

        st.subheader("AI-Generated Analysis")
        if analysis:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**Sentiment:** {analysis.get('sentiment', '-')}")
                st.markdown(f"**Topic:** {analysis.get('primary_topic', '-')}")
                st.markdown(f"**Sub-topic:** {analysis.get('sub_topic', '-')}")
                st.markdown(f"**Intent:** {analysis.get('customer_intent', '-')}")
            with col_b:
                st.markdown(f"**Resolution:** {analysis.get('resolution_status', '-')}")
                st.markdown(f"**Agent:** {analysis.get('agent_name') or '-'}")
                st.markdown(f"**Handle time (est.):** {analysis.get('handle_time_minutes') or '-'} min")
                st.markdown(f"**Customer effort:** {analysis.get('customer_effort') or '-'}/5")
            st.markdown("**Summary**")
            st.text(analysis.get("summary", "-"))
        else:
            st.warning("No AI analysis found. Run 04_compliance_check.py first.")

        if compliance:
            st.markdown("**Compliance Results**")
            for c in compliance:
                label = "PASS" if c["status"] == "PASS" else c["status"]
                color = (
                    "green" if c["status"] == "PASS"
                    else "red" if c["status"] == "FAIL"
                    else "gray"
                )
                st.markdown(
                    f"- :{color}[{label}] **{c['rule_name']}** ({c['severity']})"
                )

    # -- feedback form --
    st.markdown("---")
    st.subheader("Your Assessment")

    with st.form("review_form", clear_on_submit=True):
        r1, r2, r3 = st.columns(3)
        with r1:
            summary_acc = st.slider("Summary accuracy", 1, 5, 3, help="1 = wrong, 5 = spot-on")
            sentiment_acc = st.slider("Sentiment accuracy", 1, 5, 3)
        with r2:
            topic_acc = st.slider("Topic classification accuracy", 1, 5, 3)
            resolution_acc = st.slider("Resolution status accuracy", 1, 5, 3)
        with r3:
            compliance_agree = st.slider("Agree with compliance results", 1, 5, 3)
            tone_rating = st.slider("Agent tone / professionalism", 1, 5, 3)

        corrected_sentiment = st.selectbox(
            "If sentiment was wrong, what should it be?",
            ["(no correction)", "positive", "negative", "neutral", "mixed"],
        )
        issue_cat = st.selectbox(
            "Issue category (your judgment)",
            [
                "billing", "technical_support", "account_management",
                "device_issue", "network_outage", "cancellation",
                "feedback", "general_inquiry", "other",
            ],
        )
        comments = st.text_area("Additional notes", height=80)
        submitted = st.form_submit_button("Submit review", disabled=reviewed)

    if submitted:
        save_feedback({
            "feedback_id": str(uuid.uuid4()),
            "conversation_id": selected_id,
            "reviewer_id": reviewer_id,
            "summary_accuracy": summary_acc,
            "sentiment_accuracy": sentiment_acc,
            "topic_accuracy": topic_acc,
            "resolution_accuracy": resolution_acc,
            "compliance_agree": compliance_agree,
            "agent_tone_rating": tone_rating,
            "issue_category": issue_cat,
            "corrected_sentiment": corrected_sentiment if corrected_sentiment != "(no correction)" else None,
            "comments": comments,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        })
        st.success("Review saved.")
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
