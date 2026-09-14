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
import streamlit.components.v1 as components

from forecasting_automation.config import (
    JiraConfig,
    get_default_config_path,
    load_config,
    load_raw_config,
    save_config,
)
from forecasting_automation.forecaster import (
    STATUS_BUCKETS,
    add_working_days,
    categorize_status,
    extract_weekly_throughput_from_issues,
    is_done_status,
    run_simulation,
    summarize_days,
)
from forecasting_automation.jira_client import JiraClient
from forecasting_automation.status_report import (
    ACCOMPLISHMENTS_LOOKBACK_DAYS,
    build_accomplishments_jql,
    build_next_up_jql,
    compute_rag_status,
    flatten_description,
)




# Page configuration
st.set_page_config(
    page_title="Forecasting Automation - Jira Status Explorer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


DEFAULT_THROUGHPUT_WEEKS = 12  # ~90 days


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
                "target_date": "",
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
            "status_report": {
                "accomplishment_statuses": [
                    "Ready for Testing",
                    "Merge Requested",
                ],
                "next_up_statuses": [
                    "In Development",
                    "DEV In Progress",
                ],
            },
            "status_mapping": {},
        }
    return raw



def render_settings_tab():
    """Render the User Settings view to manage config.json variables."""
    st.subheader("⚙️ User Settings (config.json)")
    st.caption("Manage your Jira credentials, target project, status mapping, and query variables.")

    config_path = get_default_config_path()
    raw_cfg = get_current_raw_config()
    jira_dict = raw_cfg.get("jira", {})
    proj_dict = raw_cfg.get("project", {})
    query_dict = raw_cfg.get("query", {})
    saved_mapping: Dict[str, str] = raw_cfg.get("status_mapping", {})

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
        col_proj, col_date, col_points = st.columns(3)
        with col_proj:
            project_key_val = st.text_input(
                "Project Key",
                value=proj_dict.get("key", ""),
                help="Key of the Jira project to query (e.g., PROJ)",
            ).strip().upper()
        with col_date:
            saved_target_date_raw = proj_dict.get("target_date")
            saved_target_date = None
            if saved_target_date_raw:
                try:
                    if isinstance(saved_target_date_raw, datetime.date):
                        saved_target_date = saved_target_date_raw
                    else:
                        saved_target_date = datetime.date.fromisoformat(str(saved_target_date_raw).strip())
                except Exception:
                    saved_target_date = None
            target_date_val = st.date_input(
                "Project Target Date",
                value=saved_target_date,
                help="Target completion date for RAG status indicator in the Status Report tab",
            )
        with col_points:
            story_points_val = st.text_input(
                "Story Points Field ID",
                value=query_dict.get("story_points_field", "customfield_10016"),
                help="Jira custom field ID for story points (default: customfield_10016)",
            )

        st.markdown("#### 3. Status Mapping (To Do / In Progress / Done / Exclude)")
        st.caption(
            "Map statuses returned from Jira to forecasting buckets. 'Exclude' statuses will be completely ignored in forecasting."
        )

        # Merge discovered statuses from session state with saved mapping
        discovered: Dict[str, Dict[str, Any]] = st.session_state.get("discovered_statuses", {})
        all_status_names = list(discovered.keys())
        for sm_key in saved_mapping.keys():
            if sm_key not in all_status_names:
                all_status_names.append(sm_key)

        updated_mapping_inputs = {}
        if all_status_names:
            sorted_statuses = sorted(all_status_names)
            for i in range(0, len(sorted_statuses), 2):
                col_left, col_right = st.columns(2)
                for col, s_name in zip([col_left, col_right], sorted_statuses[i:i + 2]):
                    with col:
                        meta = discovered.get(s_name, {})
                        jira_cat = meta.get("category", "")
                        card_count = meta.get("count", None)

                        # Default selection: configured value -> fallback categorization
                        effective_val = categorize_status(s_name, jira_cat, saved_mapping)
                        default_idx = STATUS_BUCKETS.index(effective_val) if effective_val in STATUS_BUCKETS else 0

                        count_label = f" ({card_count} cards)" if card_count is not None else ""
                        cat_hint = f" [Jira: {jira_cat}]" if jira_cat else ""

                        selected_bucket = st.selectbox(
                            f"**{s_name}**{count_label}{cat_hint}",
                            options=STATUS_BUCKETS,
                            index=default_idx,
                            key=f"status_map_{s_name}",
                        )
                        updated_mapping_inputs[s_name] = selected_bucket
        else:
            st.info(
                "ℹ️ No statuses discovered yet. Open the **Project Status Breakdown** tab once to load statuses from Jira, or configure them manually."
            )

        st.markdown("#### 4. Status Report Statuses")
        st.caption(
            "Select workflow statuses to include in the Accomplishments and Next Up sections of the Status Report tab."
        )

        report_dict = raw_cfg.get("status_report", {})
        saved_accomplishments = report_dict.get(
            "accomplishment_statuses", ["Ready for Testing", "Merge Requested"]
        )
        if isinstance(saved_accomplishments, str):
            saved_accomplishments = [s.strip() for s in saved_accomplishments.split(",") if s.strip()]
        saved_next_up = report_dict.get(
            "next_up_statuses", ["In Development", "DEV In Progress"]
        )
        if isinstance(saved_next_up, str):
            saved_next_up = [s.strip() for s in saved_next_up.split(",") if s.strip()]

        col_rep1, col_rep2 = st.columns(2)
        if all_status_names:
            opt_accomplishments = sorted(list(set(all_status_names + saved_accomplishments)))
            opt_next_up = sorted(list(set(all_status_names + saved_next_up)))
            with col_rep1:
                selected_accomplishments = st.multiselect(
                    "Accomplishments Statuses (Last 7 Days)",
                    options=opt_accomplishments,
                    default=[s for s in saved_accomplishments if s in opt_accomplishments],
                    help="Cards transitioned into these statuses in the past 7 days are listed under Accomplishments.",
                )
            with col_rep2:
                selected_next_up = st.multiselect(
                    "Next Up Statuses (Active Development)",
                    options=opt_next_up,
                    default=[s for s in saved_next_up if s in opt_next_up],
                    help="Cards currently in these statuses are listed under Next Up.",
                )
        else:
            with col_rep1:
                txt_acc = st.text_input(
                    "Accomplishments Statuses (comma-separated)",
                    value=", ".join(saved_accomplishments),
                    help="Comma-separated status names (e.g. Ready for Testing, Merge Requested)",
                )
                selected_accomplishments = [s.strip() for s in txt_acc.split(",") if s.strip()]
            with col_rep2:
                txt_nxt = st.text_input(
                    "Next Up Statuses (comma-separated)",
                    value=", ".join(saved_next_up),
                    help="Comma-separated status names (e.g. In Development, DEV In Progress)",
                )
                selected_next_up = [s.strip() for s in txt_nxt.split(",") if s.strip()]

        col_save, col_reset, col_test = st.columns([1, 1, 1])
        with col_save:
            submitted_save = st.form_submit_button("💾 Save Settings to config.json", use_container_width=True)
        with col_reset:
            submitted_reset = st.form_submit_button("🔄 Reset Mapping to Jira Defaults", use_container_width=True)
        with col_test:
            submitted_test = st.form_submit_button("🔌 Test Connection", use_container_width=True)

    if submitted_save:
        target_date_str = target_date_val.strftime("%Y-%m-%d") if target_date_val else ""
        updated_dict = {
            "jira": {
                "server": server_val.strip().rstrip("/"),
                "email": email_val.strip(),
                "api_token": api_token_val.strip(),
            },
            "project": {
                "key": project_key_val.strip().upper(),
                "target_date": target_date_str,
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
            "status_report": {
                "accomplishment_statuses": selected_accomplishments,
                "next_up_statuses": selected_next_up,
            },
            "status_mapping": updated_mapping_inputs,
        }
        saved_file = save_config(updated_dict, config_path)

        st.success(f"✅ Settings successfully saved to `{saved_file.name}`!")
        st.rerun()

    if submitted_reset:
        target_date_str = target_date_val.strftime("%Y-%m-%d") if target_date_val else ""
        updated_dict = {
            "jira": {
                "server": server_val.strip().rstrip("/"),
                "email": email_val.strip(),
                "api_token": api_token_val.strip(),
            },
            "project": {
                "key": project_key_val.strip().upper(),
                "target_date": target_date_str,
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
            "status_report": {
                "accomplishment_statuses": selected_accomplishments,
                "next_up_statuses": selected_next_up,
            },
            "status_mapping": {},
        }
        saved_file = save_config(updated_dict, config_path)
        st.success(f"✅ Status mapping reset to Jira defaults in `{saved_file.name}`!")
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

    client = JiraClient(app_config.jira, app_config.status_mapping)

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

    # Discover and cache statuses in session state for the settings page
    discovered: Dict[str, Dict[str, Any]] = {}
    for st_name, s_data in status_groups.items():
        discovered[st_name] = {
            "category": s_data.get("category", "Undefined"),
            "count": s_data.get("count", 0),
        }
    st.session_state["discovered_statuses"] = discovered

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
            display_cat = categorize_status(st_name, raw_cat, app_config.status_mapping)
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
        resolved_bucket = categorize_status(status_name, cat, app_config.status_mapping)
        if resolved_bucket == "Done":
            cat_badge = "🟢"
        elif resolved_bucket == "In Progress":
            cat_badge = "🔵"
        elif resolved_bucket == "Exclude":
            cat_badge = "🚫"
        else:
            cat_badge = "⚪"

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
        "Simulates project completion dates by sampling weekly throughput from a recent history window, "
        "spread evenly across the 5 working days of each simulated week."
    )

    # Cleared up front so an early return never leaves the Status Report tab showing stale results.
    st.session_state["forecast_results"] = None

    try:
        app_config = load_config()
    except Exception as e:
        st.warning("⚠️ Configuration incomplete or missing `config.json`.")
        st.info("Please open the **Settings** tab to enter your Jira credentials and project key.")
        return

    client = JiraClient(app_config.jira, app_config.status_mapping)

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

    # Auto-calculate default backlog (cards in To Do / In Progress, ignoring Exclude and Done)
    active_backlog_count = sum(
        s_data["count"]
        for s_name, s_data in status_groups.items()
        if categorize_status(s_name, s_data.get("category", ""), app_config.status_mapping) in ("To Do", "In Progress")
    )
    done_count = sum(
        s_data["count"]
        for s_name, s_data in status_groups.items()
        if is_done_status(s_name, s_data.get("category", ""), app_config.status_mapping)
    )
    excluded_count = sum(
        s_data["count"]
        for s_name, s_data in status_groups.items()
        if categorize_status(s_name, s_data.get("category", ""), app_config.status_mapping) == "Exclude"
    )

    # Simulation Controls
    st.markdown("#### ⚙️ Simulation Parameters")
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns(3)

    with ctrl_col1:
        cards_left = st.number_input(
            "Cards Left (Remaining Backlog)",
            min_value=1,
            max_value=10000,
            value=max(1, active_backlog_count),
            help=f"Auto-detected {active_backlog_count} remaining active cards in project (excluding {excluded_count} Excluded and {done_count} Done cards).",
            key="sim_cards_left",
        )

    with ctrl_col2:
        trials = st.number_input(
            "Simulation Trials",
            min_value=100,
            max_value=10000,
            value=1000,
            step=100,
            help="Number of Monte Carlo simulation runs (default: 1,000).",
            key="sim_trials",
        )

    with ctrl_col3:
        history_weeks = st.number_input(
            "History Window (Weeks)",
            min_value=1,
            max_value=104,
            value=DEFAULT_THROUGHPUT_WEEKS,
            help="Weeks of completed-work history to sample throughput from (default: 12, roughly 90 days).",
            key="sim_history_weeks",
        )

    weeks_list, throughput = extract_weekly_throughput_from_issues(
        issues,
        weeks_window=int(history_weeks),
        status_mapping=app_config.status_mapping,
    )

    total_throughput = sum(throughput) if throughput else 0

    if not throughput or total_throughput == 0:
        st.error(
            f"⚠️ No cards completed in the last {history_weeks} weeks, so there is no throughput to sample.\n"
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

    # Shared with the Status Report tab so both views reflect the same simulation run.
    st.session_state["forecast_results"] = {
        "p50_date": percentile_dates.get(50),
        "p85_date": percentile_dates.get(85),
        "cards_left": int(cards_left),
        "trials": int(trials),
        "history_weeks": int(history_weeks),
    }

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
        f"Histogram of {trials:,} simulations. The **P85 Target Date** is **{percentile_dates[85].strftime('%Y-%m-%d')}** ({percentiles[85]} working days)."
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
            st.write(f"- **Sampled Weeks:** {total_weeks} weeks (last {history_weeks} weeks requested, including the current week to date)")
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


def _copy_to_clipboard_button(text: str):
    """Render a right-aligned copy icon button that copies the given text to the clipboard."""
    # Escaping '<' prevents Jira content from breaking out of the inline <script> block.
    payload = json.dumps(text).replace("<", "\\u003c")
    components.html(
        f"""
        <div class="wrap">
          <button id="copy-btn" title="Copy table to clipboard" aria-label="Copy table to clipboard">
            <svg id="icon-copy" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>
            </svg>
            <svg id="icon-done" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" style="display:none">
              <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
            </svg>
          </button>
        </div>
        <style>
          .wrap {{ display: flex; justify-content: flex-end; margin-bottom: 1rem; }}
          #copy-btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            width: 30px;
            height: 30px;
            padding: 0;
            border: 1px solid #ffffff;
            border-radius: 0.5rem;
            background-color: transparent;
            color: #ffffff;
            cursor: pointer;
          }}
          #copy-btn:hover {{ border-color: #ff4b4b; color: #ff4b4b; }}
        </style>
        <script>
          const payload = {payload};
          const btn = document.getElementById("copy-btn");
          const iconCopy = document.getElementById("icon-copy");
          const iconDone = document.getElementById("icon-done");
          btn.addEventListener("click", () => {{
            const done = () => {{
              iconCopy.style.display = "none";
              iconDone.style.display = "block";
              setTimeout(() => {{
                iconDone.style.display = "none";
                iconCopy.style.display = "block";
              }}, 1500);
            }};
            const fallback = () => {{
              const ta = document.createElement("textarea");
              ta.value = payload;
              ta.style.position = "fixed";
              ta.style.opacity = "0";
              document.body.appendChild(ta);
              ta.select();
              document.execCommand("copy");
              document.body.removeChild(ta);
              done();
            }};
            if (navigator.clipboard && window.isSecureContext) {{
              navigator.clipboard.writeText(payload).then(done).catch(fallback);
            }} else {{
              fallback();
            }}
          }});
        </script>
        """,
        height=50,
    )


def _render_card_table(cards: List[Dict[str, Any]]):
    """Render Jira cards as a uniform table of title and description."""
    rows = []
    for card in cards:
        desc_text = flatten_description(card.get("description"))
        rows.append({
            "Title": f"{card.get('key', 'N/A')} — {card.get('summary', 'No Summary')}",
            "Description": " ".join(desc_text.split()) if desc_text else "—",
        })

    # Tab-separated so it pastes cleanly into Excel or email.
    lines = ["Title\tDescription"]
    lines.extend(f"{r['Title']}\t{r['Description']}" for r in rows)
    _copy_to_clipboard_button("\n".join(lines))

    st.dataframe(
        pd.DataFrame(rows),
        column_config={
            "Title": st.column_config.TextColumn("Title", width="medium"),
            "Description": st.column_config.TextColumn("Description", width="large"),
        },
        use_container_width=True,
        hide_index=True,
    )


def render_status_report_tab():
    """Render the Status Report tab with RAG health indicator, Accomplishments, and Next Up."""
    st.subheader("📣 Project Status Report")
    st.caption("Overview of target completion health, recent achievements, and active development items.")

    # 1. Load configuration
    try:
        app_config = load_config()
    except Exception as e:
        st.warning("⚠️ Configuration incomplete or missing `config.json`.")
        st.info("Please open the **Settings** tab to enter your Jira credentials and project key.")
        return

    client = JiraClient(app_config.jira, app_config.status_mapping)

    # Top Control Bar
    col_proj_header, col_spacer, col_refresh = st.columns([3, 2, 1])
    with col_proj_header:
        st.markdown(f"### Project: `{app_config.project.key}`")
    with col_refresh:
        if st.button("🔄 Refresh Report", use_container_width=True, key="btn_refresh_status_report"):
            st.cache_data.clear()
            st.rerun()

    # --- Section 1: Project Status (RAG) ---
    st.markdown("#### 🎯 Project Health & Forecast")

    with st.spinner(f"Calculating forecast for project '{app_config.project.key}'..."):
        try:
            grouped_data = client.query_issues_grouped_by_status(
                project_key=app_config.project.key,
                query_config=app_config.query,
            )
        except Exception as e:
            st.error(f"❌ Error querying Jira for project health: {e}")
            return

    issues: List[Dict[str, Any]] = grouped_data["issues"]
    status_groups: Dict[str, Dict[str, Any]] = grouped_data["status_groups"]

    active_backlog_count = sum(
        s_data["count"]
        for s_name, s_data in status_groups.items()
        if categorize_status(s_name, s_data.get("category", ""), app_config.status_mapping) in ("To Do", "In Progress")
    )

    # Read the widget value directly so this tab matches the Forecasting tab regardless of render order.
    history_weeks = st.session_state.get("sim_history_weeks", DEFAULT_THROUGHPUT_WEEKS)

    weeks_list, throughput = extract_weekly_throughput_from_issues(
        issues,
        weeks_window=int(history_weeks),
        status_mapping=app_config.status_mapping,
    )

    shared_forecast = st.session_state.get("forecast_results")
    if shared_forecast:
        p50_date = shared_forecast.get("p50_date")
        p85_date = shared_forecast.get("p85_date")
        cards_left = shared_forecast.get("cards_left", active_backlog_count)
        trials = shared_forecast.get("trials", 1000)
    else:
        p50_date = None
        p85_date = None
        cards_left = active_backlog_count
        trials = 1000
        if throughput and sum(throughput) > 0 and cards_left > 0:
            day_counts = run_simulation(
                weekly_throughput=throughput,
                cards_left=int(cards_left),
                trials=int(trials),
            )
            summary = summarize_days(day_counts)
            p50_date = summary.get("percentile_dates", {}).get(50)
            p85_date = summary.get("percentile_dates", {}).get(85)

    target_date = app_config.project.target_date
    rag_status, rag_msg = compute_rag_status(target_date, p50_date, p85_date)

    if rag_status == "Green":
        st.success(f"🟢 **STATUS: GREEN** — {rag_msg}")
    elif rag_status == "Yellow":
        st.warning(f"🟡 **STATUS: YELLOW** — {rag_msg}")
    elif rag_status == "Red":
        st.error(f"🔴 **STATUS: RED** — {rag_msg}")
    else:
        st.info(f"⚪ **STATUS: {rag_status.upper()}** — {rag_msg}")

    # Metrics row
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Target Date", target_date.strftime("%Y-%m-%d") if target_date else "Not Configured")
    r2.metric("50th Percentile Finish", p50_date.strftime("%Y-%m-%d") if p50_date else "N/A")
    r3.metric("85th Percentile Finish", p85_date.strftime("%Y-%m-%d") if p85_date else "N/A")
    r4.metric("Cards Left (Backlog)", cards_left)
    st.caption(
        f"Based on {trials:,} simulation trials over a {history_weeks}-week throughput history. "
        "Adjust **Cards Left**, **Simulation Trials**, or **History Window** on the Forecasting tab to update these figures."
    )

    st.divider()

    # --- Section 2: Accomplishments ---
    st.markdown("#### ✅ Accomplishments (Last 7 Days)")
    acc_statuses = app_config.status_report.accomplishment_statuses
    acc_jql = build_accomplishments_jql(app_config.project.key, acc_statuses)

    if not acc_jql:
        st.info("ℹ️ No accomplishment statuses configured. Set them in the **Settings** tab.")
    else:
        with st.expander("🔍 View Accomplishments JQL", expanded=False):
            st.code(acc_jql, language="sql")

        with st.spinner("Fetching accomplishments..."):
            try:
                acc_issues = client.query_by_jql(acc_jql)
            except Exception as e:
                st.error(f"❌ Error querying accomplishments: {e}")
                acc_issues = None

        if acc_issues is not None:
            if not acc_issues:
                st.info(f"No cards transitioned into {', '.join(acc_statuses)} in the last 7 days.")
            else:
                st.caption(f"Found **{len(acc_issues)}** card(s):")
                _render_card_table(acc_issues)

    st.divider()

    # --- Section 3: Next Up ---
    st.markdown("#### 🔜 Next Up (Active Development)")
    next_statuses = app_config.status_report.next_up_statuses
    next_jql = build_next_up_jql(app_config.project.key, next_statuses)

    if not next_jql:
        st.info("ℹ️ No next-up statuses configured. Set them in the **Settings** tab.")
    else:
        with st.expander("🔍 View Next Up JQL", expanded=False):
            st.code(next_jql, language="sql")

        with st.spinner("Fetching next up cards..."):
            try:
                next_issues = client.query_by_jql(next_jql)
            except Exception as e:
                st.error(f"❌ Error querying next up cards: {e}")
                next_issues = None

        if next_issues is not None:
            if not next_issues:
                st.info(f"No cards found with status in {', '.join(next_statuses)}.")
            else:
                st.caption(f"Found **{len(next_issues)}** active card(s):")
                _render_card_table(next_issues)


def main():
    """Main Streamlit app entry point."""
    st.title("📊 Forecasting Automation")

    tab_overview, tab_forecast, tab_report, tab_settings = st.tabs([
        "📋 Project Status Breakdown",
        "📈 Forecasting (Monte Carlo)",
        "📣 Status Report",
        "⚙️ User Settings",
    ])

    with tab_overview:
        render_status_overview()

    with tab_forecast:
        render_forecasting_tab()

    with tab_report:
        render_status_report_tab()

    with tab_settings:
        render_settings_tab()



if __name__ == "__main__":
    main()
