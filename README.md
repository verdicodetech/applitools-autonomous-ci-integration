## Applitools Autonomous → Azure DevOps Test Plans

Python scripts (and a sample CI pipeline) that trigger an [Applitools Autonomous](https://applitools.com) test plan, wait for it to finish, and push the results into **Azure DevOps Test Plans** as a test run.

The Python is CI-agnostic — it only reads CLI flags and environment variables - so the pipeline file is just thin glue around it. Currently there's an example for **Azure Pipelines**.

## How it works

The flow runs in two steps:

1. **`run_applitools.py`** kicks off an Applitools Autonomous plan by its UUID, polls until the plan completes (or a timeout is hit), and writes the results to disk:
   - `results.json` - the transformed/normalised results
   - `results.json.raw` - the raw Applitools API response, kept for debugging
2. **`push_results.py`** reads `results.json` and creates a test run under a given Azure DevOps Test Plan / Test Suite, mapping the Applitools outcomes onto Azure DevOps test results.

In CI these are wrapped by a pipeline that installs dependencies, runs both scripts in order, and publishes the JSON files as build artifacts.

## Repository layout

```
.
├── README.md
├── azure-pipelines.yml          # Example Azure Pipelines workflow
└── scripts/
    ├── requirements.txt         # Python dependencies
    ├── run_applitools.py        # Trigger the Applitools plan + fetch results
    └── push_results.py          # Push results into Azure DevOps Test Plans
```

## Prerequisites

- Python 3.11 (the example pipeline pins this; other 3.x versions likely work)
- An Applitools account with an **Autonomous** plan and an **API key**
- An Azure DevOps project with a **Test Plan** and **Test Suite** to receive results
- A token that can write to Azure DevOps Test Plans (see [Secrets](#secrets-and-tokens))

## Configuration

### Pipeline variables

These are defined as `variables` in `azure-pipelines.yml`. Note that there are two distinct "plan" concepts — the Applitools plan and the Azure DevOps Test Plan - so they're named explicitly to avoid confusion.

| Variable | Example | What it is |
| --- | --- | --- |
| `adoOrg` | `MyOrg` | Azure DevOps organisation name |
| `adoProject` | `My Test Project` | Azure DevOps project name |
| `planId` | `12345` | Azure DevOps **Test Plan** ID (numeric) |
| `suiteId` | `56789` | Azure DevOps **Test Suite** ID (numeric) |
| `applitoolsPlanId` | `aaaaaa00-…` | Applitools **plan UUID** to execute |

### Secrets and tokens

These must be provided as secrets, not committed to the repo:

| Secret | Used by | Notes |
| --- | --- | --- |
| `APPLITOOLS_API_KEY` | `run_applitools.py` | Your Applitools API key. Add it as a secret pipeline variable. |
| `AZURE_DEVOPS_TOKEN` | `push_results.py` | A token authorised to write to Test Plans. In the example pipeline this is the built-in `$(System.AccessToken)`. |

When using `$(System.AccessToken)`, the project's **Build Service** identity needs permission to manage test runs/results in the target project. If pushes fail with an authorization error, grant that identity the necessary Test Plans permissions (or swap in a PAT - although not recommended outside of debugging reasons).

## Running it in Azure Pipelines

The included `azure-pipelines.yml` example is set to manual trigger by default (`trigger: none`, `pr: none`). You can modify different triggers & set up cron schedules based on your needs.

The pipeline:

1. Sets up Python 3.11
2. Installs `scripts/requirements.txt`
3. Runs `run_applitools.py` (with a 65-minute step timeout to cover the polling window)
4. Publishes `results.json` and `results.json.raw` as artifacts (`condition: succeededOrFailed()`, so artifacts are kept even on failure)
5. Runs `push_results.py` to push results into Azure DevOps

## Running locally

The scripts don't depend on anything CI-specific, so you can run the same commands on your machine:

```bash
pip install -r scripts/requirements.txt

export APPLITOOLS_API_KEY="your-applitools-key"

python -u scripts/run_applitools.py \
  --plan-id aaaaaa00-bb00-cc00-dd00-eeeeeeeeeeee \
  --output results.json \
  --poll-interval 15 \
  --timeout 3600

export AZURE_DEVOPS_TOKEN="your-ado-token"

python -u scripts/push_results.py \
  --org "MyOrg" \
  --project "My Test Project" \
  --plan 12345 \
  --suite 56789 \
  --results results.json \
  --run-name "Applitools local run"
```

## Script reference

### `run_applitools.py`

| Flag | Description |
| --- | --- |
| `--plan-id` | Applitools plan UUID to execute |
| `--output` | Path to write the transformed results JSON (raw response is written alongside as `<output>.raw`) |
| `--poll-interval` | Seconds to wait between status checks while the plan runs |
| `--timeout` | Maximum seconds to wait for the plan to finish before giving up |

Reads `APPLITOOLS_API_KEY` from the environment.

### `push_results.py`

| Flag | Description |
| --- | --- |
| `--org` | Azure DevOps organisation name |
| `--project` | Azure DevOps project name |
| `--plan` | Azure DevOps Test Plan ID |
| `--suite` | Azure DevOps Test Suite ID |
| `--results` | Path to the `results.json` produced by `run_applitools.py` |
| `--run-name` | Display name for the test run created in Azure DevOps |

Reads `AZURE_DEVOPS_TOKEN` from the environment.

## Outputs

| File | Description |
| --- | --- |
| `results.json` | Transformed results, consumed by `push_results.py` |
| `results.json.raw` | Raw Applitools API response, useful for debugging mapping issues |

## License

Released under the [MIT License](LICENSE).