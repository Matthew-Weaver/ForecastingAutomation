"""Configuration management and validation for Forecasting Automation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class JiraConfig(BaseModel):
    """Jira connection settings."""

    server: str = Field(..., description="Base URL of Jira instance (e.g. https://domain.atlassian.net)")
    email: str = Field(..., description="User account email address")
    api_token: str = Field(..., description="Atlassian API token")

    @field_validator("server")
    @classmethod
    def validate_server_url(cls, v: str) -> str:
        cleaned = v.strip().rstrip("/")
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("Jira server URL must start with 'http://' or 'https://'")
        if "your-domain.atlassian.net" in cleaned:
            raise ValueError(
                "Default placeholder 'your-domain.atlassian.net' detected in server URL. "
                "Please update config.json with your actual Jira instance URL."
            )
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        cleaned = v.strip()
        if "your-email@example.com" in cleaned or not cleaned:
            raise ValueError(
                "Placeholder or empty email detected. "
                "Please update config.json with your actual Jira account email."
            )
        return cleaned

    @field_validator("api_token")
    @classmethod
    def validate_api_token(cls, v: str) -> str:
        cleaned = v.strip()
        if cleaned == "YOUR_JIRA_API_TOKEN" or not cleaned:
            raise ValueError(
                "Placeholder or empty API token detected. "
                "Please generate a token at https://id.atlassian.com/manage-profile/security/api-tokens "
                "and set it in config.json."
            )
        return cleaned


class ProjectConfig(BaseModel):
    """Target Jira project settings."""

    key: str = Field(..., description="Jira project key (e.g. PROJ)")

    @field_validator("key")
    @classmethod
    def validate_project_key(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if cleaned == "YOUR_PROJECT_KEY" or not cleaned:
            raise ValueError(
                "Placeholder or empty project key detected. "
                "Please configure a valid Jira project key in config.json."
            )
        return cleaned


class QueryConfig(BaseModel):
    """Jira query configuration."""

    custom_jql: Optional[str] = Field(
        default="",
        description="Optional additional JQL conditions or full JQL override",
    )
    max_results: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional maximum number of issues to fetch (None to fetch all project issues)",
    )
    fields: List[str] = Field(
        default_factory=lambda: [
            "summary",
            "status",
            "issuetype",
            "created",
            "updated",
            "resolutiondate",
            "assignee",
            "priority",
        ],
        description="Fields to retrieve for each issue",
    )
    story_points_field: Optional[str] = Field(
        default=None,
        description="Custom field ID for story points / estimation (e.g., customfield_10016)",
    )



class AppConfig(BaseModel):
    """Root application configuration."""

    jira: JiraConfig
    project: ProjectConfig
    query: QueryConfig = Field(default_factory=QueryConfig)


def find_config_file(config_path: Optional[str | Path] = None) -> Path:
    """Locate the configuration file, defaulting to config.json in root."""
    if config_path:
        path = Path(config_path)
        if path.is_file():
            return path
        raise FileNotFoundError(f"Specified configuration file '{path}' does not exist.")

    default_paths = [
        Path.cwd() / "config.json",
        Path(__file__).resolve().parent.parent.parent / "config.json",
    ]

    for candidate in default_paths:
        if candidate.is_file():
            return candidate

    example_path = Path.cwd() / "config.example.json"
    msg = (
        "Configuration file 'config.json' not found!\n\n"
        "To get started:\n"
        f"  1. Copy '{example_path.name}' to 'config.json'\n"
        "  2. Edit 'config.json' and fill in your Jira Cloud credentials and project key.\n"
        "  3. Rerun the program."
    )
    raise FileNotFoundError(msg)


def get_default_config_path() -> Path:
    """Return the default config.json path in the workspace root."""
    return Path.cwd() / "config.json"


def load_raw_config(config_path: Optional[str | Path] = None) -> dict:
    """Load raw JSON configuration dictionary without strict Pydantic validation."""
    if config_path:
        path = Path(config_path)
    else:
        try:
            path = find_config_file()
        except FileNotFoundError:
            path = Path.cwd() / "config.example.json"

    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """Load, parse, and validate application configuration from JSON."""
    resolved_path = find_config_file(config_path)

    try:
        with open(resolved_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except json.JSONDecodeError as err:
        raise ValueError(f"Failed to parse '{resolved_path}' as JSON: {err}") from err

    return AppConfig.model_validate(raw_data)


def save_config(config_data: AppConfig | dict, config_path: Optional[str | Path] = None) -> Path:
    """Save application configuration dictionary or AppConfig to a JSON file."""
    path = Path(config_path) if config_path else get_default_config_path()

    if isinstance(config_data, AppConfig):
        data = config_data.model_dump()
    else:
        data = config_data

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return path

