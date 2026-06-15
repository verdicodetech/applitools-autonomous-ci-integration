import argparse
import json
import os
import sys
import time
import requests

BASE_URL = "https://autonomous.applitools.com"

# Applitools per-test status -> our normalized status
STATUS_MAP = {
    "passed":     "pass",
    "failed":     "fail",
    "aborted":    "fail",
    "unresolved": "fail",   # visual diffs needing review — treated as fail
    "pending":    "skip",
}

# Plan-level statuses that mean "done, stop polling"
TERMINAL_STATUSES = {"passed", "failed", "aborted", "completed"}


def execute_plan(plan_id, api_key, parameter_values=None):
    url = f"{BASE_URL}/api/plan/{plan_id}/execute"
    body = {"parameter_values": parameter_values} if parameter_values else None
    r = requests.post(url, params={"apiKey": api_key}, json=body, timeout=30)
    r.raise_for_status()
    result = r.json()["result"]
    return result["id"], result.get("read_token")


def get_results(run_id, api_key, read_token):
    params = {"apiKey": api_key}
    if read_token:
        params["read_token"] = read_token
    r = requests.get(f"{BASE_URL}/api/result/{run_id}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["result"]

def summarize_by_status(tests):
    """Count tests by their Applitools status — our own tally, not the plan-level field."""
    counts = {}
    for t in tests:
        s = (t.get("status") or "unknown").lower()
        counts[s] = counts.get(s, 0) + 1
    return counts

def wait_for_completion(run_id, api_key, read_token, poll_interval, timeout):
    deadline = time.time() + timeout
    while True:
        if time.time() > deadline:
            raise TimeoutError(f"Run {run_id} did not finish within {timeout}s")

        try:
            result = get_results(run_id, api_key, read_token)
        except requests.RequestException as e:
            print(f"Poll error (will retry): {e}")
            time.sleep(poll_interval)
            continue

        status = (result.get("status") or "").lower()
        print(f"Run {run_id}: status={status or '(in progress)'}")
        if status in TERMINAL_STATUSES:
            return result

        time.sleep(poll_interval)


def to_push_format(plan_result):
    """Applitools plan result -> list of {title, status, duration_ms, notes}"""
    out = []
    for t in plan_result.get("tests", []):
        applitools_status = (t.get("status") or "").lower()
        normalized = STATUS_MAP.get(applitools_status, "fail")
        notes = (
            f"Test Result URL: {t.get('testResultsUrl')} "
            f"\n\nApplitools status: {applitools_status}"
        )
        out.append({
            "title": t.get("name", ""),
            "status": normalized,
            "duration_ms": 0,   # per-test duration not exposed by Applitools
            "notes": notes,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-id", required=True, help="Applitools plan ID")
    ap.add_argument("--output", default="results.json")
    ap.add_argument("--poll-interval", type=int, default=15)
    ap.add_argument("--timeout", type=int, default=3600,
                    help="Max seconds to wait for the plan to finish")
    ap.add_argument("--parameter-values",
                    help="JSON object of plan parameters, e.g. '{\"url\":\"https://x\"}'")
    args = ap.parse_args()

    api_key = os.environ.get("APPLITOOLS_API_KEY")
    if not api_key:
        print("APPLITOOLS_API_KEY env var is required", file=sys.stderr)
        sys.exit(2)

    parameter_values = json.loads(args.parameter_values) if args.parameter_values else None

    print(f"Executing Applitools plan {args.plan_id}...")
    run_id, read_token = execute_plan(args.plan_id, api_key, parameter_values)
    print(f"Triggered run {run_id}")

    result = wait_for_completion(run_id, api_key, read_token,
                                  args.poll_interval, args.timeout)

    # Save the raw response for debugging
    raw_path = args.output + ".raw"
    with open(raw_path, "w") as f:
        json.dump(result, f, indent=2)

    # Save the transformed format push_results.py expects
    push_format = to_push_format(result)
    with open(args.output, "w") as f:
        json.dump(push_format, f, indent=2)

    status_counts = summarize_by_status(result.get("tests", []))
    summary = " ".join(f"{k}={v}" for k, v in sorted(status_counts.items())) or "(no tests)"
    print(f"Plan finished: status={result.get('status')} tests: {summary}")
    print(f"Wrote {len(push_format)} test results to {args.output} "
          f"(raw response in {raw_path})")


if __name__ == "__main__":
    main()