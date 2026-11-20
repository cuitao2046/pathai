"""Batch replay every recorded run of an experiment ZIP through ``/api/v1/field/replay/batch``.

The export ZIP keeps all raw RSSI windows, so every run (T6 static, T7 walk, T8 navigation)
can be replayed offline against the frozen configuration without touching the online chain.

Example::

    python tools/replay_all_field_runs.py experiment_export_2026-11-20.zip \
        --base-url http://192.168.1.10:8000 --token <access-token> \
        --config-id field-20261120 --out replay.json
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from zipfile import ZipFile

RUN_FILES = ("static_test_segments.jsonl", "dynamic_trajectories.jsonl", "nav_sessions.jsonl")
CHUNK_SIZE = 50


def run_ids(archive: Path) -> list[str]:
    ids: list[str] = []
    with ZipFile(archive) as bundle:
        for name in RUN_FILES:
            if name not in bundle.namelist():
                continue
            for line in bundle.read(name).decode("utf-8").splitlines():
                row = json.loads(line)
                if row.get("status") == "aborted" or not row.get("runId"):
                    continue
                if row["runId"] not in ids:
                    ids.append(row["runId"])
    return ids


def replay_chunk(base_url: str, token: str | None, payload: dict) -> list[dict]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/field/replay/batch",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))["results"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="experiment_export_*.zip")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=None, help="access token (Authorization: Bearer ...)")
    parser.add_argument("--config-id", default=None, help="冻结配置编号（T6/T7 重放必填）")
    parser.add_argument("--suspend-radius-m", type=float, default=1.5,
                        help="挂起库半径（米），0 表示不挂起")
    parser.add_argument("--out", type=Path, default=Path("replay.json"))
    args = parser.parse_args()

    ids = run_ids(args.archive)
    results: list[dict] = []
    for start in range(0, len(ids), CHUNK_SIZE):
        results.extend(replay_chunk(args.base_url, args.token, {
            "runIds": ids[start:start + CHUNK_SIZE],
            "configId": args.config_id,
            "suspendRadiusM": args.suspend_radius_m,
        }))
    args.out.write_text(json.dumps({
        "archive": args.archive.name,
        "configId": args.config_id,
        "suspendRadiusM": args.suspend_radius_m,
        "runIds": ids,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [item["runId"] for item in results if "error" in item]
    print(f"replayed {len(ids)} runs → {args.out}（失败 {len(failed)}）")
    if failed:
        print("失败：", "、".join(failed))


if __name__ == "__main__":
    main()
