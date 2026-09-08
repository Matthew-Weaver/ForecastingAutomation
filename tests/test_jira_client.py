"""Unit tests for Jira client query formatting and parsing."""

from unittest.mock import MagicMock
from forecasting_automation.config import JiraConfig, QueryConfig
from forecasting_automation.jira_client import JiraClient


def test_build_default_jql():
    config = JiraConfig(
        server="https://company.atlassian.net",
        email="dev@company.com",
        api_token="valid_token",
    )
    client = JiraClient(config)

    query_cfg = QueryConfig(custom_jql="")
    jql = client.build_jql("DEV", query_cfg)
    assert jql == 'project = "DEV" ORDER BY created DESC'


def test_build_custom_jql_combined():
    config = JiraConfig(
        server="https://company.atlassian.net",
        email="dev@company.com",
        api_token="valid_token",
    )
    client = JiraClient(config)

    query_cfg = QueryConfig(custom_jql="status = 'Done'")
    jql = client.build_jql("DEV", query_cfg)
    assert jql == 'project = "DEV" AND (status = \'Done\') ORDER BY created DESC'


def test_parse_issue():
    config = JiraConfig(
        server="https://company.atlassian.net",
        email="dev@company.com",
        api_token="valid_token",
    )
    client = JiraClient(config)

    mock_issue = MagicMock()
    mock_issue.key = "DEV-101"
    mock_issue.fields.summary = "Implement login feature"
    mock_issue.fields.issuetype.name = "Story"
    mock_issue.fields.status.name = "In Progress"
    mock_issue.fields.status.statusCategory.name = "In Progress"
    mock_issue.fields.assignee.displayName = "Jane Doe"
    mock_issue.fields.priority.name = "High"
    mock_issue.fields.created = "2026-01-01T10:00:00.000+0000"
    mock_issue.fields.updated = "2026-01-02T10:00:00.000+0000"
    mock_issue.fields.resolutiondate = None
    mock_issue.fields.customfield_10016 = 5.0
    mock_issue.changelog.histories = []

    parsed = client._parse_issue(mock_issue, story_points_field="customfield_10016")

    assert parsed["key"] == "DEV-101"
    assert parsed["number"] == 101
    assert parsed["summary"] == "Implement login feature"
    assert parsed["issue_type"] == "Story"
    assert parsed["status"] == "In Progress"
    assert parsed["assignee"] == "Jane Doe"
    assert parsed["story_points"] == 5.0
    assert parsed["url"] == "https://company.atlassian.net/browse/DEV-101"


def _make_mock_issue(num):
    m = MagicMock()
    m.key = f"DEV-{num}"
    m.fields.summary = f"Issue {num}"
    m.fields.issuetype.name = "Task"
    m.fields.status.name = "To Do"
    m.fields.status.statusCategory.name = "To Do"
    m.fields.assignee = None
    m.fields.priority.name = "Medium"
    m.fields.created = "2026-01-01"
    m.fields.updated = "2026-01-01"
    m.fields.resolutiondate = None
    m.changelog.histories = []
    return m


def _make_client(status_mapping=None):
    config = JiraConfig(
        server="https://company.atlassian.net",
        email="dev@company.com",
        api_token="valid_token",
    )
    return JiraClient(config, status_mapping=status_mapping)


def test_query_project_issues_fetches_all_on_cloud():
    client = _make_client()

    mock_jira = MagicMock()
    mock_jira._is_cloud = True
    mock_jira.enhanced_search_issues.return_value = [_make_mock_issue(i) for i in range(1, 206)]
    client._client = mock_jira

    results = client.query_project_issues("DEV", QueryConfig(max_results=None))

    assert len(results) == 205
    assert results[0]["key"] == "DEV-1"
    mock_jira.search_issues.assert_not_called()
    # maxResults=False makes the jira library page through every result
    assert mock_jira.enhanced_search_issues.call_args.kwargs["maxResults"] is False


def test_query_project_issues_honors_max_results():
    client = _make_client()

    mock_jira = MagicMock()
    mock_jira._is_cloud = True
    mock_jira.enhanced_search_issues.return_value = [_make_mock_issue(i) for i in range(1, 26)]
    client._client = mock_jira

    client.query_project_issues("DEV", QueryConfig(max_results=25))

    assert mock_jira.enhanced_search_issues.call_args.kwargs["maxResults"] == 25


def test_parse_issue_completed_date_honors_status_mapping():
    config = JiraConfig(
        server="https://company.atlassian.net",
        email="dev@company.com",
        api_token="valid_token",
    )
    client = JiraClient(config, status_mapping={"On UAT": "Done"})

    mock_issue = MagicMock()
    mock_issue.key = "DEV-200"
    mock_issue.fields.summary = "UAT item"
    mock_issue.fields.issuetype.name = "Story"
    mock_issue.fields.status.name = "On UAT"
    mock_issue.fields.status.statusCategory.name = "In Progress"
    mock_issue.fields.assignee = None
    mock_issue.fields.priority.name = "Medium"
    mock_issue.fields.created = "2026-01-01T10:00:00.000+0000"
    mock_issue.fields.updated = "2026-01-05T12:00:00.000+0000"
    mock_issue.fields.resolutiondate = None
    mock_issue.fields.customfield_10016 = None

    history = MagicMock()
    history.created = "2026-01-04T09:00:00.000+0000"
    item = MagicMock()
    item.field = "status"
    item.toString = "On UAT"
    history.items = [item]
    mock_issue.changelog.histories = [history]

    parsed = client._parse_issue(mock_issue)
    assert parsed["completed_date"] == "2026-01-04T09:00:00.000+0000"


def test_query_project_issues_falls_back_on_server():
    client = _make_client()

    mock_jira = MagicMock(spec=["search_issues", "_is_cloud"])
    mock_jira._is_cloud = False
    mock_jira.search_issues.return_value = [_make_mock_issue(i) for i in range(1, 11)]
    client._client = mock_jira

    results = client.query_project_issues("DEV", QueryConfig(max_results=None))

    assert len(results) == 10
    assert mock_jira.search_issues.call_args.kwargs["maxResults"] is False


def _make_history(created, to_status):
    h = MagicMock()
    h.created = created
    item = MagicMock()
    item.field = "status"
    item.toString = to_status
    h.items = [item]
    return h


def test_completed_date_uses_earliest_done_transition():
    client = _make_client(status_mapping={"On UAT": "Done", "Migrate to UAT": "Done"})

    issue = _make_mock_issue(200)
    issue.fields.status.name = "On UAT"
    issue.fields.status.statusCategory.name = "In Progress"
    issue.fields.updated = "2026-03-01T00:00:00.000+0000"
    issue.changelog.histories = [
        _make_history("2026-02-20T09:00:00.000+0000", "Migrate to UAT"),
        _make_history("2026-01-05T09:00:00.000+0000", "In Progress"),
        _make_history("2026-02-10T09:00:00.000+0000", "On UAT"),
    ]

    parsed = client._parse_issue(issue)

    assert parsed["completed_date"] == "2026-02-10T09:00:00.000+0000"


def test_completed_date_falls_back_when_changelog_missing():
    client = _make_client(status_mapping={"On UAT": "Done"})

    issue = _make_mock_issue(201)
    issue.fields.status.name = "On UAT"
    issue.fields.status.statusCategory.name = "In Progress"
    issue.fields.updated = "2026-03-01T00:00:00.000+0000"
    issue.changelog.histories = []

    assert client._parse_issue(issue)["completed_date"] == "2026-03-01T00:00:00.000+0000"


def test_completed_date_is_none_for_open_issues():
    client = _make_client()

    issue = _make_mock_issue(202)
    issue.changelog.histories = [_make_history("2026-02-10T09:00:00.000+0000", "In Progress")]

    assert client._parse_issue(issue)["completed_date"] is None




