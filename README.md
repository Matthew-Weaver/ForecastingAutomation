# Forecasting Automation

Python tool to authenticate with Jira Cloud using local configuration credentials and query project issues via JQL.

## Features

- **Interactive Local Browser UI**: Built with Streamlit — opens automatically in your default browser.
- **Monte Carlo Project Forecasting**: Ported from Google Apps Script to simulate completion dates from historical daily throughput, highlighting the **85th Percentile ($\text{P85}$)** planning target date.
- **Project Status Breakdown**: Reaches into Jira and displays card counts, keys, names/summaries, numbers, priorities, and assignees grouped by workflow status.
- **In-App User Settings**: View, edit, test, and save Jira credentials and configuration directly in the UI to `config.json`.
- **Search & Export**: Real-time card search across all statuses and one-click export to CSV / JSON.
- **Secure Configuration**: Uses a gitignored `config.json` with a version-controlled `config.example.json` template.
- **Jira Cloud Authentication**: Authenticates with Atlassian API Token and user email.
- **CLI Interface**: Headless terminal execution with overrides for project keys, custom JQL filters, limit count, and JSON output export.



---

## Prerequisites

- Python 3.9+
- An Atlassian account with Jira Cloud access
- An Atlassian API token:
  1. Log into [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
  2. Click **Create API token**, assign a label (e.g., `ForecastingAutomation`), and copy the token.

---

## Installation & Setup

### 1. Clone the repository and navigate into the folder:
```bash
cd ForecastingAutomation
```

### 2. Create and activate a virtual environment:

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows (cmd):**
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies:
```bash
pip install -r requirements.txt
```
*(Or install in editable mode: `pip install -e .`)*

---

## Configuration

Copy `config.example.json` to `config.json` (this file is excluded from Git in `.gitignore` to protect credentials):

```bash
cp config.example.json config.json
```

Edit `config.json` with your Jira details:

```json
{
  "jira": {
    "server": "https://your-domain.atlassian.net",
    "email": "your-email@example.com",
    "api_token": "YOUR_JIRA_API_TOKEN"
  },
  "project": {
    "key": "YOUR_PROJECT_KEY"
  },
  "query": {
    "custom_jql": "",
    "max_results": 100,
    "fields": [
      "summary",
      "status",
      "issuetype",
      "created",
      "updated",
      "resolutiondate",
      "assignee",
      "priority"
    ],
    "story_points_field": "customfield_10016"
  }
}
```

### Configuration Options

| Section | Parameter | Description |
| :--- | :--- | :--- |
| `jira.server` | `string` | Base URL of your Jira Cloud instance (e.g. `https://mycompany.atlassian.net`) |
| `jira.email` | `string` | Your Atlassian account email address |
| `jira.api_token` | `string` | API token created from your Atlassian profile |
| `project.key` | `string` | Default Jira project key (e.g. `PROJ`, `ENG`) |
| `query.custom_jql` | `string` | Optional JQL filter (e.g. `status = 'In Progress'`) |
| `query.max_results` | `integer` | Maximum issues to fetch (default: 100) |
| `query.fields` | `array` | List of Jira fields to retrieve |
| `query.story_points_field` | `string` | Custom field ID for story points / estimation if applicable |

---

## Usage

### 1. Launch Interactive Browser UI (Recommended)

To launch the local web interface (which automatically opens in your default browser):

```bash
python -m forecasting_automation.main --ui
```

Or using Streamlit directly:
```bash
streamlit run src/forecasting_automation/app.py
```

Or if installed via `pip install -e .`:
```bash
forecasting-ui
```

Inside the UI:
- **📋 Project Status Breakdown tab**: View total card counts, status distribution chart, and collapsible status groups showing card number, key (with direct links to Jira), summary/name, type, assignee, priority, and story points.
- **📈 Forecasting (Monte Carlo) tab**: Run Monte Carlo trials using historical daily throughput to project project completion dates with the **85th Percentile** target date prominently highlighted, along with simulation frequency charts and throughput breakdown tables.
- **⚙️ User Settings tab**: Enter or edit your Jira URL, email, API token, project key, test your connection live, and save directly to `config.json`.


---

### 2. Run Headless CLI Query

```bash
python -m forecasting_automation.main
```
Or if installed as a package:
```bash
forecasting-automation
```

```

### Command Line Options

- **Target a different project:**
  ```bash
  python -m forecasting_automation.main --project OTHERPROJ
  ```

- **Filter by custom JQL:**
  ```bash
  python -m forecasting_automation.main --jql "status = 'Done' AND resolved >= -30d"
  ```

- **Limit number of results:**
  ```bash
  python -m forecasting_automation.main --limit 25
  ```

- **Export results to JSON:**
  ```bash
  python -m forecasting_automation.main --output data/issues.json
  ```

- **Use a custom configuration file path:**
  ```bash
  python -m forecasting_automation.main --config /path/to/custom_config.json
  ```

- **Enable verbose debug logging:**
  ```bash
  python -m forecasting_automation.main --verbose
  ```
