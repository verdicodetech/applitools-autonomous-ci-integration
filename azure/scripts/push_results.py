import argparse
import base64
import json
import os
import re
import sys
import requests

OUTCOME_MAP = {"pass": "Passed", "fail": "Failed", "skip": "NotExecuted"}
_ID_PREFIX = re.compile(r"^\s*\[([^\]]+)\]")


def auth_header(tok: str) -> dict:
    token = base64.b64encode(f":{tok}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def extract_test_case_ids(title: str) -> list[int]:
    """'[1234, 5678] some title' -> [1234, 5678]. Returns [] if no bracket prefix."""
    m = _ID_PREFIX.match(title)
    if not m:
        return []
    tokens = re.split(r"[,\s]+", m.group(1).strip())
    return [int(t) for t in tokens if t.isdigit()]


def aggregate(results: list[dict]) -> dict:
    """Multiple external results -> one effective result. Failures win."""
    # If you want a richer ordering later, swap for:
    #   priority = {"fail": 0, "skip": 1, "pass": 2}
    #   return min(results, key=lambda r: priority.get(r["status"], 99))
    for r in results:
        if r["status"] == "fail":
            return r
    return results[0]


def fetch_points_recursive(base: str, headers: dict, plan_id: int, suite_id: int) -> list[dict]:
    """
    Return every test point under suite_id and all descendant sub-suites.

    Uses the newer `testplan` API with isRecursive=true, which walks the suite
    tree server-side in one paginated call. Each item carries:
        - id: the test point id
        - testCaseReference.id: the test case work item id
    """
    url = f"{base}/testplan/Plans/{plan_id}/Suites/{suite_id}/TestPoint"
    points: list[dict] = []
    continuation_token = None

    while True:
        # includePointDetails=false trims tester/configuration/last-result info
        # from each point. We only read id and testCaseReference.id, both of
        # which stay in the basic payload, so this is pure overhead reduction.
        params = {
            "api-version": "7.1",
            "isRecursive": "true",
            "includePointDetails": "false",
        }
        if continuation_token:
            params["continuationToken"] = continuation_token

        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        points.extend(r.json().get("value", []))

        # Continuation token comes back as a response header on this endpoint.
        continuation_token = r.headers.get("x-ms-continuationtoken")
        if not continuation_token:
            break

    return points


def push(org, project, plan_id, suite_id, raw_results, tok, run_name):
    base = f"https://dev.azure.com/{org}/{project}/_apis"
    headers = auth_header(tok)

    # 1. Bucket external results by Azure test case ID, then aggregate
    by_case_id: dict[int, list[dict]] = {}
    skipped_titles = []
    for r in raw_results:
        ids = extract_test_case_ids(r["title"])
        if not ids:
            skipped_titles.append(r["title"])
            continue
        for cid in ids:
            by_case_id.setdefault(cid, []).append(r)

    effective = {cid: aggregate(rs) for cid, rs in by_case_id.items()}
    print(f"External results: {len(raw_results)} parsed, "
          f"{len(skipped_titles)} skipped (no [id] prefix), "
          f"mapping to {len(effective)} test cases")
    for t in skipped_titles:
        print(f"  skipped: {t}")

    # 2. List test points across the suite tree (suite + all sub-suites)
    points = fetch_points_recursive(base, headers, plan_id, suite_id)

    # Walk points once, attaching the external result for any case we recognise.
    # A test case can produce multiple points (multiple sub-suites, multiple
    # configurations), so the same external result naturally lands on each one.
    point_to_result: dict[int, dict] = {}
    matched_case_ids: set[int] = set()
    for p in points:
        case_id = int(p["testCaseReference"]["id"])
        if case_id in effective:
            point_to_result[int(p["id"])] = effective[case_id]
            matched_case_ids.add(case_id)

    unmatched_cases = sorted(set(effective) - matched_case_ids)

    print(f"Test points: {len(points)} in suite tree, "
          f"{len(point_to_result)} matched to results "
          f"(across {len(matched_case_ids)} test cases)")
    if unmatched_cases:
        print(f"  result IDs not found in suite tree: {unmatched_cases}")

    if not point_to_result:
        print("Nothing to push.")
        return 0

    # 3. Create the run
    r = requests.post(
        f"{base}/test/runs",
        headers=headers,
        params={"api-version": "7.1"},
        json={
            "name": run_name,
            "plan": {"id": str(plan_id)},
            "pointIds": list(point_to_result.keys()),
            "automated": True,
            "state": "InProgress",
        },
    )
    r.raise_for_status()
    run_id = r.json()["id"]
    print(f"Created run {run_id}")

    # 4. Get auto-created result rows
    r = requests.get(
        f"{base}/test/Runs/{run_id}/results",
        headers=headers,
        params={"api-version": "7.1"},
    )
    r.raise_for_status()
    result_rows = r.json()["value"]

    # 5. PATCH outcomes
    patch_body = []
    for row in result_rows:
        point_id = int(row["testPoint"]["id"])
        ext = point_to_result[point_id]
        patch_body.append({
            "id": row["id"],
            "outcome": OUTCOME_MAP[ext["status"]],
            "state": "Completed",
            "durationInMs": ext["duration_ms"],
            "comment": f"{ext['title']}\n{ext.get('notes', '')}".strip(),
        })

    r = requests.patch(
        f"{base}/test/Runs/{run_id}/results",
        headers=headers,
        params={"api-version": "7.1"},
        json=patch_body,
    )
    r.raise_for_status()

    # 6. Close run
    r = requests.patch(
        f"{base}/test/Runs/{run_id}",
        headers=headers,
        params={"api-version": "7.1"},
        json={"state": "Completed"},
    )
    r.raise_for_status()

    failed = sum(1 for ext in point_to_result.values() if ext["status"] == "fail")
    print(f"Run {run_id} completed. {failed} failure(s).")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", type=int, required=True)
    ap.add_argument("--suite", type=int, required=True)
    ap.add_argument("--results", required=True, help="Path to results JSON (list)")
    ap.add_argument("--run-name", default="External test run")
    args = ap.parse_args()

    tok = os.environ.get("AZURE_DEVOPS_TOKEN")
    if not tok:
        print("AZURE_DEVOPS_TOKEN env var is required", file=sys.stderr)
        sys.exit(2)

    with open(args.results) as f:
        raw_results = json.load(f)

    sys.exit(push(args.org, args.project, args.plan, args.suite,
                  raw_results, tok, args.run_name))


if __name__ == "__main__":
    main()