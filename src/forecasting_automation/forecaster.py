"""Monte Carlo project forecasting engine ported from Google Apps Script.

Simulates project completion dates by sampling historical weekly throughput
and calculates percentiles (50th, 85th, 95th) mapped to calendar working days.
"""

from __future__ import annotations

import datetime
import math
import random
from typing import Any, Callable, Dict, List, Optional, Tuple
import dateutil.parser

WORKING_DAYS_PER_WEEK = 5


def add_working_days(start_date: datetime.date | datetime.datetime, n: int) -> datetime.date:
    """Add n working days (Monday-Friday) to start_date."""
    if isinstance(start_date, datetime.datetime):
        current = start_date.date()
    else:
        current = start_date

    added = 0
    while added < n:
        current += datetime.timedelta(days=1)
        # weekday(): Monday is 0, Sunday is 6
        if current.weekday() < 5:
            added += 1
    return current


def simulate_once(
    cards_left: int,
    weekly_throughput: List[int],
    rng: Optional[Callable[[], float]] = None,
    day_cap: int = 5000,
) -> int:
    """Run a single trial, drawing a new weekly total every 5 working days."""
    if rng is None:
        rng = random.random

    remaining = float(cards_left)
    days = 0
    daily_rate = 0.0
    t_len = len(weekly_throughput)

    while remaining > 0 and days < day_cap:
        if days % WORKING_DAYS_PER_WEEK == 0:
            idx = math.floor(rng() * t_len)
            daily_rate = weekly_throughput[idx] / WORKING_DAYS_PER_WEEK
        remaining -= daily_rate
        days += 1

    return days


def run_simulation(
    weekly_throughput: List[int],
    cards_left: int,
    trials: int = 1000,
    rng: Optional[Callable[[], float]] = None,
) -> List[int]:
    """Execute multiple Monte Carlo simulation trials."""
    if cards_left <= 0 or not weekly_throughput or sum(weekly_throughput) == 0:
        return []

    day_counts = []
    for _ in range(trials):
        day_counts.append(simulate_once(cards_left, weekly_throughput, rng))
    return day_counts


def summarize_days(
    day_counts: List[int],
    start_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Summarize simulation trials into percentiles, histogram, and finish dates."""
    if not day_counts:
        return {
            "percentiles": {},
            "histogram": [],
            "trials": 0,
            "min_days": 0,
            "max_days": 0,
        }

    if start_date is None:
        start_date = datetime.date.today()

    trials = len(day_counts)

    # Count occurrences of each day result
    counts: Dict[int, int] = {}
    for d in day_counts:
        counts[d] = counts.get(d, 0) + 1

    # Sorted histogram
    histogram = [
        {"days": d, "count": counts[d]}
        for d in sorted(counts.keys())
    ]

    # Calculate percentiles (50th, 85th, 95th) matching Google Apps Script logic
    targets = {
        50: 0.50 * trials,
        85: 0.85 * trials,
        95: 0.95 * trials,
    }
    percentiles: Dict[int, int] = {}
    percentile_dates: Dict[int, datetime.date] = {}

    for p in [50, 85, 95]:
        cumulative = 0
        bucket = 0
        target = targets[p]
        while bucket < len(histogram) and cumulative < target:
            cumulative += histogram[bucket]["count"]
            bucket += 1
        found_days = histogram[max(0, bucket - 1)]["days"]
        percentiles[p] = found_days
        percentile_dates[p] = add_working_days(start_date, found_days)

    # Attach finish dates and cumulative percentages to histogram items
    cum = 0
    for item in histogram:
        cum += item["count"]
        item["finish_date"] = add_working_days(start_date, item["days"])
        item["cumulative_pct"] = round((cum / trials) * 100, 2)

    return {
        "trials": trials,
        "percentiles": percentiles,
        "percentile_dates": percentile_dates,
        "histogram": histogram,
        "min_days": min(day_counts),
        "max_days": max(day_counts),
        "start_date": start_date,
    }


DONE_STATUSES = {"done", "on uat", "migrate to uat"}


def is_done_status(status_name: Optional[str], category_name: Optional[str] = None) -> bool:
    """Check if a status represents completed work.
    
    Treats 'Done', 'On UAT', 'Migrate to UAT', and any Jira 'Done' category status as completed.
    """
    if status_name and status_name.strip().lower() in DONE_STATUSES:
        return True
    if category_name and "done" in category_name.strip().lower():
        return True
    return False


def extract_weekly_throughput_from_issues(
    issues: List[Dict[str, Any]],
    days_window: Optional[int] = None,
    end_date: Optional[datetime.date] = None,
) -> Tuple[List[datetime.date], List[int]]:
    """Derive cards completed per week, keyed by each week's Monday.

    Considers issues in 'Done', 'On UAT', 'Migrate to UAT' or 'Done' category.
    Includes 0-progress weeks to reflect realistic delivery cadence.
    """
    if end_date is None:
        end_date = datetime.date.today()

    # Collect resolution/completion dates from done issues
    resolved_dates: List[datetime.date] = []
    for issue in issues:
        status_name = issue.get("status")
        cat_name = issue.get("status_category")

        if is_done_status(status_name, cat_name):
            res_raw = (
                issue.get("completed_date")
                or issue.get("resolution_date")
                or issue.get("updated")
            )
            if res_raw:
                try:
                    dt = dateutil.parser.parse(res_raw)
                    resolved_dates.append(dt.date())
                except Exception:
                    continue

    if not resolved_dates:
        return [], []

    earliest_res = min(resolved_dates)

    if days_window and days_window > 0:
        start_date = max(earliest_res, end_date - datetime.timedelta(days=days_window))
    else:
        start_date = earliest_res

    def week_start(d: datetime.date) -> datetime.date:
        return d - datetime.timedelta(days=d.weekday())

    counts_by_week: Dict[datetime.date, int] = {}
    for d in resolved_dates:
        if start_date <= d <= end_date:
            wk = week_start(d)
            counts_by_week[wk] = counts_by_week.get(wk, 0) + 1

    # Trailing partial week would understate throughput, so stop at the last full week
    last_full_week = week_start(end_date) - datetime.timedelta(days=7)

    weeks_list: List[datetime.date] = []
    throughput_list: List[int] = []

    curr = week_start(start_date)
    while curr <= last_full_week:
        weeks_list.append(curr)
        throughput_list.append(counts_by_week.get(curr, 0))
        curr += datetime.timedelta(days=7)

    return weeks_list, throughput_list
