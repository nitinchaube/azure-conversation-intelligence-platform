"""
Conversation Intelligence Platform
====================================
Single-page enterprise application for contact-centre operations.

Modules:
    1. Operations Dashboard  - KPIs, topic/sentiment/resolution analytics
    2. Agent Performance     - Per-agent scorecard with coaching signals
    3. Compliance Monitor    - Rule-level pass rates, violation tracker, risk heatmap
    4. QA Review             - Human-in-the-loop feedback on AI outputs
    5. Conversation Explorer - Search and drill into individual transcripts

Data sources:
    - Azure AI Search (call transcripts)
    - compliance_results.db (AI-generated analysis + compliance checks)
    - feedback_data.db (human QA reviews)

Usage:
    streamlit run app.py
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from azure.search.documents import SearchClient
from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEARCH_ENDPOINT = "https://srch-ckmv2inaoo.search.windows.net"
SEARCH_INDEX = "call_transcripts_index"

BASE_DIR = Path(__file__).parent
COMPLIANCE_DB = BASE_DIR / "compliance_results.db"
FEEDBACK_DB = BASE_DIR / "feedback_data.db"

SENTIMENT_COLORS = {
    "positive": "#2ecc71",
    "negative": "#e74c3c",
    "neutral": "#95a5a6",
    "mixed": "#f39c12",
}

# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def _conn(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def load_df(db: Path, table: str) -> pd.DataFrame:
    if not db.exists():
        return pd.DataFrame()
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", sqlite3.connect(db))
    except Exception:
        return pd.DataFrame()


def init_feedback_db():
    conn = _conn(FEEDBACK_DB)
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
    conn = _conn(FEEDBACK_DB)
    conn.execute(
        "INSERT OR REPLACE INTO human_feedback VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(data[k] for k in [
            "feedback_id", "conversation_id", "reviewer_id",
            "summary_accuracy", "sentiment_accuracy", "topic_accuracy",
            "resolution_accuracy", "compliance_agree", "agent_tone_rating",
            "issue_category", "corrected_sentiment", "comments", "submitted_at",
        ]),
    )
    conn.commit()
    conn.close()


def is_reviewed(cid: str, reviewer: str) -> bool:
    if not FEEDBACK_DB.exists():
        return False
    conn = _conn(FEEDBACK_DB)
    row = conn.execute(
        "SELECT 1 FROM human_feedback WHERE conversation_id=? AND reviewer_id=?",
        (cid, reviewer),
    ).fetchone()
    conn.close()
    return row is not None


@st.cache_resource(show_spinner="Connecting to Azure AI Search...")
def get_search_client():
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=DefaultAzureCredential(),
    )


@st.cache_data(ttl=300, show_spinner="Loading transcripts...")
def load_transcripts(limit: int = 50) -> list[dict]:
    results = get_search_client().search(search_text="*", top=limit)
    return [
        {"id": r.get("id", ""), "content": r.get("content", ""), "source": r.get("sourceurl", "")}
        for r in results
    ]


# ---------------------------------------------------------------------------
# Page: Operations Dashboard
# ---------------------------------------------------------------------------

def page_dashboard():
    st.header("Operations Dashboard")

    analysis = load_df(COMPLIANCE_DB, "conversation_analysis")
    summary = load_df(COMPLIANCE_DB, "compliance_summary")

    if analysis.empty:
        st.warning("No analysis data. Run 04_compliance_check.py first.")
        return

    n = len(analysis)

    # KPI row
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Calls Analysed", n)

    resolved = (analysis["resolution_status"] == "resolved").sum()
    k2.metric("Resolution Rate", f"{resolved / n * 100:.0f}%")

    avg_effort = analysis["customer_effort"].dropna().mean()
    k3.metric("Avg Customer Effort", f"{avg_effort:.1f}/5" if pd.notna(avg_effort) else "-")

    avg_handle = analysis["handle_time_minutes"].dropna().mean()
    k4.metric("Avg Handle Time", f"{avg_handle:.0f} min" if pd.notna(avg_handle) else "-")

    hold_pct = analysis["hold_mentioned"].mean() * 100
    k5.metric("Hold Rate", f"{hold_pct:.0f}%")

    if not summary.empty:
        k6.metric("Compliance Score", f"{summary['score_pct'].mean():.0f}%")
    else:
        k6.metric("Compliance Score", "-")

    st.markdown("---")

    # Charts row 1
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Call Topics")
        topics = analysis["primary_topic"].value_counts()
        st.bar_chart(topics, horizontal=True)

    with c2:
        st.subheader("Sentiment Distribution")
        sent = analysis["sentiment"].value_counts()
        st.bar_chart(sent)

    # Charts row 2
    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Customer Intent")
        intent = analysis["customer_intent"].value_counts()
        st.bar_chart(intent)

    with c4:
        st.subheader("Resolution Status")
        res = analysis["resolution_status"].value_counts()
        st.bar_chart(res)

    # Customer effort distribution
    st.subheader("Customer Effort Distribution")
    effort = analysis["customer_effort"].dropna().astype(int).value_counts().sort_index()
    effort.index = [f"Score {i}" for i in effort.index]
    st.bar_chart(effort)

    # Unresolved / escalated calls -- actionable
    flagged = analysis[analysis["resolution_status"].isin(["unresolved", "escalated", "follow_up_needed"])]
    if not flagged.empty:
        st.subheader("Action Required")
        st.caption("Calls that need follow-up, escalation, or remain unresolved.")
        display = flagged[["conversation_id", "primary_topic", "resolution_status",
                           "agent_name", "customer_effort", "summary"]].copy()
        display["conversation_id"] = display["conversation_id"].str[:30]
        display["summary"] = display["summary"].str[:100]
        st.dataframe(display, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page: Agent Performance
# ---------------------------------------------------------------------------

def page_agents():
    st.header("Agent Performance")

    analysis = load_df(COMPLIANCE_DB, "conversation_analysis")
    compliance = load_df(COMPLIANCE_DB, "compliance_results")
    summary = load_df(COMPLIANCE_DB, "compliance_summary")

    if analysis.empty:
        st.warning("No analysis data. Run 04_compliance_check.py first.")
        return

    agents = analysis[analysis["agent_name"].notna()].copy()
    if agents.empty:
        st.info("No agent names found in transcripts.")
        return

    # Build agent-level metrics
    agent_stats = agents.groupby("agent_name").agg(
        calls=("conversation_id", "count"),
        avg_effort=("customer_effort", "mean"),
        avg_handle=("handle_time_minutes", "mean"),
        resolved=("resolution_status", lambda x: (x == "resolved").sum()),
        holds=("hold_mentioned", "sum"),
        transfers=("transfer_occurred", "sum"),
    ).round(1)
    agent_stats["resolution_pct"] = (agent_stats["resolved"] / agent_stats["calls"] * 100).round(0)

    # Merge compliance scores per agent
    if not summary.empty and not analysis.empty:
        merged = analysis[["conversation_id", "agent_name"]].merge(
            summary[["conversation_id", "score_pct", "critical_fails", "major_fails"]],
            on="conversation_id",
            how="left",
        )
        comp = merged.groupby("agent_name").agg(
            avg_compliance=("score_pct", "mean"),
            total_critical=("critical_fails", "sum"),
            total_major=("major_fails", "sum"),
        ).round(1)
        agent_stats = agent_stats.join(comp)

    st.subheader("Scorecard")
    display_cols = {
        "calls": "Calls",
        "resolution_pct": "Resolution %",
        "avg_effort": "Avg Cust. Effort",
        "avg_handle": "Avg Handle (min)",
        "holds": "Holds",
        "transfers": "Transfers",
    }
    if "avg_compliance" in agent_stats.columns:
        display_cols["avg_compliance"] = "Compliance %"
        display_cols["total_critical"] = "Critical Violations"
        display_cols["total_major"] = "Major Violations"

    st.dataframe(
        agent_stats[list(display_cols.keys())].rename(columns=display_cols),
        use_container_width=True,
    )

    # Coaching signals
    st.subheader("Coaching Signals")
    for _, row in agent_stats.iterrows():
        agent = row.name
        signals = []
        if row.get("avg_effort", 0) >= 4:
            signals.append("High customer effort -- review call handling process")
        if row.get("resolution_pct", 100) < 60:
            signals.append("Low resolution rate -- may need additional training")
        if row.get("avg_compliance", 100) < 70:
            signals.append("Below compliance threshold -- schedule compliance review")
        if row.get("total_critical", 0) > 0:
            signals.append(f"{int(row['total_critical'])} critical violation(s) -- immediate review needed")
        if row.get("holds", 0) / max(row.get("calls", 1), 1) > 0.5:
            signals.append("Frequent holds -- knowledge gap or system issue")

        if signals:
            with st.expander(f"{agent} -- {len(signals)} signal(s)"):
                for s in signals:
                    st.markdown(f"- {s}")

    # Comparative charts
    if len(agent_stats) > 1:
        st.subheader("Resolution Rate by Agent")
        st.bar_chart(agent_stats["resolution_pct"].sort_values())

        if "avg_compliance" in agent_stats.columns:
            st.subheader("Compliance Score by Agent")
            st.bar_chart(agent_stats["avg_compliance"].sort_values())


# ---------------------------------------------------------------------------
# Page: Compliance Monitor
# ---------------------------------------------------------------------------

def page_compliance():
    st.header("Compliance Monitor")

    summary = load_df(COMPLIANCE_DB, "compliance_summary")
    results = load_df(COMPLIANCE_DB, "compliance_results")

    if summary.empty:
        st.warning("No compliance data. Run 04_compliance_check.py first.")
        return

    # KPIs
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Avg Compliance Score", f"{summary['score_pct'].mean():.1f}%")
    c2.metric("Critical Violations", int(summary["critical_fails"].sum()))
    c3.metric("Major Violations", int(summary["major_fails"].sum()))
    clean = (summary["failed"] == 0).sum()
    c4.metric("Clean Calls", f"{clean}/{len(summary)}")

    st.markdown("---")

    # Per-rule pass rate
    if not results.empty:
        st.subheader("Pass Rate by Rule")
        scorable = results[results["status"].isin(["PASS", "FAIL"])]
        if not scorable.empty:
            rule_pass = (
                scorable.groupby("rule_name")["status"]
                .apply(lambda x: round((x == "PASS").mean() * 100, 1))
                .sort_values()
            )
            st.bar_chart(rule_pass, horizontal=True)

        # Risk heatmap: severity x rule
        st.subheader("Violations by Severity")
        fails = results[results["status"] == "FAIL"]
        if not fails.empty:
            pivot = fails.groupby(["rule_name", "severity"]).size().unstack(fill_value=0)
            st.dataframe(pivot, use_container_width=True)

        # Detailed violation log
        st.subheader("Violation Log")
        if not fails.empty:
            display = fails[["conversation_id", "rule_name", "severity", "detail"]].copy()
            display["conversation_id"] = display["conversation_id"].str[:30]
            display["detail"] = display["detail"].str[:150]
            st.dataframe(
                display.sort_values("severity", ascending=False),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("No violations detected.")

    # Score distribution
    st.subheader("Compliance Score Distribution")
    bins = pd.cut(summary["score_pct"], bins=[0, 25, 50, 75, 100], labels=["0-25%", "26-50%", "51-75%", "76-100%"])
    dist = bins.value_counts().sort_index()
    st.bar_chart(dist)


# ---------------------------------------------------------------------------
# Page: QA Review
# ---------------------------------------------------------------------------

def page_qa_review():
    st.header("QA Review")
    st.markdown(
        "Compare AI-generated analysis against the original transcript and rate "
        "each dimension. Submitted reviews feed into the AI accuracy metrics."
    )

    init_feedback_db()
    reviewer_id = st.text_input("Reviewer ID", value="reviewer_01")

    transcripts = load_transcripts()
    if not transcripts:
        st.warning("No transcripts found.")
        return

    selected_id = st.selectbox("Select transcript", [t["id"] for t in transcripts])
    conv = next(t for t in transcripts if t["id"] == selected_id)
    reviewed = is_reviewed(selected_id, reviewer_id)

    if reviewed:
        st.info("Already reviewed. Select another transcript.")

    left, right = st.columns([1, 1])

    with left:
        st.subheader("Transcript")
        st.text_area(
            "transcript_text",
            value=conv.get("content", "(empty)"),
            height=400,
            disabled=True,
            label_visibility="collapsed",
        )

    with right:
        st.subheader("AI Analysis")
        analysis = None
        if COMPLIANCE_DB.exists():
            conn = _conn(COMPLIANCE_DB)
            row = conn.execute(
                "SELECT * FROM conversation_analysis WHERE conversation_id=?",
                (selected_id,),
            ).fetchone()
            conn.close()
            if row:
                analysis = dict(row)

        if analysis:
            a1, a2 = st.columns(2)
            with a1:
                st.markdown(f"**Sentiment:** {analysis.get('sentiment', '-')}")
                st.markdown(f"**Topic:** {analysis.get('primary_topic', '-')}")
                st.markdown(f"**Intent:** {analysis.get('customer_intent', '-')}")
            with a2:
                st.markdown(f"**Resolution:** {analysis.get('resolution_status', '-')}")
                st.markdown(f"**Agent:** {analysis.get('agent_name') or '-'}")
                st.markdown(f"**Effort:** {analysis.get('customer_effort') or '-'}/5")

            st.markdown("**Summary**")
            st.caption(analysis.get("summary", "-"))

            # Compliance results inline
            if COMPLIANCE_DB.exists():
                conn = _conn(COMPLIANCE_DB)
                rows = conn.execute(
                    "SELECT rule_name, severity, status FROM compliance_results WHERE conversation_id=?",
                    (selected_id,),
                ).fetchall()
                conn.close()
                if rows:
                    st.markdown("**Compliance**")
                    for r in rows:
                        color = "green" if r["status"] == "PASS" else "red" if r["status"] == "FAIL" else "gray"
                        st.markdown(f"- :{color}[{r['status']}] {r['rule_name']} ({r['severity']})")
        else:
            st.info("Run 04_compliance_check.py to generate analysis.")

    st.markdown("---")
    st.subheader("Assessment")

    with st.form("review_form", clear_on_submit=True):
        r1, r2, r3 = st.columns(3)
        with r1:
            summary_acc = st.slider("Summary accuracy", 1, 5, 3)
            sentiment_acc = st.slider("Sentiment accuracy", 1, 5, 3)
        with r2:
            topic_acc = st.slider("Topic accuracy", 1, 5, 3)
            resolution_acc = st.slider("Resolution accuracy", 1, 5, 3)
        with r3:
            compliance_agree = st.slider("Compliance agreement", 1, 5, 3)
            tone_rating = st.slider("Agent professionalism", 1, 5, 3)

        corrected_sentiment = st.selectbox(
            "Correct sentiment (if wrong)",
            ["(no correction)", "positive", "negative", "neutral", "mixed"],
        )
        issue_cat = st.selectbox(
            "Your issue classification",
            ["billing", "technical_support", "account_management", "device_issue",
             "network_outage", "cancellation", "feedback", "general_inquiry", "other"],
        )
        comments = st.text_area("Notes", height=60)
        submitted = st.form_submit_button("Submit", disabled=reviewed)

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


# ---------------------------------------------------------------------------
# Page: AI Accuracy
# ---------------------------------------------------------------------------

def page_ai_accuracy():
    st.header("AI Model Accuracy")

    feedback = load_df(FEEDBACK_DB, "human_feedback")
    if feedback.empty:
        st.info("No QA reviews submitted yet. Use the QA Review page to submit feedback.")
        return

    n = len(feedback)

    # KPIs
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Reviews", n)
    m2.metric("Summary Accuracy", f"{feedback['summary_accuracy'].mean():.1f}/5")
    m3.metric("Sentiment Accuracy", f"{feedback['sentiment_accuracy'].mean():.1f}/5")
    m4.metric("Topic Accuracy", f"{feedback['topic_accuracy'].mean():.1f}/5")
    m5.metric("Unique Reviewers", feedback["reviewer_id"].nunique())

    st.markdown("---")

    # Accuracy by dimension
    st.subheader("Accuracy by Dimension")
    dimensions = {
        "Summary": feedback["summary_accuracy"].mean(),
        "Sentiment": feedback["sentiment_accuracy"].mean(),
        "Topic": feedback["topic_accuracy"].mean(),
        "Resolution": feedback["resolution_accuracy"].mean(),
        "Compliance": feedback["compliance_agree"].mean(),
        "Agent Tone": feedback["agent_tone_rating"].mean(),
    }
    dim_df = pd.Series(dimensions).sort_values()
    st.bar_chart(dim_df, horizontal=True)

    c1, c2 = st.columns(2)

    # Sentiment corrections
    with c1:
        corrections = feedback[feedback["corrected_sentiment"].notna()]
        if not corrections.empty:
            st.subheader("Sentiment Corrections")
            st.caption("Reviewers corrected the AI sentiment to:")
            st.bar_chart(corrections["corrected_sentiment"].value_counts())
        else:
            st.subheader("Sentiment Corrections")
            st.caption("No corrections submitted -- AI sentiment is tracking well.")

    # Reviewer-assigned categories vs AI
    with c2:
        if "issue_category" in feedback.columns:
            st.subheader("Reviewer Issue Categories")
            cats = feedback["issue_category"].value_counts()
            if not cats.empty:
                st.bar_chart(cats)

    # Low-confidence outputs (where reviewers gave 1 or 2)
    low_conf = feedback[
        (feedback["summary_accuracy"] <= 2) |
        (feedback["sentiment_accuracy"] <= 2) |
        (feedback["topic_accuracy"] <= 2)
    ]
    if not low_conf.empty:
        st.subheader("Low-Confidence AI Outputs")
        st.caption("Conversations where at least one dimension scored 1 or 2.")
        display = low_conf[["conversation_id", "summary_accuracy", "sentiment_accuracy",
                            "topic_accuracy", "comments"]].copy()
        display["conversation_id"] = display["conversation_id"].str[:30]
        st.dataframe(display, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page: Conversation Explorer
# ---------------------------------------------------------------------------

def page_explorer():
    st.header("Conversation Explorer")

    analysis = load_df(COMPLIANCE_DB, "conversation_analysis")
    if analysis.empty:
        st.warning("No analysis data available.")
        return

    # Filters
    f1, f2, f3 = st.columns(3)
    with f1:
        topic_filter = st.multiselect(
            "Filter by topic",
            sorted(analysis["primary_topic"].dropna().unique()),
        )
    with f2:
        sentiment_filter = st.multiselect(
            "Filter by sentiment",
            sorted(analysis["sentiment"].dropna().unique()),
        )
    with f3:
        resolution_filter = st.multiselect(
            "Filter by resolution",
            sorted(analysis["resolution_status"].dropna().unique()),
        )

    filtered = analysis.copy()
    if topic_filter:
        filtered = filtered[filtered["primary_topic"].isin(topic_filter)]
    if sentiment_filter:
        filtered = filtered[filtered["sentiment"].isin(sentiment_filter)]
    if resolution_filter:
        filtered = filtered[filtered["resolution_status"].isin(resolution_filter)]

    st.caption(f"Showing {len(filtered)} of {len(analysis)} conversations")

    display = filtered[[
        "conversation_id", "primary_topic", "sentiment", "customer_intent",
        "resolution_status", "agent_name", "customer_effort", "handle_time_minutes",
    ]].copy()
    display["conversation_id"] = display["conversation_id"].str[:30]
    st.dataframe(display, use_container_width=True, hide_index=True)

    # Drill-down
    if not filtered.empty:
        selected = st.selectbox("Drill into conversation", filtered["conversation_id"].tolist())
        if selected:
            row = filtered[filtered["conversation_id"] == selected].iloc[0]
            st.markdown("---")
            st.subheader(f"Details: {selected[:40]}")

            d1, d2 = st.columns(2)
            with d1:
                st.markdown(f"**Topic:** {row.get('primary_topic', '-')}")
                st.markdown(f"**Sub-topic:** {row.get('sub_topic', '-')}")
                st.markdown(f"**Sentiment:** {row.get('sentiment', '-')}")
                st.markdown(f"**Intent:** {row.get('customer_intent', '-')}")
            with d2:
                st.markdown(f"**Resolution:** {row.get('resolution_status', '-')}")
                st.markdown(f"**Agent:** {row.get('agent_name') or '-'}")
                st.markdown(f"**Handle time:** {row.get('handle_time_minutes') or '-'} min")
                st.markdown(f"**Customer effort:** {row.get('customer_effort') or '-'}/5")

            st.markdown("**AI Summary**")
            st.text(row.get("summary", "-"))

            # Fetch transcript from search
            try:
                results = list(get_search_client().search(
                    search_text="*",
                    filter=f"id eq '{selected}'",
                    top=1,
                ))
                if results:
                    st.markdown("**Full Transcript**")
                    st.text_area("full_text", value=results[0].get("content", ""), height=300,
                                 disabled=True, label_visibility="collapsed")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="Conversation Intelligence Platform",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.sidebar.title("Conversation Intelligence")
    st.sidebar.caption("Enterprise Contact Centre Analytics")

    page = st.sidebar.radio(
        "Navigate",
        [
            "Operations Dashboard",
            "Agent Performance",
            "Compliance Monitor",
            "QA Review",
            "AI Accuracy",
            "Conversation Explorer",
        ],
    )

    st.sidebar.markdown("---")

    # Quick stats in sidebar
    analysis = load_df(COMPLIANCE_DB, "conversation_analysis")
    feedback = load_df(FEEDBACK_DB, "human_feedback")
    if not analysis.empty:
        st.sidebar.metric("Conversations", len(analysis))
    if not feedback.empty:
        st.sidebar.metric("QA Reviews", len(feedback))

    st.sidebar.markdown("---")
    st.sidebar.caption("Powered by Azure AI + OpenAI")

    pages = {
        "Operations Dashboard": page_dashboard,
        "Agent Performance": page_agents,
        "Compliance Monitor": page_compliance,
        "QA Review": page_qa_review,
        "AI Accuracy": page_ai_accuracy,
        "Conversation Explorer": page_explorer,
    }
    pages[page]()


if __name__ == "__main__":
    main()
