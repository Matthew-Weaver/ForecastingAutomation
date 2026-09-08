"""Unit tests for Monte Carlo forecasting algorithm."""

import datetime
from forecasting_automation.forecaster import (
    add_working_days,
    extract_weekly_throughput_from_issues,
    run_simulation,
    simulate_once,
    summarize_days,
)


def test_add_working_days_standard():
    # 2026-09-07 is Monday
    start = datetime.date(2026, 9, 7)
    # Adding 1 working day -> Tuesday 2026-09-08
    assert add_working_days(start, 1) == datetime.date(2026, 9, 8)
    # Adding 5 working days -> next Monday 2026-09-14
    assert add_working_days(start, 5) == datetime.date(2026, 9, 14)


def test_add_working_days_over_weekend():
    # 2026-09-11 is Friday
    friday = datetime.date(2026, 9, 11)
    # Adding 1 working day should jump to Monday 2026-09-14
    assert add_working_days(friday, 1) == datetime.date(2026, 9, 14)


def test_simulate_once_deterministic():
    # 10 cards/week spread over 5 working days = 2/day, so 6 cards takes 3 days
    days = simulate_once(cards_left=6, weekly_throughput=[10])
    assert days == 3


def test_simulate_once_zero_throughput_cap():
    days = simulate_once(cards_left=5, weekly_throughput=[0], day_cap=50)
    assert days == 50


def test_simulate_once_redraws_weekly_rate_every_five_days():
    draws = iter([0.0, 0.9])
    rng = lambda: next(draws)

    # First week draws 0 cards/week, second draws 10 (2/day) to clear 10 cards
    days = simulate_once(cards_left=10, weekly_throughput=[0, 10], rng=rng)

    assert days == 10


def test_run_simulation_and_summarize():
    weekly_throughput = [5, 10, 0, 15, 5]
    trials = 500
    cards_left = 20
    start_date = datetime.date(2026, 9, 7)

    day_counts = run_simulation(weekly_throughput, cards_left, trials=trials)
    assert len(day_counts) == trials

    summary = summarize_days(day_counts, start_date=start_date)
    percentiles = summary["percentiles"]
    dates = summary["percentile_dates"]

    assert 50 in percentiles
    assert 85 in percentiles
    assert 95 in percentiles

    # P50 <= P85 <= P95
    assert percentiles[50] <= percentiles[85] <= percentiles[95]
    assert dates[50] <= dates[85] <= dates[95]


def test_categorize_status_with_mapping():
    from forecasting_automation.forecaster import categorize_status

    mapping = {
        "On UAT": "Done",
        "Code Review": "In Progress",
        "Parked": "To Do",
        "Won't Fix": "Exclude",
    }
    # Direct mapping
    assert categorize_status("On UAT", "In Progress", mapping) == "Done"
    # Case-insensitive mapping lookup
    assert categorize_status("on uat", "In Progress", mapping) == "Done"
    assert categorize_status("Code Review", "To Do", mapping) == "In Progress"
    assert categorize_status("Parked", "Done", mapping) == "To Do"
    assert categorize_status("Won't Fix", "Done", mapping) == "Exclude"
    assert categorize_status("won't fix", "In Progress", mapping) == "Exclude"


def test_categorize_status_jira_category_fallback():
    from forecasting_automation.forecaster import categorize_status

    # When not in mapping, falls back to Jira category
    assert categorize_status("Closed", "Done") == "Done"
    assert categorize_status("Under Review", "In Progress") == "In Progress"
    assert categorize_status("Backlog", "To Do") == "To Do"
    assert categorize_status("New", "New") == "To Do"


def test_categorize_status_generic_name_fallback():
    from forecasting_automation.forecaster import categorize_status

    assert categorize_status("Done", None) == "Done"
    assert categorize_status("In Progress", None) == "In Progress"
    assert categorize_status("To Do", None) == "To Do"
    assert categorize_status("Exclude", None) == "Exclude"
    assert categorize_status("Unknown Status", None) == "To Do"


def test_is_done_status():
    from forecasting_automation.forecaster import is_done_status

    assert is_done_status("Done") is True
    assert is_done_status("done") is True
    assert is_done_status("Some Custom Status", "Done") is True
    assert is_done_status("In Progress", "In Progress") is False
    assert is_done_status("To Do", "To Do") is False
    assert is_done_status("Won't Fix", "Done", {"Won't Fix": "Exclude"}) is False

    # Without mapping, On UAT with In Progress category is not Done
    assert is_done_status("On UAT", "In Progress") is False
    # With mapping, On UAT is Done
    assert is_done_status("On UAT", "In Progress", {"On UAT": "Done"}) is True


def test_extract_weekly_throughput_from_issues():
    issues = [
        {"status": "Done", "status_category": "Done", "resolution_date": "2026-09-01T10:00:00.000+0000"},  # Tue, week of Aug 31
        {"status": "On UAT", "status_category": "In Progress", "updated": "2026-09-01T15:00:00.000+0000"},  # Tue, week of Aug 31
        {"status": "Migrate to UAT", "status_category": "In Progress", "resolution_date": "2026-09-02T11:00:00.000+0000"},  # Wed, week of Aug 31
        {"status": "In Progress", "status_category": "In Progress", "resolution_date": None},  # Not done
    ]

    weeks, throughput = extract_weekly_throughput_from_issues(
        issues,
        end_date=datetime.date(2026, 9, 18),  # Friday; last full week starts Sept 7
        status_mapping={"On UAT": "Done", "Migrate to UAT": "Done"},
    )

    assert weeks == [datetime.date(2026, 8, 31), datetime.date(2026, 9, 7)]
    assert throughput == [3, 0]


def test_extract_weekly_throughput_excludes_partial_current_week():
    issues = [
        {"status": "Done", "status_category": "Done", "completed_date": "2026-09-15T10:00:00.000+0000"},
    ]

    # Sept 15 falls in the week of Sept 14, which is still in progress on Sept 18
    weeks, throughput = extract_weekly_throughput_from_issues(
        issues,
        end_date=datetime.date(2026, 9, 18),
    )

    assert datetime.date(2026, 9, 14) not in weeks


def test_extract_weekly_throughput_prefers_completed_date():
    issues = [
        {
            "status": "On UAT",
            "status_category": "In Progress",
            "completed_date": "2026-09-01T10:00:00.000+0000",  # week of Aug 31
            "updated": "2026-09-08T10:00:00.000+0000",  # week of Sept 7, a later unrelated edit
        },
    ]

    weeks, throughput = extract_weekly_throughput_from_issues(
        issues,
        end_date=datetime.date(2026, 9, 18),
        status_mapping={"On UAT": "Done"},
    )

    assert weeks[0] == datetime.date(2026, 8, 31)
    assert throughput == [1, 0]

