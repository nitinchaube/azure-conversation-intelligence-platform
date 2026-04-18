"""
Operations analytics dashboard.

Reads conversation analysis, compliance results, and human feedback from
the local SQLite databases and renders an interactive Streamlit dashboard
with metrics that a contact-centre operations team would actually use:

  - Call volume & topic distribution
  - Agent compliance scorecard
  - Sentiment breakdown and customer-effort distribution
  - Resolution rates and escalation tracking
  - AI accuracy metrics from human QA feedback
  - Compliance violation trends by severity

Usage:
    streamlit run 05_feedback_analytics.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).parent
COMPLIANCE_DB = BASE_DIR / "compliance_results.db"
FEEDBACK_DB = BASE_DIR / "feedback_data.db"


def load_table(db_path: Path, table: str) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


def main():
    st.set_page_config(page_title="CKM - Operations Analytics", layout="wide")
    st.title("Contact Centre Operations Analytics")

    analysis_df = load_table(COMPLIANCE_DB, "conversation_analysis")
    compliance_df = load_table(COMPLIANCE_DB, "compliance_results")
    summary_df = load_table(COMPLIANCE_DB, "compliance_summary")
    feedback_df = load_table(FEEDBACK_DB, "human_feedback")

    if analysis_df.empty and compliance_df.empty:
        st.warning(
            "No data found. Run 04_compliance_check.py first to populate "
            "the analysis and compliance databases."
        )
        return

    # ------------------------------------------------------------------
    # Row 1: top-level KPIs
    # ------------------------------------------------------------------
    st.header("Key Performance Indicators")
    k1, k2, k3, k4, k5 = st.columns(5)

    total_calls = len(analysis_df) if not analysis_df.empty else 0
    k1.metric("Total Calls Analysed", total_calls)

    if not analysis_df.empty:
        resolved = (analysis_df["resolution_status"] == "resolved").sum()
        resolution_rate = round(resolved / total_calls * 100, 1) if total_calls else 0
        k2.metric("Resolution Rate", f"{resolution_rate}%")

        avg_effort = analysis_df["customer_effort"].dropna().mean()
        k3.metric("Avg Customer Effort", f"{avg_effort:.1f} / 5" if pd.notna(avg_effort) else "-")

        avg_handle = analysis_df["handle_time_minutes"].dropna().mean()
        k4.metric("Avg Handle Time", f"{avg_handle:.0f} min" if pd.notna(avg_handle) else "-")

        hold_pct = round(analysis_df["hold_mentioned"].mean() * 100, 1)
        k5.metric("Calls With Hold", f"{hold_pct}%")
    else:
        for col in [k2, k3, k4, k5]:
            col.metric("-", "-")

    st.markdown("---")

    # ------------------------------------------------------------------
    # Row 2: topic & sentiment breakdown
    # ------------------------------------------------------------------
    if not analysis_df.empty:
        left, right = st.columns(2)

        with left:
            st.subheader("Call Topic Distribution")
            topic_counts = analysis_df["primary_topic"].value_counts()
            if not topic_counts.empty:
                st.bar_chart(topic_counts)

        with right:
            st.subheader("Sentiment Breakdown")
            sentiment_counts = analysis_df["sentiment"].value_counts()
            if not sentiment_counts.empty:
                st.bar_chart(sentiment_counts)

        left2, right2 = st.columns(2)

        with left2:
            st.subheader("Customer Intent")
            intent_counts = analysis_df["customer_intent"].value_counts()
            if not intent_counts.empty:
                st.bar_chart(intent_counts)

        with right2:
            st.subheader("Resolution Status")
            res_counts = analysis_df["resolution_status"].value_counts()
            if not res_counts.empty:
                st.bar_chart(res_counts)

        st.markdown("---")

        # Customer effort distribution
        st.subheader("Customer Effort Score Distribution")
        effort_vals = analysis_df["customer_effort"].dropna().astype(int)
        if not effort_vals.empty:
            effort_counts = effort_vals.value_counts().sort_index()
            effort_counts.index = [f"Score {i}" for i in effort_counts.index]
            st.bar_chart(effort_counts)

        st.markdown("---")

    # ------------------------------------------------------------------
    # Row 3: compliance scorecard
    # ------------------------------------------------------------------
    if not summary_df.empty:
        st.header("Compliance Scorecard")
        c1, c2, c3, c4 = st.columns(4)

        avg_score = summary_df["score_pct"].mean()
        c1.metric("Avg Compliance Score", f"{avg_score:.1f}%")

        critical = summary_df["critical_fails"].sum()
        c2.metric("Critical Violations", int(critical))

        major = summary_df["major_fails"].sum()
        c3.metric("Major Violations", int(major))

        clean = (summary_df["failed"] == 0).sum()
        c4.metric("Clean Calls (0 fails)", int(clean))

        # per-rule pass rate
        if not compliance_df.empty:
            st.subheader("Pass Rate by Rule")
            rule_stats = (
                compliance_df[compliance_df["status"].isin(["PASS", "FAIL"])]
                .groupby("rule_name")["status"]
                .apply(lambda x: round((x == "PASS").mean() * 100, 1))
                .sort_values()
            )
            if not rule_stats.empty:
                st.bar_chart(rule_stats)

            st.subheader("Violation Detail")
            violations = compliance_df[compliance_df["status"] == "FAIL"][
                ["conversation_id", "rule_name", "severity", "detail"]
            ].copy()
            violations["conversation_id"] = violations["conversation_id"].str[:30]
            violations["detail"] = violations["detail"].str[:120]
            if not violations.empty:
                st.dataframe(violations, use_container_width=True, hide_index=True)
            else:
                st.write("No violations found.")

        st.markdown("---")

    # ------------------------------------------------------------------
    # Row 4: agent performance (if agent names extracted)
    # ------------------------------------------------------------------
    if not analysis_df.empty and "agent_name" in analysis_df.columns:
        agents = analysis_df[analysis_df["agent_name"].notna()].copy()
        if not agents.empty:
            st.header("Agent Performance")
            agent_stats = agents.groupby("agent_name").agg(
                calls=("conversation_id", "count"),
                avg_effort=("customer_effort", "mean"),
                avg_handle=("handle_time_minutes", "mean"),
                resolved=("resolution_status", lambda x: (x == "resolved").sum()),
            ).round(1)
            agent_stats["resolution_rate"] = (
                (agent_stats["resolved"] / agent_stats["calls"]) * 100
            ).round(1)

            st.dataframe(
                agent_stats[["calls", "resolution_rate", "avg_effort", "avg_handle"]].rename(
                    columns={
                        "calls": "Total Calls",
                        "resolution_rate": "Resolution %",
                        "avg_effort": "Avg Customer Effort",
                        "avg_handle": "Avg Handle Time (min)",
                    }
                ),
                use_container_width=True,
            )
            st.markdown("---")

    # ------------------------------------------------------------------
    # Row 5: human feedback / AI accuracy
    # ------------------------------------------------------------------
    if not feedback_df.empty:
        st.header("AI Accuracy (from QA Reviews)")

        n = len(feedback_df)
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Total Reviews", n)
        f2.metric(
            "Summary Accuracy",
            f"{feedback_df['summary_accuracy'].mean():.1f} / 5",
        )
        f3.metric(
            "Sentiment Accuracy",
            f"{feedback_df['sentiment_accuracy'].mean():.1f} / 5",
        )
        f4.metric(
            "Topic Accuracy",
            f"{feedback_df['topic_accuracy'].mean():.1f} / 5",
        )

        # sentiment corrections
        corrections = feedback_df[feedback_df["corrected_sentiment"].notna()]
        if not corrections.empty:
            st.subheader("Sentiment Corrections by Reviewers")
            corr_counts = corrections["corrected_sentiment"].value_counts()
            st.bar_chart(corr_counts)

        # issue categories from reviewers vs AI topics
        if "issue_category" in feedback_df.columns:
            st.subheader("Reviewer-Assigned Issue Categories")
            cat_counts = feedback_df["issue_category"].value_counts()
            if not cat_counts.empty:
                st.bar_chart(cat_counts)

    elif FEEDBACK_DB.exists():
        st.info(
            "No feedback submitted yet. Use the QA Review app "
            "(streamlit run feedback_app.py) to rate AI outputs."
        )

    # ------------------------------------------------------------------
    # Row 6: raw data explorer
    # ------------------------------------------------------------------
    st.markdown("---")
    with st.expander("Raw data explorer"):
        tab1, tab2, tab3 = st.tabs(["Analysis", "Compliance", "Feedback"])
        with tab1:
            if not analysis_df.empty:
                st.dataframe(analysis_df, use_container_width=True, hide_index=True)
            else:
                st.write("No data.")
        with tab2:
            if not compliance_df.empty:
                st.dataframe(compliance_df, use_container_width=True, hide_index=True)
            else:
                st.write("No data.")
        with tab3:
            if not feedback_df.empty:
                st.dataframe(feedback_df, use_container_width=True, hide_index=True)
            else:
                st.write("No data.")


if __name__ == "__main__":
    main()
