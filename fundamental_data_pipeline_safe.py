"""Duplicate-safe wrapper around fundamental_data_pipeline candidate refresh."""
from __future__ import annotations

import argparse

import fundamental_data_pipeline as base
from report_quality import safe_statement_row


def _safe_row(frame, key):
    aliases = base.STATEMENT_ALIASES.get(key)
    if not aliases:
        return None
    return safe_statement_row(frame, aliases, base._norm)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--visual-json", default="data/production-4h/visual_candidates.json")
    p.add_argument("--peer-csv", default="data/fundamentals/latest.csv")
    p.add_argument("--output-dir", default="data/fundamentals/quarterly")
    a = p.parse_args()
    base._statement_row = _safe_row
    result = base.refresh_candidate_histories(a.visual_json, a.peer_csv, a.output_dir)
    print(f"Candidate fundamentals: {len(result['success'])}/{result['requested']} success; {len(result['errors'])} error(s)")
    if result["errors"]:
        for item in result["errors"]:
            print("ERROR", item["symbol"], item["error"])
    return 0 if len(result["success"]) == result["requested"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
