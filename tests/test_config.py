"""Unit tests for configuration validation and loading."""

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from forecasting_automation.config import AppConfig, JiraConfig, ProjectConfig, load_config


def test_valid_config(tmp_path: Path):
    config_data = {
        "jira": {
            "server": "https://company.atlassian.net",
            "email": "dev@company.com",
            "api_token": "secret_token_123",
        },
        "project": {
            "key": "DEV",
        },
        "query": {
            "custom_jql": "status = 'In Progress'",
            "max_results": 50,
        },
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(config_data), encoding="utf-8")

    loaded = load_config(cfg_file)
    assert loaded.jira.server == "https://company.atlassian.net"
    assert loaded.jira.email == "dev@company.com"
    assert loaded.project.key == "DEV"
    assert loaded.query.max_results == 50
    assert loaded.query.custom_jql == "status = 'In Progress'"


def test_missing_config_raises_file_not_found(tmp_path: Path):
    non_existent = tmp_path / "non_existent_config.json"
    with pytest.raises(FileNotFoundError) as excinfo:
        load_config(non_existent)
    assert "does not exist" in str(excinfo.value)


def test_placeholder_validation_errors():
    with pytest.raises(ValidationError):
        JiraConfig(
            server="https://your-domain.atlassian.net",
            email="valid@example.com",
            api_token="valid_token",
        )

    with pytest.raises(ValidationError):
        JiraConfig(
            server="https://company.atlassian.net",
            email="your-email@example.com",
            api_token="valid_token",
        )

    with pytest.raises(ValidationError):
        JiraConfig(
            server="https://company.atlassian.net",
            email="valid@example.com",
            api_token="YOUR_JIRA_API_TOKEN",
        )

    with pytest.raises(ValidationError):
        ProjectConfig(key="YOUR_PROJECT_KEY")


def test_save_and_load_raw_config(tmp_path: Path):
    from forecasting_automation.config import save_config, load_raw_config

    cfg_file = tmp_path / "custom_config.json"
    data = {
        "jira": {"server": "https://test.atlassian.net", "email": "a@b.com", "api_token": "tok123"},
        "project": {"key": "TEST"},
        "query": {"custom_jql": "", "max_results": 20},
    }
    saved_path = save_config(data, cfg_file)
    assert saved_path.is_file()

    raw = load_raw_config(saved_path)
    assert raw["jira"]["server"] == "https://test.atlassian.net"
    assert raw["project"]["key"] == "TEST"

