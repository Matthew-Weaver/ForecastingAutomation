"""Jira Cloud API client for authentication and issue querying."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import dateutil.parser
from jira import JIRA, JIRAError

from forecasting_automation.config import JiraConfig, QueryConfig
from forecasting_automation.forecaster import is_done_status

logger = logging.getLogger(__name__)


class JiraClient:
    """Wrapper around Jira Cloud client for authentication and querying."""

    def __init__(
        self,
        config: JiraConfig,
        status_mapping: Optional[Dict[str, str]] = None,
    ):
        self.config = config
        self.status_mapping = status_mapping or {}
        self._client: Optional[JIRA] = None
        self._status_categories: Dict[str, str] = {}

    def connect(self) -> JIRA:
        """Create and authenticate a Jira client session."""
        if self._client is None:
            try:
                self._client = JIRA(
                    server=self.config.server,
                    basic_auth=(self.config.email, self.config.api_token),
                    options={"server": self.config.server},
                )
            except Exception as e:
                raise ConnectionError(
                    f"Failed to connect to Jira instance at '{self.config.server}': {e}"
                ) from e
        return self._client

    def verify_connection(self) -> Dict[str, Any]:
        """Test authentication and fetch basic user/server info."""
        client = self.connect()
        try:
            myself = client.myself()
            server_info = client.server_info()
            return {
                "user_name": myself.get("displayName") or myself.get("name"),
                "email": myself.get("emailAddress", self.config.email),
                "server_title": server_info.get("serverTitle", "Jira Cloud"),
                "version": server_info.get("version", "Cloud"),
            }
        except JIRAError as e:
            raise PermissionError(
                f"Jira authentication failed ({e.status_code}): {e.text}"
            ) from e

    def verify_project_access(self, project_key: str) -> Dict[str, Any]:
        """Verify that the user has access to the specified project."""
        client = self.connect()
        try:
            proj = client.project(project_key)
            return {
                "key": proj.key,
                "name": proj.name,
                "id": proj.id,
                "lead": getattr(getattr(proj, "lead", None), "displayName", "N/A"),
            }
        except JIRAError as e:
            if e.status_code == 404:
                raise ValueError(
                    f"Project '{project_key}' was not found or your user lacks permission to view it."
                ) from e
            raise

    def build_jql(self, project_key: str, query_config: QueryConfig) -> str:
        """Construct the JQL query string."""
        custom = (query_config.custom_jql or "").strip()
        if not custom:
            return f'project = "{project_key}" ORDER BY created DESC'

        # If custom JQL already specifies a project, use it directly
        if "project" in custom.lower():
            return custom

        # Otherwise, prepend project constraint
        return f'project = "{project_key}" AND ({custom}) ORDER BY created DESC'

    def query_project_issues(
        self,
        project_key: str,
        query_config: QueryConfig,
    ) -> List[Dict[str, Any]]:
        """Query all issues from the configured Jira project using JQL."""
        client = self.connect()
        jql = self.build_jql(project_key, query_config)

        # Ensure requested fields + story points field are in fields parameter
        fields_to_fetch = list(query_config.fields)
        if query_config.story_points_field and query_config.story_points_field not in fields_to_fetch:
            fields_to_fetch.append(query_config.story_points_field)

        logger.info("Executing JQL: %s", jql)

        # maxResults=False tells the jira library to page through every result
        fetch_limit = query_config.max_results or False

        try:
            # Jira Cloud's legacy /search endpoint caps results and ignores startAt,
            # so use the token-paginated /search/jql endpoint when available.
            if getattr(client, "_is_cloud", False) and hasattr(client, "enhanced_search_issues"):
                raw_issues = client.enhanced_search_issues(
                    jql_str=jql,
                    maxResults=fetch_limit,
                    fields=fields_to_fetch,
                    expand="changelog",
                )
            else:
                raw_issues = client.search_issues(
                    jql_str=jql,
                    maxResults=fetch_limit,
                    fields=",".join(fields_to_fetch),
                    expand="changelog",
                )
        except JIRAError as e:
            raise RuntimeError(f"Jira JQL search query failed ({e.status_code}): {e.text}") from e

        logger.info("Retrieved a total of %d issues from Jira", len(raw_issues))

        parsed_issues = []
        for issue in raw_issues:
            parsed = self._parse_issue(issue, query_config.story_points_field)
            parsed_issues.append(parsed)

        return parsed_issues

    def _first_done_transition(self, issue: Any) -> Optional[str]:
        """Timestamp of the earliest changelog transition into a done status."""
        histories = getattr(getattr(issue, "changelog", None), "histories", None) or []

        earliest = None
        earliest_raw = None
        for history in histories:
            raw = getattr(history, "created", None)
            if not raw:
                continue
            for item in getattr(history, "items", []):
                if getattr(item, "field", "") != "status":
                    continue
                to_status = getattr(item, "toString", None)
                cat_name = self._status_categories.get(to_status) if to_status else None
                if not is_done_status(to_status, cat_name, self.status_mapping):
                    continue
                try:
                    parsed = dateutil.parser.parse(raw)
                except (ValueError, TypeError):
                    continue
                if earliest is None or parsed < earliest:
                    earliest, earliest_raw = parsed, raw

        return earliest_raw

    def _parse_issue(
        self,
        issue: Any,
        story_points_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract structured data from a Jira Issue object."""
        fields = issue.fields

        # Extract standard fields safely
        status = getattr(fields, "status", None)
        status_name = getattr(status, "name", "N/A") if status else "N/A"
        status_category = (
            getattr(getattr(status, "statusCategory", None), "name", "N/A")
            if status
            else "N/A"
        )

        issuetype = getattr(fields, "issuetype", None)
        issuetype_name = getattr(issuetype, "name", "N/A") if issuetype else "N/A"

        assignee = getattr(fields, "assignee", None)
        assignee_name = (
            getattr(assignee, "displayName", None) or getattr(assignee, "name", "Unassigned")
            if assignee
            else "Unassigned"
        )

        priority = getattr(fields, "priority", None)
        priority_name = getattr(priority, "name", "N/A") if priority else "N/A"

        story_points = None
        if story_points_field and hasattr(fields, story_points_field):
            story_points = getattr(fields, story_points_field)

        # Extract issue number from key (e.g., 'PROJ-123' -> 123)
        issue_number = None
        if issue.key and "-" in issue.key:
            try:
                issue_number = int(issue.key.split("-")[-1])
            except ValueError:
                issue_number = None

        resolution_date = getattr(fields, "resolutiondate", None)
        updated = getattr(fields, "updated", None)

        completed_date = None
        if is_done_status(status_name, status_category, self.status_mapping):
            completed_date = self._first_done_transition(issue) or resolution_date or updated

        return {
            "key": issue.key,
            "number": issue_number,
            "summary": getattr(fields, "summary", "N/A"),
            "issue_type": issuetype_name,
            "status": status_name,
            "status_category": status_category,
            "priority": priority_name,
            "assignee": assignee_name,
            "created": getattr(fields, "created", None),
            "updated": updated,
            "resolution_date": resolution_date,
            "completed_date": completed_date,
            "story_points": story_points,
            "url": f"{self.config.server.rstrip('/')}/browse/{issue.key}",
        }

    def get_project_statuses(self, project_key: str) -> List[Dict[str, Any]]:
        """Retrieve all workflow statuses available in the specified project."""
        client = self.connect()
        try:
            # Try project_statuses if supported by server
            raw_statuses = client.project_statuses(project_key)
            seen = set()
            statuses = []
            for issue_type_status in raw_statuses:
                for st in getattr(issue_type_status, "statuses", []):
                    st_name = getattr(st, "name", "")
                    if st_name and st_name not in seen:
                        seen.add(st_name)
                        statuses.append({
                            "id": getattr(st, "id", ""),
                            "name": st_name,
                            "category": getattr(
                                getattr(st, "statusCategory", None), "name", "Undefined"
                            ),
                        })
            return statuses
        except Exception as e:
            logger.warning("Could not fetch workflow statuses directly: %s", e)
            return []

    def query_issues_grouped_by_status(
        self,
        project_key: str,
        query_config: QueryConfig,
    ) -> Dict[str, Any]:
        """Fetch project issues and group them by status with counts and card metadata."""
        # Retrieve workflow statuses first to populate category cache before parsing issues
        known_statuses = self.get_project_statuses(project_key)
        for s in known_statuses:
            if s.get("name") and s.get("category"):
                self._status_categories[s["name"]] = s["category"]

        issues = self.query_project_issues(project_key, query_config)

        # Group issues
        grouped: Dict[str, Dict[str, Any]] = {}
        for s in known_statuses:
            grouped[s["name"]] = {
                "name": s["name"],
                "category": s.get("category", "Undefined"),
                "count": 0,
                "issues": [],
            }

        for issue in issues:
            st_name = issue["status"]
            if st_name not in grouped:
                grouped[st_name] = {
                    "name": st_name,
                    "category": issue.get("status_category", "Undefined"),
                    "count": 0,
                    "issues": [],
                }
            grouped[st_name]["count"] += 1
            grouped[st_name]["issues"].append(issue)

        return {
            "project_key": project_key,
            "total_count": len(issues),
            "status_groups": grouped,
            "issues": issues,
        }


