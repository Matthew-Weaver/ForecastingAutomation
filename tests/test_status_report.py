"""Unit tests for Status Report JQL generation, ADF description parsing, and RAG status computation."""

import datetime
from forecasting_automation.status_report import (
    build_accomplishments_jql,
    build_next_up_jql,
    compute_rag_status,
    flatten_description,
)


def test_build_accomplishments_jql():
    # Multiple statuses
    jql = build_accomplishments_jql("HOUSEINTRA", ["Ready for Testing", "Merge Requested"])
    expected = (
        'project = "HOUSEINTRA" AND '
        '((status = "Ready for Testing" AND status CHANGED TO "Ready for Testing" AFTER -7d) OR '
        '(status = "Merge Requested" AND status CHANGED TO "Merge Requested" AFTER -7d))'
    )
    assert jql == expected

    # Single status
    jql_single = build_accomplishments_jql("PROJ", ["Done"])
    assert jql_single == 'project = "PROJ" AND ((status = "Done" AND status CHANGED TO "Done" AFTER -7d))'

    # Custom lookback
    jql_lookback = build_accomplishments_jql("PROJ", ["Done"], lookback_days=14)
    assert jql_lookback == 'project = "PROJ" AND ((status = "Done" AND status CHANGED TO "Done" AFTER -14d))'

    # Empty statuses or project
    assert build_accomplishments_jql("PROJ", []) is None
    assert build_accomplishments_jql("   ", ["Done"]) is None

    # Escaping quotes
    jql_escaped = build_accomplishments_jql("PROJ", ['QA "Verified"'])
    assert jql_escaped == 'project = "PROJ" AND ((status = "QA \\"Verified\\"" AND status CHANGED TO "QA \\"Verified\\"" AFTER -7d))'


def test_build_next_up_jql():
    jql = build_next_up_jql("HOUSEINTRA", ["In Development", "DEV In Progress"])
    expected = 'project = "HOUSEINTRA" AND status IN ("In Development", "DEV In Progress") ORDER BY created DESC'
    assert jql == expected

    # Empty statuses or project
    assert build_next_up_jql("PROJ", []) is None
    assert build_next_up_jql("   ", ["In Progress"]) is None

    # Escaping quotes
    jql_escaped = build_next_up_jql("PROJ", ['Status "A"', 'Status "B"'])
    assert jql_escaped == 'project = "PROJ" AND status IN ("Status \\"A\\"", "Status \\"B\\"") ORDER BY created DESC'


def test_flatten_description():
    # None
    assert flatten_description(None) == ""

    # Plain text
    assert flatten_description(" Simple description ") == "Simple description"

    # ADF dictionary structure
    adf_data = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Overview"}],
            },
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "First line of text."},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "Second line on same paragraph."},
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Bullet item 1"}],
                            }
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Bullet item 2"}],
                            }
                        ],
                    },
                ],
            },
        ],
    }

    result = flatten_description(adf_data)
    assert "Overview" in result
    assert "First line of text.\nSecond line on same paragraph." in result
    assert "• Bullet item 1" in result
    assert "• Bullet item 2" in result


def test_compute_rag_status():
    target = datetime.date(2026, 6, 30)

    # Missing target
    status, msg = compute_rag_status(None, datetime.date(2026, 5, 1), datetime.date(2026, 5, 15))
    assert status == "Unknown"
    assert "No target completion date" in msg

    # Missing forecast
    status, msg = compute_rag_status(target, None, None)
    assert status == "Unknown"
    assert "Insufficient throughput" in msg

    # Green: P85 <= target
    status_green, msg_green = compute_rag_status(
        target,
        p50_date=datetime.date(2026, 5, 1),
        p85_date=datetime.date(2026, 6, 20),
    )
    assert status_green == "Green"
    assert "On track" in msg_green

    # Green on exact target date equality
    status_green_eq, _ = compute_rag_status(
        target,
        p50_date=datetime.date(2026, 5, 1),
        p85_date=datetime.date(2026, 6, 30),
    )
    assert status_green_eq == "Green"

    # Yellow: P50 <= target < P85
    status_yellow, msg_yellow = compute_rag_status(
        target,
        p50_date=datetime.date(2026, 6, 15),
        p85_date=datetime.date(2026, 7, 10),
    )
    assert status_yellow == "Yellow"
    assert "At risk" in msg_yellow

    # Red: P50 > target
    status_red, msg_red = compute_rag_status(
        target,
        p50_date=datetime.date(2026, 7, 1),
        p85_date=datetime.date(2026, 7, 20),
    )
    assert status_red == "Red"
    assert "Behind schedule" in msg_red
