"""Validate the experiment ZIP and independently score its recorded navigation runs."""
from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
from collections import Counter
from math import hypot
from pathlib import Path
from zipfile import ZipFile

FILES = {
    "CAL": "calibration_segments.jsonl", "FP": "fingerprint_segments.jsonl",
    "ST": "static_test_segments.jsonl", "DY": "dynamic_trajectories.jsonl",
    "NV": "nav_sessions.jsonl",
}
TURN_ACTIONS = {"turn_left", "turn_right", "u_turn"}
DEFAULT_JUMP_THRESHOLD_M = 3.0
METRIC_VERSION = "1.0"


def rows(bundle, name):
    return csv.DictReader(io.StringIO(bundle.read(name).decode("utf-8")))


def progress(polyline, point, max_offset=None):
    """Nearest projection, in metres along the route on the matching floor."""
    best = None
    offset = 0.0
    for start, end in zip(polyline, polyline[1:]):
        dx, dy = end["x"] - start["x"], end["y"] - start["y"]
        length = hypot(dx, dy)
        if length and start["floorId"] == end["floorId"] == point.get("floorId"):
            rx, ry = point["x"] - start["x"], point["y"] - start["y"]
            fraction = max(0.0, min(1.0, (rx * dx + ry * dy) / length**2))
            distance = hypot(rx - fraction * dx, ry - fraction * dy)
            if best is None or distance < best[0]:
                best = (distance, offset + fraction * length)
        offset += length
    return best[1] if best and (max_offset is None or best[0] <= max_offset) else None


def turn_reference_timing(run, events, instructions):
    def timing(signal_at, reference_at):
        delta = signal_at - reference_at if signal_at is not None and reference_at is not None else None
        if delta is None:
            status = "reference_missing" if reference_at is None else "signal_missing"
        else:
            status = "early" if delta < 0 else "late" if delta > 0 else "same_time"
        return {"signalAtEpoch": signal_at, "referenceAtEpoch": reference_at, "deltaMs": delta, "status": status}

    checkpoints = {}
    for event in run.get("checkpoints", []):
        if event.get("undo"):
            checkpoints.pop(event["pointId"], None)
        else:
            checkpoints[event["pointId"]] = event["t"]
    speech = {}
    haptics = {}
    for event in events:
        if event["kind"] == "speech_started" and event.get("source", "").startswith("nav:instruction:"):
            speech.setdefault(event["source"].removeprefix("nav:instruction:"), event["t"])
        if event["kind"] == "turn_haptic":
            haptics.setdefault(event.get("instructionId"), event["t"])
    marks = {event["announcementId"]: event["t"] for event in events if event["kind"] == "announcement_mark"}
    enabled = next((event.get("turnHapticsEnabled") for event in events if event["kind"] == "session"), None)
    routes = [event["route"]["polyline"] for event in events if event["kind"] in ("session", "route")]
    results = {}
    for reference in (e for e in events if e["kind"] == "turn_reference"):
        point_id = reference["pointId"]
        ids = [key for key, instruction in instructions.items()
               if instruction["instruction"].get("trigger", {}).get("nodeId") == reference["nodeId"]]
        instruction_id = ids[0] if len(ids) == 1 else None
        announcement_id = reference["announcementId"]
        announcement = timing(speech.get(instruction_id), checkpoints.get(announcement_id, marks.get(announcement_id)))
        vibration = timing(haptics.get(instruction_id), checkpoints.get(point_id))
        if enabled is False and vibration["signalAtEpoch"] is None:
            vibration["status"] = "disabled"
        if len(ids) > 1:
            announcement["status"] = "ambiguous_instruction"
            if enabled is not False:
                vibration["status"] = "ambiguous_instruction"
        position = reference.get("position")
        if position and routes and not any(progress(route, position, max_offset=0.5) is not None for route in routes):
            for item in (announcement, vibration):
                item.update(deltaMs=None, status="off_route")
        results[point_id] = {"announcementId": reference["announcementId"], "instructionId": instruction_id,
                             "speech": announcement, "haptic": vibration}
    return results


def check_navigation(run, config):
    events = run["navigation"]
    estimates = [e for e in events if e["kind"] == "estimate" and e.get("position")]
    instructions = {e["instruction"]["instructionId"]: e for e in events
                    if e["kind"] == "instruction" and e["instruction"]["action"] in TURN_ACTIONS}
    turn_scores = {key: {"status": "no_playback"} for key in instructions}
    routes = [e for e in events if e["kind"] in ("session", "route")]
    for event in events:
        if event["kind"] != "speech_started" or not event.get("source", "").startswith("nav:instruction:"):
            continue
        key = event["source"].rsplit(":", 1)[-1]
        if key not in instructions or turn_scores[key]["status"] != "no_playback":
            continue
        candidates = [e for e in estimates if abs(e["t"] - event["t"]) <= 1000]
        active_routes = [e for e in routes if e["t"] <= event["t"]]
        turn = instructions[key].get("turn")
        if not candidates or not active_routes or not turn:
            turn_scores[key] = {"status": "not_evaluable"}
            continue
        nearest = min(candidates, key=lambda e: abs(e["t"] - event["t"]))
        polyline = active_routes[-1]["route"]["polyline"]
        at_turn, at_speech = progress(polyline, turn), progress(polyline, nearest["position"])
        if at_turn is None or at_speech is None:
            turn_scores[key] = {"status": "not_evaluable"}
            continue
        delta = at_speech - at_turn
        turn_scores[key] = {"status": "on_time" if -3 <= delta <= 1 else "early" if delta < -3 else "late",
                            "relativeM": round(delta, 2)}
    end = next((e for e in events if e["kind"] == "end"), None)
    system = next((e for e in events if e["kind"] == "end" and e.get("byArrival")), None)
    manual = next((e for e in events if e["kind"] == "arrival_manual"), None)
    delta = system["t"] - manual["t"] if system and manual else None
    if delta is None:
        status = "not_detected" if manual and end else "reference_missing"
    else:
        status = "early" if delta < 0 else "late" if delta > 0 else "same_time"
    arrival = {"reported": system is not None, "withinFrozenRadius": None,
               "systemAtEpoch": system["t"] if system else None,
               "manualAtEpoch": manual["t"] if manual else None, "deltaMs": delta, "status": status}
    outcome = system or end
    if outcome:
        estimates = [e for e in estimates if e["t"] <= outcome["t"]]
        routes = [e for e in routes if e["t"] <= outcome["t"]]
    if outcome and estimates and routes:
        terminal = routes[-1]["route"]["polyline"][-1]
        last = estimates[-1]["position"]
        distance = hypot(last["x"] - terminal["x"], last["y"] - terminal["y"])
        arrival["distanceM"] = round(distance, 2)
        arrival["withinFrozenRadius"] = last["floorId"] == terminal["floorId"] and distance <= config["arrivalRadiusM"]
    return {"runId": run["runId"], "method": run["method"], "turns": turn_scores,
            "turnTiming": turn_reference_timing(run, events, instructions), "arrival": arrival}


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(values):
    values = [value for value in values if value is not None]
    if not values:
        return {"n": 0, "mean": None, "p90": None, "p95": None, "max": None}
    return {"n": len(values), "mean": round(statistics.fmean(values), 3),
            "p90": round(percentile(values, 0.9), 3),
            "p95": round(percentile(values, 0.95), 3), "max": round(max(values), 3)}


def ratio(values):
    values = list(values)
    return sum(1 for value in values if value) / len(values) if values else None


def replay_documents(replay):
    if replay is None:
        return []
    if isinstance(replay, (str, Path)):
        replay = json.loads(Path(replay).read_text(encoding="utf-8"))
    if isinstance(replay, list):
        return replay
    if isinstance(replay, dict) and "results" in replay:
        return replay["results"]
    return [replay]


def replay_metrics(replay):
    """A/B/C accuracy from ``/field/replay`` documents, with all/hit-only views."""
    documents = replay_documents(replay)
    tasks = {}
    sources = Counter()
    fallback_reasons = Counter()
    for document in documents:
        if "error" in document:
            continue
        bucket = tasks.setdefault(document.get("task", "?"), {})
        for checkpoint in document.get("checkpoints", []):
            for method, result in checkpoint.get("methods", {}).items():
                data = bucket.setdefault(method, {"errors": [], "floor": [], "hits": []})
                if result.get("errorM") is not None:
                    data["errors"].append(result["errorM"])
                    data["floor"].append(result.get("floorCorrect") is True)
                if method == "C":
                    sources[result.get("source")] += 1
                    if result.get("fallbackReason"):
                        fallback_reasons[result["fallbackReason"]] += 1
                    if result.get("source") == "fingerprint" and result.get("errorM") is not None:
                        data["hits"].append(result["errorM"])
    accuracy = {}
    for task, methods in tasks.items():
        accuracy[task] = {}
        for method, data in methods.items():
            accuracy[task][method] = {
                "all": {**summarize(data["errors"]), "floorCorrectRate": ratio(data["floor"])},
            }
            if method == "C":
                accuracy[task][method]["hitOnly"] = summarize(data["hits"])
    attempts = sources["fingerprint"] + sources["trilateration"]
    return {
        "documents": sum(1 for document in documents if "error" not in document),
        "errors": [document for document in documents if "error" in document],
        "tasks": accuracy,
        "C": {"fingerprintHits": sources["fingerprint"],
              "fallbacksToB": sources["trilateration"],
              "fallbackRate": sources["trilateration"] / attempts if attempts else None,
              "fallbackReasons": dict(fallback_reasons)},
    }


def navigation_metrics(segments, jump_threshold):
    """Availability and timing metrics computed from recorded navigation events."""
    estimates = 0
    located = 0
    intervals = []
    rtts = []
    jumps = 0
    runs_with_jump = 0
    for segment in segments:
        stream = [event for event in segment["navigation"]
                  if event.get("kind") == "estimate" and isinstance(event.get("t"), (int, float))]
        positions = []
        for event in stream:
            estimates += 1
            if event.get("position"):
                located += 1
                positions.append(event)
            if isinstance(event.get("requestMs"), (int, float)):
                rtts.append(event["requestMs"])
        intervals.extend(
            current["t"] - previous["t"]
            for previous, current in zip(stream, stream[1:])
            if 0 < current["t"] - previous["t"] <= 60_000)
        run_jumps = sum(
            1 for previous, current in zip(positions, positions[1:])
            if previous["position"].get("floorId") == current["position"].get("floorId")
            and hypot(current["position"]["x"] - previous["position"]["x"],
                      current["position"]["y"] - previous["position"]["y"]) > jump_threshold)
        jumps += run_jumps
        runs_with_jump += 1 if run_jumps else 0
    return {
        "validLocationRate": located / estimates if estimates else None,
        "estimates": estimates,
        "positionedEstimates": located,
        "jumpCount": jumps,
        "runsWithJump": runs_with_jump,
        "jumpThresholdM": jump_threshold,
        "updateIntervalMs": summarize(intervals),
        "requestRttMs": summarize(rtts),
    }


def timing_metrics(navigation):
    """Instruction/arrival timing distributions from the scored navigation runs."""
    speech, haptics, arrivals = [], [], []
    speech_status = Counter()
    playback_status = Counter()
    for run in navigation:
        for armed in run["turnTiming"].values():
            speech_status[armed["speech"]["status"]] += 1
            if armed["speech"]["deltaMs"] is not None:
                speech.append(armed["speech"]["deltaMs"])
            if armed["haptic"]["deltaMs"] is not None:
                haptics.append(armed["haptic"]["deltaMs"])
        for score in run["turns"].values():
            playback_status[score["status"]] += 1
        if run["arrival"]["deltaMs"] is not None:
            arrivals.append(run["arrival"]["deltaMs"])
    return {
        "turnSpeechDeltaMs": summarize(speech),
        "turnHapticDeltaMs": summarize(haptics),
        "arrivalDetectionDeltaMs": summarize(arrivals),
        "turnSpeechStatusCounts": dict(speech_status),
        "turnPlaybackStatusCounts": dict(playback_status),
    }


def analyze(path, replay=None, jump_threshold=DEFAULT_JUMP_THRESHOLD_M):
    with ZipFile(path) as bundle:
        if set(bundle.namelist()) != {*FILES.values(), "ground_truth.csv", "observer_records.csv", "manifest.json"}:
            raise ValueError("实验 ZIP 的八类产物不齐")
        info = json.loads(bundle.read("manifest.json"))
        configs = {c["configId"]: c for c in info["frozenConfigurations"]}
        ids = set()
        nav = []
        nav_segments = []
        c = Counter()
        for task, file in FILES.items():
            count = 0
            with bundle.open(file) as stream:
                for line in stream:
                    row = json.loads(line)
                    if row["task"] != task or row["segmentId"] in ids:
                        raise ValueError(f"{file} 含重复段或任务分类错误")
                    ids.add(row["segmentId"])
                    count += 1
                    if task == "NV" and row["status"] == "complete":
                        nav.append(check_navigation(row, configs[row["configId"]]))
                        nav_segments.append(row)
                        if row["method"] == "C":
                            c.update(e.get("source") for e in row["navigation"] if e["kind"] == "estimate")
            if count != info["counts"][task]:
                raise ValueError(f"{file} 与 manifest 计数不一致")
        observed = Counter((r["turnRating"] for r in rows(bundle, "observer_records.csv") if r["turnRating"]))
        truth_rows = sum(1 for _ in rows(bundle, "ground_truth.csv"))
    attempts = c["fingerprint"] + c["trilateration"]
    return {"counts": info["counts"], "truthRows": truth_rows, "observerTurnRatings": dict(observed),
            "C": {"fingerprintHits": c["fingerprint"], "fallbacksToB": c["trilateration"],
                  "fallbackRate": c["trilateration"] / attempts if attempts else None},
            "navigation": nav,
            "accuracy": replay_metrics(replay),
            "usability": navigation_metrics(nav_segments, jump_threshold),
            "timeliness": timing_metrics(nav),
            "analysisConfig": {"metricVersion": METRIC_VERSION, "jumpThresholdM": jump_threshold,
                               "export": info.get("analysisConfig")}}


def metric_rows(report):
    rows_out = []

    def add(dimension, task, method, view, metric, value):
        rows_out.append({"dimension": dimension, "task": task, "method": method,
                         "view": view, "metric": metric, "value": value})

    for task, methods in report.get("accuracy", {}).get("tasks", {}).items():
        for method, views in methods.items():
            for view, values in views.items():
                for metric, value in values.items():
                    add("accuracy", task, method, view, metric, value)
    for metric in ("fingerprintHits", "fallbacksToB", "fallbackRate"):
        add("accuracy", "*", "C", "replay", metric, report.get("accuracy", {}).get("C", {}).get(metric))
    for metric, value in report.get("usability", {}).items():
        if isinstance(value, dict):
            for key, item in value.items():
                add("usability", "*", "*", "all", f"{metric}.{key}", item)
        else:
            add("usability", "*", "*", "all", metric, value)
    for metric, value in report.get("timeliness", {}).items():
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, dict):
                    for sub, number in item.items():
                        add("timeliness", "*", "*", "all", f"{metric}.{key}.{sub}", number)
                else:
                    add("timeliness", "*", "*", "all", f"{metric}.{key}", item)
        else:
            add("timeliness", "*", "*", "all", metric, value)
    return rows_out


def write_csv(report, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "dimension", "task", "method", "view", "metric", "value"])
        writer.writeheader()
        writer.writerows(metric_rows(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--replay", help="batch replay JSON written by tools/replay_all_field_runs.py")
    parser.add_argument("--jump-threshold", type=float, default=DEFAULT_JUMP_THRESHOLD_M,
                        help="位置跳变判定距离（米），默认 3.0")
    parser.add_argument("--csv", help="把指标表另存为 CSV")
    args = parser.parse_args()
    report = analyze(args.archive, replay=args.replay, jump_threshold=args.jump_threshold)
    if args.csv:
        write_csv(report, args.csv)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
