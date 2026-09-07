"""Main command-line interface for Forecasting Automation."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

from forecasting_automation.config import AppConfig, load_config
from forecasting_automation.jira_client import JiraClient

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def setup_logging(verbose: bool = False) -> None:
    """Configure console logging level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def format_issue_table(issues: List[Dict[str, Any]]) -> str:
    """Format issues into a clean text table."""
    if not issues:
        return "No issues found matching the query."

    rows = []
    for issue in issues:
        rows.append([
            issue["key"],
            issue["issue_type"],
            issue["status"],
            issue["assignee"] or "Unassigned",
            issue["story_points"] if issue.get("story_points") is not None else "-",
            issue["summary"][:50] + ("..." if len(issue["summary"]) > 50 else ""),
        ])

    headers = ["Key", "Type", "Status", "Assignee", "Points", "Summary"]

    if HAS_TABULATE:
        return tabulate(rows, headers=headers, tablefmt="github")

    # Fallback basic tabular formatting if tabulate isn't available
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * widths[i] for i in range(len(headers)))
    data_lines = [
        " | ".join(str(val).ljust(widths[i]) for i, val in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line, sep_line] + data_lines)


def parse_args(args: List[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Forecasting Automation - Jira Cloud integration and project query tool."
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="Path to JSON configuration file (default: config.json in working directory)",
    )
    parser.add_argument(
        "-p", "--project",
        type=str,
        default=None,
        help="Override target Jira project key from config",
    )
    parser.add_argument(
        "-q", "--jql",
        type=str,
        default=None,
        help="Override or provide custom JQL query filter",
    )
    parser.add_argument(
        "-l", "--limit",
        type=int,
        default=None,
        help="Maximum number of issues to retrieve",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Optional path to save query results as JSON file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug logging",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch the interactive web UI in your default browser",
    )
    return parser.parse_args(args)



def run(config: AppConfig, output_path: str | None = None) -> int:
    """Execute Jira connection, project verification, and issue query."""
    print("=" * 60)
    print(" Forecasting Automation - Jira Project Query")
    print("=" * 60)

    # 1. Initialize client and verify authentication
    print(f"\n[1/3] Connecting to Jira at '{config.jira.server}'...")
    client = JiraClient(config.jira)

    try:
        user_info = client.verify_connection()
    except Exception as e:
        print(f"\n[ERROR] Authentication failed: {e}", file=sys.stderr)
        return 1

    print(f"      Authenticated successfully as: {user_info['user_name']} ({user_info['email']})")

    # 2. Verify project access
    target_project = config.project.key
    print(f"\n[2/3] Verifying access to project '{target_project}'...")
    try:
        proj_info = client.verify_project_access(target_project)
    except Exception as e:
        print(f"\n[ERROR] Project check failed: {e}", file=sys.stderr)
        return 1

    print(f"      Found project: {proj_info['name']} (Key: {proj_info['key']}, Lead: {proj_info['lead']})")

    # 3. Query issues
    jql_preview = client.build_jql(target_project, config.query)
    print(f"\n[3/3] Querying issues with JQL: {jql_preview}")
    limit_str = f"up to {config.query.max_results}" if config.query.max_results else "all"
    print(f"      Fetching {limit_str} issues...")


    try:
        issues = client.query_project_issues(target_project, config.query)

    except Exception as e:
        print(f"\n[ERROR] Query execution failed: {e}", file=sys.stderr)
        return 1

    print(f"\n Retrieved {len(issues)} issue(s):\n")
    print(format_issue_table(issues))

    # 4. Save to output file if requested
    if output_path:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(issues, f, indent=2, default=str)
        print(f"\n Results saved to: {out_file.resolve()}")

    print("\n Done.")
    return 0


def run_ui() -> int:
    """Launch the Streamlit Web UI and open in the default browser."""
    app_path = Path(__file__).resolve().parent / "app.py"
    try:
        from streamlit.web import cli as stcli
        sys.argv = ["streamlit", "run", str(app_path)]
        return stcli.main()
    except ImportError:
        import subprocess
        cmd = [sys.executable, "-m", "streamlit", "run", str(app_path)]
        return subprocess.call(cmd)


def main(argv: List[str] | None = None) -> int:
    """Main CLI entry point."""
    args = parse_args(argv)

    if args.ui:
        return run_ui()

    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"[ERROR] Configuration error:\n{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[ERROR] Unexpected error loading config: {e}", file=sys.stderr)
        return 1


    # Apply CLI overrides if supplied
    if args.project:
        config.project.key = args.project.strip().upper()
    if args.jql:
        config.query.custom_jql = args.jql.strip()
    if args.limit:
        config.query.max_results = args.limit

    return run(config, output_path=args.output)


if __name__ == "__main__":
    sys.exit(main())
