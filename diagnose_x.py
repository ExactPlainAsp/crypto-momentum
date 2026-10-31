"""Test a documented public fixture; publish fixed technical error codes.

No account metadata, row counts, search terms, prices, IDs, report text or
other private values are written to the public Actions log.
"""
import json
import time
from pathlib import Path

from agent import Apify, HTTP, Stop, secret
from signals import normalise_tweet


REASONS = frozenset({
    "aborted", "budget_limited", "deadline_reached", "errored", "invalid_input",
    "no_input", "pagination_safety_limit", "partial_failure",
    "requested_limit_reached", "source_exhausted", "window_empty", "zero_output",
})
CAUSES = frozenset({
    "target_not_found", "target_suspended", "target_protected", "search_unavailable",
    "likes_hidden", "target_failed", "pagination_safety_limit", "reply_reach", "deadline_reached",
})


def technical_codes(report, rows, now):
    """Return a finite set of technical categories, never a response value."""
    codes = set()
    result = report.get("results") or {}
    reason = result.get("completionReason") or report.get("completionReason")
    if reason in REASONS:
        codes.add("SOURCE_" + reason.upper())
    causes = result.get("stopCauses") or report.get("stopCauses") or []
    for cause in causes:
        if isinstance(cause, dict):
            kind = cause.get("cause") or cause.get("code") or cause.get("reason")
            if kind in CAUSES:
                codes.add("SOURCE_" + kind.upper())
    if not isinstance(rows, list):
        codes.add("IMPORT_DATASET_SHAPE")
        return sorted(codes)
    for row in rows:
        if not isinstance(row, dict):
            codes.add("IMPORT_ROW_SHAPE")
            continue
        if not str(row.get("id") or row.get("tweetId") or "").isdigit():
            continue  # Diagnostic rows are not tweets.
        if not (row.get("text") or row.get("fullText")):
            codes.add("IMPORT_POST_TEXT")
            continue
        author = row.get("author")
        if not isinstance(author, dict):
            codes.add("IMPORT_AUTHOR_SHAPE")
            continue
        try:
            post = normalise_tweet(row, now)
        except (TypeError, ValueError, AttributeError):
            codes.add("IMPORT_ROW_SHAPE")
            continue
        if post is None:
            codes.add("IMPORT_REQUIRED_FIELDS")
        elif any(post.get(k) is None for k in ("author_created", "followers", "following", "reply_count", "retweet_count")):
            codes.add("IMPORT_QUALITY_FIELDS")
        else:
            codes.add("IMPORT_CONTRACT_VALID")
    return sorted(codes) or ["SOURCE_UNCLASSIFIED"]


def main():
    config = json.loads(Path("config.json").read_text())
    # A separate ledger contains only this public test's run reservation.
    # Never read the bot's private STATE, posts, chat or trading observations.
    config["apify_store_name"] = "crypto-momentum-public-fixture-v1"
    api = Apify(HTTP(), secret("APIFY_TOKEN"), config)
    budget = api.budget()
    if budget["remaining"] <= config["credit_reserve_usd"]:
        raise Stop("Gratisbudget-Grenze erreicht")
    state = api.load()
    probe = state.setdefault("public_fixture_cent_cap", {})
    if not probe.get("attempted"):
        probe["attempted"] = time.time()
        api.save(state)
        # Tweet 20 is the publicly documented example in the provider README.
        inputs = {"mode": "tweets", "tweetIds": ["20"], "maxItems": 1,
                  "outputVariant": "rich", "fieldStyle": "camelCase"}
        probe["run_id"] = api.start_x(inputs, time.time())
        api.save(state)
    run_id = probe.get("run_id")
    if not run_id:
        print("X-Diagnose: SOURCE_TEST_RESERVED")
        return
    rows = api.finish_x(run_id)
    run = api.call("actor-runs/" + run_id)["data"]
    report = {}
    store = run.get("defaultKeyValueStoreId")
    if store:
        try:
            report = api.call(f"key-value-stores/{store}/records/run-report")
        except Stop:
            print("X-Diagnose: SOURCE_REPORT_UNAVAILABLE")
    for code in technical_codes(report, rows, time.time()):
        print("X-Diagnose: " + code)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("X-Diagnose: SOURCE_INSPECTION_FAILED")
        raise SystemExit(1)
