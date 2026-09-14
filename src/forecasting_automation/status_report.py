"""Status report helpers: JQL query building, ADF description flattening, and RAG status computation."""

from __future__ import annotations

import datetime
from typing import Any, List, Optional, Tuple

ACCOMPLISHMENTS_LOOKBACK_DAYS = 7


def _escape_jql_string(value: str) -> str:
    """Escape backslashes and double quotes for safe JQL literal inclusion."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_accomplishments_jql(
    project_key: str,
    statuses: List[str],
    lookback_days: int = ACCOMPLISHMENTS_LOOKBACK_DAYS,
) -> Optional[str]:
    """Build JQL for recently completed / delivered work in the given statuses."""
    clean_key = project_key.strip()
    valid_statuses = [s.strip() for s in statuses if s and s.strip()]
    if not clean_key or not valid_statuses:
        return None

    status_clauses = [
        f'(status = "{_escape_jql_string(s)}" AND status CHANGED TO "{_escape_jql_string(s)}" AFTER -{lookback_days}d)'
        for s in valid_statuses
    ]

    joined_clauses = " OR ".join(status_clauses)
    return f'project = "{_escape_jql_string(clean_key)}" AND ({joined_clauses})'


def build_next_up_jql(
    project_key: str,
    statuses: List[str],
) -> Optional[str]:
    """Build JQL for in-progress / next-up work in the given statuses."""
    clean_key = project_key.strip()
    valid_statuses = [s.strip() for s in statuses if s and s.strip()]
    if not clean_key or not valid_statuses:
        return None

    status_list_str = ", ".join(f'"{_escape_jql_string(s)}"' for s in valid_statuses)
    return f'project = "{_escape_jql_string(clean_key)}" AND status IN ({status_list_str}) ORDER BY created DESC'


def _extract_adf_text(node: Any) -> str:
    """Recursively extract plain text from an Atlassian Document Format (ADF) structure."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_extract_adf_text(child) for child in node)
    if not isinstance(node, dict):
        return str(node) if node is not None else ""

    node_type = node.get("type", "")
    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"

    content = node.get("content", [])
    inner_text = "".join(_extract_adf_text(child) for child in content)

    if node_type in ("paragraph", "heading", "blockquote", "codeBlock"):
        return inner_text.strip() + "\n\n" if inner_text.strip() else ""
    if node_type == "listItem":
        return "• " + inner_text.strip() + "\n" if inner_text.strip() else ""
    if node_type in ("bulletList", "orderedList", "doc"):
        return inner_text

    return inner_text


def flatten_description(value: Any) -> str:
    """Convert Jira issue description (string, ADF dict, or None) to clean plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _extract_adf_text(value).strip()
    return str(value).strip()


def compute_rag_status(
    target_date: Optional[datetime.date],
    p50_date: Optional[datetime.date],
    p85_date: Optional[datetime.date],
) -> Tuple[str, str]:
    """Compute Green / Yellow / Red / Unknown status against the target date."""
    if target_date is None:
        return ("Unknown", "No target completion date configured in Settings.")

    if p50_date is None or p85_date is None:
        return ("Unknown", "Insufficient throughput history to calculate forecast.")

    if p85_date <= target_date:
        return (
            "Green",
            f"On track! 85th percentile forecast ({p85_date.strftime('%Y-%m-%d')}) "
            f"meets target date ({target_date.strftime('%Y-%m-%d')}).",
        )

    if p50_date <= target_date < p85_date:
        return (
            "Yellow",
            f"At risk: 50th percentile ({p50_date.strftime('%Y-%m-%d')}) meets target, "
            f"but 85th percentile ({p85_date.strftime('%Y-%m-%d')}) exceeds target ({target_date.strftime('%Y-%m-%d')}).",
        )

    return (
        "Red",
        f"Behind schedule: 50th percentile forecast ({p50_date.strftime('%Y-%m-%d')}) "
        f"exceeds target date ({target_date.strftime('%Y-%m-%d')}).",
    )
