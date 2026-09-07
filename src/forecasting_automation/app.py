"""Streamlit Web UI for Forecasting Automation.

Provides User Settings management for config.json and an interactive
Project Status Overview displaying card counts, names, numbers, and details
per workflow status from Jira.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure src/ is on sys.path when executed directly via `streamlit run`
_src_dir = Path(__file__).resolve().parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from typing import Any, Dict, List
import datetime
import pandas as pd
import streamlit as st

from forecasting_automation.config import (
    JiraConfig,
    get_default_config_path,
    load_config,
    load_raw_config,
    save_config,
)
from forecasting_automation.forecaster import (
    add_working_days,
    extract_weekly_throughput_from_issues,
    is_done_status,
    run_simulation,
    summarize_days,
)
from forecasting_automation.jira_client import JiraClient




# Page configuration
st.set_page_config(
    page_title="Forecasting Automation - Jira Status Explorer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


THROUGHPUT_WINDOW_DAYS = 90


def get_current_raw_config() -> dict:
    """Retrieve raw config dictionary, falling back to defaults/example."""
    raw = load_raw_config()
    if not raw:
        raw = {
            "jira": {
                "server": "https://",
                "email": "",
                "api_token": "",
            },
            "project": {
                "key": "",
            },
            "query": {
                "custom_jql": "",
                "fields": [
                    "summary",
                    "status",
                    "issuetype",
                    "created",
                    "updated",
                    "resolutiondate",
                    "assignee",
                    "priority",
                ],
                "story_points_field": "customfield_10016",
            },
        }
    return raw



def render_settings_tab():
    """Render the User Settings view to manage config.json variables."""
    st.subheader("⚙️ User Settings (config.json)")
    st.caption("Manage your Jira credentials, target project, and query variables.")

    config_path = get_default_config_path()
    raw_cfg = get_current_raw_config()
    jira_dict = raw_cfg.get("jira", {})
    proj_dict = raw_cfg.get("project", {})
    query_dict = raw_cfg.get("query", {})

    with st.form("settings_form", enter_to_submit=False):
        st.markdown("#### 1. Jira Cloud Credentials")
        col_srv, col_email = st.columns(2)

        with col_srv:
            server_val = st.text_input(
                "Jira Server URL",
                value=jira_dict.get("server", "https://"),
                help="Base URL of Jira instance (e.g., https://my-company.atlassian.net)",
            )
        with col_email:
            email_val = st.text_input(
                "Atlassian Account Email",
                value=jira_dict.get("email", ""),
                help="Your Jira login email address",
            )

        api_token_val = st.text_input(
            "Atlassian API Token",
            value=jira_dict.get("api_token", ""),
            type="password",
            help="Generate at https://id.atlassian.com/manage-profile/security/api-tokens",
        )

        st.markdown("#### 2. Target Project")
        col_proj, col_points = st.columns(2)
        with col_proj:
            project_key_val = st.text_input(
                "Project Key",
                value=proj_dict.get("key", ""),
                help="Key of the Jira project to query (e.g., PROJ)",
            ).strip().upper()
        with col_points:
            story_points_val = st.text_input(
                "Story Points Field ID",
                value=query_dict.get("story_points_field", "customfield_10016"),
                help="Jira custom field ID for story points (default: customfield_10016)",
            )

        col_save, col_test = st.columns([1, 1])
        with col_save:
            submitted_save = st.form_submit_button("💾 Save Settings to config.json", use_container_width=True)
        with col_test:
            submitted_test = st.form_submit_button("🔌 Test Connection", use_container_width=True)

    if submitted_save:
        updated_dict = {
            "jira": {
                "server": server_val.strip().rstrip("/"),
                "email": email_val.strip(),
                "api_token": api_token_val.strip(),
            },
            "project": {
                "key": project_key_val.strip().upper(),
            },
            "query": {
                "custom_jql": query_dict.get("custom_jql", ""),
                "max_results": query_dict.get("max_results", None),
                "fields": query_dict.get("fields", [
                    "summary", "status", "issuetype", "created", "updated",
                    "resolutiondate", "assignee", "priority"
                ]),
                "story_points_field": story_points_val.strip() if story_points_val else None,
            },
        }
        saved_file = save_config(updated_dict, config_path)

        st.success(f"✅ Settings successfully saved to `{saved_file.name}`!")
        st.rerun()


    if submitted_test:
        test_jira_cfg = JiraConfig(
            server=server_val.strip().rstrip("/"),
            email=email_val.strip(),
            api_token=api_token_val.strip(),
        )
        client = JiraClient(test_jira_cfg)
        with st.spinner("Connecting to Jira..."):
            try:
                user_info = client.verify_connection()
                st.success(
                    f"✅ Authentication successful! Connected as **{user_info['user_name']}** ({user_info['email']}) on {user_info['server_title']}."
                )
                if project_key_val:
                    try:
                        proj_info = client.verify_project_access(project_key_val)
                        st.info(f"📁 Project found: **{proj_info['name']}** (Key: `{proj_info['key']}`, Lead: {proj_info['lead']})")
                    except Exception as pe:
                        st.warning(f"⚠️ Authenticated, but could not access project `{project_key_val}`: {pe}")
            except Exception as e:
                st.error(f"❌ Connection failed: {e}")


def render_status_overview():
    """Render the main project status dashboard with card counts, names, and numbers."""
    # 1. Attempt to load configuration
    try:
        app_config = load_config()
    except Exception as e:
        st.warning("⚠️ Configuration incomplete or missing `config.json`.")
        st.info("Please open the **Settings** tab to enter your Jira credentials and project key.")
        return

    client = JiraClient(app_config.jira)

    # Top Control Bar
    col_proj_header, col_filter, col_refresh = st.columns([2, 3, 1])
    with col_proj_header:
        st.markdown(f"### Project: `{app_config.project.key}`")
    with col_filter:
        search_query = st.text_input(
            "🔍 Search cards (by name, key, or number)",
            placeholder="Type to filter cards...",
            label_visibility="collapsed",
        )
    with col_refresh:
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # Fetch data
    with st.spinner(f"Fetching all cards from project '{app_config.project.key}'..."):

        try:
            grouped_data = client.query_issues_grouped_by_status(
                project_key=app_config.project.key,
                query_config=app_config.query,
            )
        except Exception as e:
            st.error(f"❌ Error querying Jira: {e}")
            return

    issues: List[Dict[str, Any]] = grouped_data["issues"]
    status_groups: Dict[str, Dict[str, Any]] = grouped_data["status_groups"]
    total_issues = len(issues)

    if not issues:
        st.info(f"No issues found in project `{app_config.project.key}` for the current query.")
        return

    # Calculate summary metrics
    total_points = sum(
        float(i["story_points"]) for i in issues if i.get("story_points") is not None
    )
    distinct_statuses = len([k for k, v in status_groups.items() if v["count"] > 0])

    # Display Metrics Row
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Cards", total_issues)
    m2.metric("Active Statuses", distinct_statuses)
    m3.metric("Total Story Points", f"{total_points:g}")
    m4.metric("Jira Server", app_config.jira.server.replace("https://", ""))

    st.divider()

    # Status Breakdown Distribution Chart & Summary Table
    chart_col, stat_list_col = st.columns([3, 2])

    status_summary_rows = []
    for st_name, data in status_groups.items():
        if data["count"] > 0 or not data["issues"]:
            raw_cat = data.get("category", "Undefined")
            display_cat = "Done" if is_done_status(st_name, raw_cat) else raw_cat
            status_summary_rows.append({
                "Status": st_name,
                "Category": display_cat,
                "Card Count": data["count"],
            })

    df_summary = pd.DataFrame(status_summary_rows)


    with chart_col:
        st.markdown("#### 📊 Card Count by Status")
        if not df_summary.empty:
            chart_data = df_summary.set_index("Status")[["Card Count"]]
            st.bar_chart(chart_data)

    with stat_list_col:
        st.markdown("#### 📋 Status Summary")
        if not df_summary.empty:
            st.dataframe(
                df_summary.sort_values(by="Card Count", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.markdown("### 🗂️ Cards Grouped by Status")
    st.caption("Card counts, numbers, keys, names (summaries), types, and assignees per status.")

    # Filter issues if search query is provided
    filtered_search = search_query.strip().lower()

    # Render an expander / column card for each status
    for status_name, s_data in status_groups.items():
        count = s_data["count"]
        card_list = s_data["issues"]

        if filtered_search:
            card_list = [
                c for c in card_list
                if filtered_search in str(c.get("key", "")).lower()
                or filtered_search in str(c.get("number", "")).lower()
                or filtered_search in str(c.get("summary", "")).lower()
                or filtered_search in str(c.get("assignee", "")).lower()
            ]

        # Category icon/badge
        cat = s_data.get("category", "")
        is_done = is_done_status(status_name, cat)
        cat_badge = "🟢" if is_done else ("🔵" if "progress" in cat.lower() else "⚪")

        expander_title = f"{cat_badge} **{status_name}** — ({len(card_list)} cards" + (f" of {count})" if filtered_search else ")")

        
        with st.expander(expander_title, expanded=(count > 0)):
            if not card_list:
                st.write("*No cards currently in this status.*")
                continue

            # Build detailed table for cards in this status
            card_rows = []
            for card in card_list:
                card_rows.append({
                    "Key": card["key"],
                    "Number": card.get("number"),
                    "Name / Summary": card["summary"],
                    "Type": card["issue_type"],
                    "Priority": card["priority"],
                    "Assignee": card["assignee"],
                    "Points": card["story_points"] if card["story_points"] is not None else "-",
                    "Link": card["url"],
                })

            df_cards = pd.DataFrame(card_rows)

            st.dataframe(
                df_cards,
                column_config={
                    "Link": st.column_config.LinkColumn("Jira URL", display_text="Open in Jira ↗"),
                    "Number": st.column_config.NumberColumn("Issue #", format="%d"),
                    "Name / Summary": st.column_config.TextColumn("Summary / Name", width="large"),
                },
                use_container_width=True,
                hide_index=True,
            )

    # Export utilities
    st.divider()
    exp_col1, exp_col2 = st.columns([1, 1])
    with exp_col1:
        df_all = pd.DataFrame(issues)
        csv_data = df_all.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export All Cards as CSV",
            data=csv_data,
            file_name=f"{app_config.project.key}_cards.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with exp_col2:
        json_data = json.dumps(issues, indent=2, default=str).encode("utf-8")
        st.download_button(
            label="📥 Export All Cards as JSON",
            data=json_data,
            file_name=f"{app_config.project.key}_cards.json",
            mime="application/json",
            use_container_width=True,
        )


def render_forecasting_tab():
    """Render the Monte Carlo project forecasting tab with 85th percentile prediction."""
    st.subheader("📈 Project Forecasting (Monte Carlo)")
    st.caption(
        f"Simulates project completion dates by sampling weekly throughput from the last {THROUGHPUT_WINDOW_DAYS} days, "
        "spread evenly across the 5 working days of each simulated week."
    )

    try:
        app_config = load_config()
    except Exception as e:
        st.warning("⚠️ Configuration incomplete or missing `config.json`.")
        st.info("Please open the **Settings** tab to enter your Jira credentials and project key.")
        return

    client = JiraClient(app_config.jira)

    # Fetch issues for the project
    with st.spinner(f"Loading data for project '{app_config.project.key}'..."):
        try:
            grouped_data = client.query_issues_grouped_by_status(
                project_key=app_config.project.key,
                query_config=app_config.query,
            )
        except Exception as e:
            st.error(f"❌ Error querying Jira for forecasting: {e}")
            return

    issues: List[Dict[str, Any]] = grouped_data["issues"]
    status_groups: Dict[str, Dict[str, Any]] = grouped_data["status_groups"]

    if not issues:
        st.warning(f"No issues found in project `{app_config.project.key}`.")
        return

    # Auto-calculate default backlog (cards not in Done / UAT statuses)
    non_done_count = sum(
        s_data["count"]
        for s_name, s_data in status_groups.items()
        if not is_done_status(s_name, s_data.get("category", ""))
    )
    done_count = sum(
        s_data["count"]
        for s_name, s_data in status_groups.items()
        if is_done_status(s_name, s_data.get("category", ""))
    )


    # Simulation Controls
    st.markdown("#### ⚙️ Simulation Parameters")
    ctrl_col1, ctrl_col2 = st.columns(2)

    with ctrl_col1:
        cards_left = st.number_input(
            "Cards Left (Remaining Backlog)",
            min_value=1,
            max_value=10000,
            value=max(1, non_done_count),
            help=f"Auto-detected {non_done_count} remaining non-Done cards in project.",
        )

    with ctrl_col2:
        trials = st.number_input(
            "Simulation Trials",
            min_value=100,
            max_value=10000,
            value=1000,
            step=100,
            help="Number of Monte Carlo simulation runs (default: 1,000).",
        )

    weeks_list, throughput = extract_weekly_throughput_from_issues(
        issues,
        days_window=THROUGHPUT_WINDOW_DAYS,
    )

    total_throughput = sum(throughput) if throughput else 0

    if not throughput or total_throughput == 0:
        st.error(
            f"⚠️ No cards completed in the last {THROUGHPUT_WINDOW_DAYS} days, so there is no throughput to sample.\n"
            f"Currently detected {done_count} completed cards in project overall."
        )
        return

    # Throughput Summary Stats
    total_weeks = len(throughput)
    avg_weekly = total_throughput / total_weeks if total_weeks > 0 else 0
    avg_daily = avg_weekly / 5
    zero_weeks = throughput.count(0)
    zero_pct = (zero_weeks / total_weeks) * 100 if total_weeks > 0 else 0

    # Run Monte Carlo simulation
    day_counts = run_simulation(
        weekly_throughput=throughput,
        cards_left=int(cards_left),
        trials=int(trials),
    )
    summary = summarize_days(day_counts)
    percentiles = summary["percentiles"]
    percentile_dates = summary["percentile_dates"]

    st.divider()

    # Percentile Prediction Cards
    st.markdown("### 🎯 Forecast Predictions")
    st.caption("Working days (excluding weekends) required to complete the remaining backlog.")

    p_col1, p_col2, p_col3 = st.columns(3)

    with p_col1:
        st.info(
            f"### 🪙 50th Percentile (Coin-flip)\n"
            f"**Finish Date:** `{percentile_dates[50].strftime('%A, %b %d, %Y')}`\n\n"
            f"**Working Days:** `{percentiles[50]}` days (50% likelihood)"
        )

    with p_col2:
        st.success(
            f"### 🎯 85th Percentile (Planning Target)\n"
            f"**Finish Date:** `{percentile_dates[85].strftime('%A, %b %d, %Y')}`\n\n"
            f"**Working Days:** `{percentiles[85]}` days (85% confidence)"
        )

    with p_col3:
        st.warning(
            f"### 🛡️ 95th Percentile (Pessimistic)\n"
            f"**Finish Date:** `{percentile_dates[95].strftime('%A, %b %d, %Y')}`\n\n"
            f"**Working Days:** `{percentiles[95]}` days (95% confidence)"
        )

    st.divider()

    # Completion Date Distribution Chart with P85 Highlight
    st.markdown("### 📊 Completion Date Distribution")
    st.caption(
        f"Histogram of {trials:,} simulations. The highlighted **P85 Target Date** is **{percentile_dates[85].strftime('%Y-%m-%d')}** ({percentiles[85]} working days)."
    )

    histogram_data = summary["histogram"]
    p85_date_str = percentile_dates[85].strftime("%Y-%m-%d")

    chart_rows = []
    for h in histogram_data:
        d_str = h["finish_date"].strftime("%Y-%m-%d")
        is_p85 = (h["days"] == percentiles[85])
        chart_rows.append({
            "Finish Date": d_str,
            "Working Days": h["days"],
            "Simulations": h["count"],
            "Cumulative Likelihood (%)": h["cumulative_pct"],
            "P85 Target": "🎯 85th Percentile" if is_p85 else "Standard",
        })

    df_chart = pd.DataFrame(chart_rows)

    # Primary simulation distribution bar chart
    if not df_chart.empty:
        chart_series = df_chart.set_index("Finish Date")[["Simulations"]]
        st.bar_chart(chart_series, height=350)

    # Historical Throughput & Simulation Details Expanders
    exp_hist, exp_sim = st.columns(2)

    with exp_hist:
        with st.expander("ℹ️ Historical Throughput Baseline", expanded=False):
            st.write(f"- **Sampled Weeks:** {total_weeks} full weeks (last {THROUGHPUT_WINDOW_DAYS} days)")
            st.write(f"- **Total Cards Completed:** {total_throughput} cards")
            st.write(f"- **Average Throughput:** {avg_weekly:.2f} cards / week ({avg_daily:.2f} / working day)")
            st.write(f"- **Zero-Progress Weeks:** {zero_weeks} ({zero_pct:.1f}%)")

            df_tp = pd.DataFrame({
                "Week Starting": [d.strftime("%Y-%m-%d") for d in weeks_list],
                "Cards Done": throughput,
            })
            st.dataframe(df_tp.sort_values(by="Week Starting", ascending=False), height=200, hide_index=True)

    with exp_sim:
        with st.expander("📋 Simulation Frequency & Cumulative Probability Table", expanded=False):
            st.dataframe(
                df_chart[["Finish Date", "Working Days", "Simulations", "Cumulative Likelihood (%)"]],
                height=250,
                hide_index=True,
            )


def main():
    """Main Streamlit app entry point."""
    st.title("📊 Forecasting Automation")

    tab_overview, tab_forecast, tab_settings = st.tabs([
        "📋 Project Status Breakdown",
        "📈 Forecasting (Monte Carlo)",
        "⚙️ User Settings",
    ])

    with tab_overview:
        render_status_overview()

    with tab_forecast:
        render_forecasting_tab()

    with tab_settings:
        render_settings_tab()



if __name__ == "__main__":
    main()
