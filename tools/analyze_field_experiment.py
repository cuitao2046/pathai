"""Validate the experiment ZIP and independently score its recorded navigation runs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import statistics
import subprocess
from collections import Counter
from datetime import datetime, timezone
from math import hypot
from pathlib import Path
from zipfile import ZipFile

FILES = {
    "CAL": "calibration_segments.jsonl", "FP": "fingerprint_segments.jsonl",
    "ST": "static_test_segments.jsonl", "DY": "dynamic_trajectories.jsonl",
    "NV": "nav_sessions.jsonl", "UT": "uturn_sessions.jsonl",
}
TURN_ACTIONS = {"turn_left", "turn_right", "u_turn"}
DEFAULT_JUMP_THRESHOLD_M = 3.0
METRIC_VERSION = "1.1"
#: T9 时效口径：|播报 − 检查点打点| ≤ 1s 视为同时；P90 目标 ≤ 1.5s。
UTURN_SAME_TIME_MS = 1000
UTURN_P90_TARGET_MS = 1500

#: 逐条中间数据的列定义；文件按 key 命名（如 segments.csv），便于人工复核。
DETAIL_FIELDS = {
    "segments": [
        "task", "runId", "segmentId", "planId", "planned", "pointId", "orientation", "part",
        "method", "status", "startedAtEpoch", "durationS", "rssiFrames", "checkpoints",
        "navigationEvents", "hasTruth", "truthX", "truthY", "truthFloor", "truthNote",
    ],
    "runs": [
        "task", "runId", "segments", "complete", "suspect", "aborted", "plannedSegments",
        "trialSegments", "methods", "points", "marks", "durationS",
    ],
    "exclusions": ["segmentId", "reason"],
    "accuracy_checkpoints": [
        "task", "runId", "checkpointIndex", "pointId", "orientation", "t",
        "truthX", "truthY", "truthFloor", "method", "estimateX", "estimateY", "estimateFloor",
        "errorM", "floorCorrect", "source", "fallbackReason", "rmseDb", "overlap", "marginDb",
        "confidence", "diagnostics",
    ],
    "nav_estimates": [
        "runId", "method", "configId", "t", "positioned", "floorId", "x", "y",
        "requestMs", "visibleBeaconCount", "intervalMs", "jump",
    ],
    "timing_events": [
        "runId", "method", "pointId", "kind", "status", "signalAtEpoch", "referenceAtEpoch",
        "deltaMs", "relativeM", "announcementId", "instructionId", "distanceM",
    ],
    "uturn_timing": [
        "runId", "method", "pointId", "checkAtEpoch", "speechAtEpoch", "detectedAtEpoch",
        "deltaMs", "absDeltaMs", "status", "announcements", "duplicateAnnouncements",
    ],
}


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


def uturn_timing(run):
    """T9：从「检查点打点」到「掉头播报开始」的纠偏时效。

    - 检查点：T9 清单只有一个点，取最后一条未撤销的打点；
    - 播报：``speech_started`` 且 ``source == "nav:uturn"``（客户端离线音频）；
    - deltaMs = 播报 − 打点；|delta| ≤ 1s 记 same_time，负值 early，正值 late。
    """
    marks = [event for event in run.get("checkpoints", []) if not event.get("undo")]
    check = marks[-1] if marks else None
    speech = next((event for event in run.get("navigation", [])
                   if event.get("kind") == "speech_started" and event.get("source") == "nav:uturn"), None)
    detected = next((event for event in run.get("navigation", [])
                     if event.get("kind") == "uturn_detected"), None)
    announcements = sum(1 for event in run.get("navigation", [])
                        if event.get("kind") == "speech_started" and event.get("source") == "nav:uturn")
    check_at = check["t"] if check else None
    speech_at = speech["t"] if speech else None
    detection_at = detected["t"] if detected else None
    delta = speech_at - check_at if speech_at is not None and check_at is not None else None
    if delta is None:
        status = "reference_missing" if check_at is None else "not_detected"
    else:
        status = ("same_time" if abs(delta) <= UTURN_SAME_TIME_MS
                  else "early" if delta < 0 else "late")
    return {
        "runId": run["runId"], "method": run.get("method", ""),
        "pointId": check["pointId"] if check else "",
        "checkAtEpoch": check_at, "speechAtEpoch": speech_at, "detectedAtEpoch": detection_at,
        "deltaMs": delta, "absDeltaMs": abs(delta) if delta is not None else None,
        "status": status,
        "detected": speech_at is not None or detection_at is not None,
        "announcements": announcements,
        "duplicateAnnouncements": max(0, announcements - 1),
    }


def _uturn_summary(uturns):
    deltas = [item["deltaMs"] for item in uturns if item["deltaMs"] is not None]
    abs_deltas = [abs(value) for value in deltas]
    statuses = Counter(item["status"] for item in uturns)
    detection_rate = ratio(item["detected"] for item in uturns)
    same_time_rate = (sum(1 for value in deltas if abs(value) <= UTURN_SAME_TIME_MS) / len(deltas)
                      if deltas else None)
    p90 = percentile(abs_deltas, 0.9)
    return {
        "runs": len(uturns),
        "detectedRuns": sum(1 for item in uturns if item["detected"]),
        "detectionRate": detection_rate,
        "deltaMs": summarize(deltas),
        "absDeltaMs": summarize(abs_deltas),
        "sameTimeRate": same_time_rate,
        "statusCounts": dict(statuses),
        "duplicateAnnouncements": sum(item["duplicateAnnouncements"] for item in uturns),
        "acceptance": {
            "detectionRateAtLeast95": None if detection_rate is None else detection_rate >= 0.95,
            "p90Within1500ms": None if p90 is None else p90 <= UTURN_P90_TARGET_MS,
            "noDuplicateAnnouncements": all(item["duplicateAnnouncements"] == 0 for item in uturns),
        },
    }


def uturn_metrics(uturns):
    """T9 时效：总体 + 按方法（B/C）。"""
    methods = sorted({item["method"] for item in uturns})
    return {
        "runs": uturns,
        "summary": _uturn_summary(uturns),
        "byMethod": {method: _uturn_summary([item for item in uturns if item["method"] == method])
                     for method in methods},
    }


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
                data["floor"].append(result.get("floorCorrect") is True)
                if result.get("errorM") is not None:
                    data["errors"].append(result["errorM"])
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


def _availability(segments, jump_threshold):
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


def navigation_metrics(segments, jump_threshold):
    """Availability metrics overall and per method (A/B/C), computed from recorded events."""
    methods = sorted({segment["method"] for segment in segments})
    return {
        **_availability(segments, jump_threshold),
        "byMethod": {method: _availability(
            [segment for segment in segments if segment["method"] == method], jump_threshold)
            for method in methods},
    }


def _timing(navigation):
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


def timing_metrics(navigation):
    """Instruction/arrival timing overall and per method (A/B/C)."""
    methods = sorted({run["method"] for run in navigation})
    return {
        **_timing(navigation),
        "byMethod": {method: _timing(
            [run for run in navigation if run["method"] == method])
            for method in methods},
    }


def _value(value):
    return "" if value is None else value


def _segment_detail(row, plan_points):
    truth = plan_points.get(row["task"], {}).get(row.get("pointId") or "")
    if truth is not None:
        note = ""
    elif not row.get("planId"):
        note = "试采段，无正式计划"
    elif row.get("pointId"):
        note = "计划中缺少该点位"
    else:
        note = "该任务不按单点采集"
    return {
        "task": row["task"], "runId": row["runId"], "segmentId": row["segmentId"],
        "planId": row.get("planId") or "", "planned": 1 if row.get("planId") else 0,
        "pointId": row.get("pointId") or "", "orientation": row.get("orientation") or "",
        "part": row.get("part", ""), "method": row.get("method", ""), "status": row["status"],
        "startedAtEpoch": row["startedAtEpoch"],
        "durationS": round((row["endedAtEpoch"] - row["startedAtEpoch"]) / 1000, 1),
        "rssiFrames": len(row.get("rssi", [])), "checkpoints": len(row.get("checkpoints", [])),
        "navigationEvents": len(row.get("navigation", [])),
        "hasTruth": 1 if truth is not None else 0,
        "truthX": truth.get("x", "") if truth else "",
        "truthY": truth.get("y", "") if truth else "",
        "truthFloor": truth.get("floorId", "") if truth else "",
        "truthNote": note,
    }


def _run_details(segment_rows):
    buckets = {}
    for row in segment_rows:
        buckets.setdefault((row["task"], row["runId"]), []).append(row)
    output = []
    for (task, run_id), items in sorted(buckets.items()):
        output.append({
            "task": task, "runId": run_id, "segments": len(items),
            "complete": sum(1 for item in items if item["status"] == "complete"),
            "suspect": sum(1 for item in items if item["status"] == "suspect"),
            "aborted": sum(1 for item in items if item["status"] == "aborted"),
            "plannedSegments": sum(item["planned"] for item in items),
            "trialSegments": sum(1 for item in items if not item["planned"]),
            "methods": "|".join(sorted({item["method"] for item in items if item["method"]})),
            "points": len({item["pointId"] for item in items if item["pointId"]}),
            "marks": sum(item["checkpoints"] for item in items),
            "durationS": round(sum(item["durationS"] for item in items), 1),
        })
    return output


def _estimate_details(segment, jump_threshold):
    stream = [event for event in segment.get("navigation", [])
              if event.get("kind") == "estimate" and isinstance(event.get("t"), (int, float))]
    output = []
    previous = None
    previous_position = None
    for event in stream:
        position = event.get("position") or {}
        interval = event["t"] - previous["t"] if previous is not None else None
        jump = 0
        if (position and previous_position
                and position.get("floorId") == previous_position.get("floorId")
                and hypot(position.get("x", 0) - previous_position.get("x", 0),
                          position.get("y", 0) - previous_position.get("y", 0)) > jump_threshold):
            jump = 1
        output.append({
            "runId": segment["runId"], "method": segment.get("method", ""),
            "configId": segment.get("configId", ""), "t": event["t"],
            "positioned": 1 if position else 0,
            "floorId": position.get("floorId", ""),
            "x": position.get("x", ""), "y": position.get("y", ""),
            "requestMs": _value(event.get("requestMs")),
            "visibleBeaconCount": _value(event.get("visibleBeaconCount")),
            "intervalMs": interval if interval is not None and 0 < interval <= 60_000 else "",
            "jump": jump,
        })
        previous = event
        if position:
            previous_position = position
    return output


def _accuracy_details(replay, plan_points):
    output = []
    for document in replay_documents(replay):
        if "error" in document:
            continue
        task = document.get("task", "")
        for index, checkpoint in enumerate(document.get("checkpoints", [])):
            point_id = checkpoint.get("pointId", "")
            truth = plan_points.get(task, {}).get(point_id)
            for method, result in checkpoint.get("methods", {}).items():
                output.append({
                    "task": task, "runId": document.get("runId", ""),
                    "checkpointIndex": index + 1, "pointId": point_id,
                    "orientation": checkpoint.get("orientation", ""),
                    "t": checkpoint.get("t", ""),
                    "truthX": truth.get("x", "") if truth else "",
                    "truthY": truth.get("y", "") if truth else "",
                    "truthFloor": truth.get("floorId", "") if truth else "",
                    "method": method,
                    "estimateX": _value(result.get("x")), "estimateY": _value(result.get("y")),
                    "estimateFloor": _value(result.get("floorId")),
                    "errorM": _value(result.get("errorM")),
                    "floorCorrect": _value(result.get("floorCorrect")),
                    "source": result.get("source", ""),
                    "fallbackReason": result.get("fallbackReason") or "",
                    "rmseDb": _value(result.get("rmseDb")),
                    "overlap": _value(result.get("overlap")),
                    "marginDb": _value(result.get("marginDb")),
                    "confidence": _value(result.get("confidence")),
                    "diagnostics": (json.dumps(result["diagnostics"], ensure_ascii=False, sort_keys=True)
                                    if result.get("diagnostics") else ""),
                })
    return output


def _timing_details(nav):
    output = []
    for run in nav:
        for point_id, armed in run["turnTiming"].items():
            relative = run["turns"].get(point_id, {}).get("relativeM", "")
            for kind in ("speech", "haptic"):
                item = armed[kind]
                output.append({
                    "runId": run["runId"], "method": run["method"], "pointId": point_id,
                    "kind": kind, "status": item["status"],
                    "signalAtEpoch": _value(item["signalAtEpoch"]),
                    "referenceAtEpoch": _value(item["referenceAtEpoch"]),
                    "deltaMs": _value(item["deltaMs"]), "relativeM": _value(relative),
                    "announcementId": armed.get("announcementId", ""),
                    "instructionId": armed.get("instructionId") or "", "distanceM": "",
                })
        arrival = run["arrival"]
        output.append({
            "runId": run["runId"], "method": run["method"], "pointId": "", "kind": "arrival",
            "status": arrival["status"],
            "signalAtEpoch": _value(arrival.get("systemAtEpoch")),
            "referenceAtEpoch": _value(arrival.get("manualAtEpoch")),
            "deltaMs": _value(arrival.get("deltaMs")), "relativeM": "",
            "announcementId": "", "instructionId": "",
            "distanceM": _value(arrival.get("distanceM")),
        })
    return output


def analyze(path, replay=None, jump_threshold=DEFAULT_JUMP_THRESHOLD_M, with_details=False):
    with ZipFile(path) as bundle:
        namelist = set(bundle.namelist())
        required = {*FILES.values(), "ground_truth.csv", "observer_records.csv", "manifest.json"}
        # 兼容 T9 之前导出的旧归档（没有 uturn_sessions.jsonl）。
        if required - namelist - {"uturn_sessions.jsonl"} or namelist - required:
            raise ValueError("实验 ZIP 的产物不齐")
        info = json.loads(bundle.read("manifest.json"))
        configs = {c["configId"]: c for c in info["frozenConfigurations"]}
        plan_points = {}
        for plan in info.get("plans", []):
            target = plan_points.setdefault(plan["task"], {})
            for point in plan.get("points", []):
                target.setdefault(point["pointId"], point)
        details = {name: [] for name in DETAIL_FIELDS} if with_details else None
        if details is not None:
            details["exclusions"] = [{"segmentId": item.get("segmentId", ""),
                                      "reason": item.get("reason", "")}
                                     for item in info.get("excluded", [])]
        ids = set()
        nav = []
        nav_segments = []
        uturn_runs = []
        c = Counter()
        for task, file in FILES.items():
            count = 0
            if file not in namelist:
                continue
            with bundle.open(file) as stream:
                for line in stream:
                    row = json.loads(line)
                    if row["task"] != task or row["segmentId"] in ids:
                        raise ValueError(f"{file} 含重复段或任务分类错误")
                    ids.add(row["segmentId"])
                    count += 1
                    if details is not None:
                        details["segments"].append(_segment_detail(row, plan_points))
                    if task == "NV" and row["status"] == "complete":
                        nav.append(check_navigation(row, configs[row["configId"]]))
                        nav_segments.append(row)
                        if details is not None:
                            details["nav_estimates"].extend(_estimate_details(row, jump_threshold))
                        if row["method"] == "C":
                            c.update(e.get("source") for e in row["navigation"] if e["kind"] == "estimate")
                    if task == "UT" and row["status"] == "complete":
                        uturn_runs.append(uturn_timing(row))
            if count != info["counts"].get(task, 0):
                raise ValueError(f"{file} 与 manifest 计数不一致")
        observed = Counter((r["turnRating"] for r in rows(bundle, "observer_records.csv") if r["turnRating"]))
        truth_rows = sum(1 for _ in rows(bundle, "ground_truth.csv"))
    if details is not None:
        details["runs"] = _run_details(details["segments"])
        details["accuracy_checkpoints"] = _accuracy_details(replay, plan_points)
        details["timing_events"] = _timing_details(nav)
        details["uturn_timing"] = uturn_runs
    attempts = c["fingerprint"] + c["trilateration"]
    report = {"counts": info["counts"], "truthRows": truth_rows, "observerTurnRatings": dict(observed),
              "C": {"fingerprintHits": c["fingerprint"], "fallbacksToB": c["trilateration"],
                    "fallbackRate": c["trilateration"] / attempts if attempts else None},
              "navigation": nav,
              "accuracy": replay_metrics(replay),
              "usability": navigation_metrics(nav_segments, jump_threshold),
              "timeliness": timing_metrics(nav),
              "uturn": uturn_metrics(uturn_runs),
              "analysisConfig": {"metricVersion": METRIC_VERSION, "jumpThresholdM": jump_threshold,
                                 "uturnSameTimeMs": UTURN_SAME_TIME_MS,
                                 "uturnP90TargetMs": UTURN_P90_TARGET_MS,
                                 "export": info.get("analysisConfig")}}
    return (report, details) if with_details else report


def metric_rows(report):
    rows_out = []

    def add(dimension, task, method, view, metric, value):
        rows_out.append({"dimension": dimension, "task": task, "method": method,
                         "view": view, "metric": metric, "value": value})

    def flatten(dimension, method, data):
        for metric, value in data.items():
            if metric == "byMethod":
                continue
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, dict):
                        for sub, number in item.items():
                            add(dimension, "*", method, "all", f"{metric}.{key}.{sub}", number)
                    else:
                        add(dimension, "*", method, "all", f"{metric}.{key}", item)
            else:
                add(dimension, "*", method, "all", metric, value)

    for task, methods in report.get("accuracy", {}).get("tasks", {}).items():
        for method, views in methods.items():
            for view, values in views.items():
                for metric, value in values.items():
                    add("accuracy", task, method, view, metric, value)
    for metric in ("fingerprintHits", "fallbacksToB", "fallbackRate"):
        add("accuracy", "*", "C", "replay", metric, report.get("accuracy", {}).get("C", {}).get(metric))
    for dimension in ("usability", "timeliness"):
        section = report.get(dimension, {})
        flatten(dimension, "*", section)
        for method, data in section.get("byMethod", {}).items():
            flatten(dimension, method, data)
    uturn = report.get("uturn", {})
    if uturn:
        flatten("uturn", "*", {"summary": uturn.get("summary", {})})
        for method, data in uturn.get("byMethod", {}).items():
            flatten("uturn", method, data)
    return rows_out


def write_csv(report, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "dimension", "task", "method", "view", "metric", "value"])
        writer.writeheader()
        writer.writerows(metric_rows(report))


def write_details(details, directory):
    """把逐条中间数据写成 CSV，供人工复核与口径追溯。"""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    for name, fields in DETAIL_FIELDS.items():
        with open(target / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(details.get(name, []))


def _file_info(path):
    if not path:
        return None
    target = Path(path)
    digest = hashlib.sha256()
    with open(target, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(target), "bytes": target.stat().st_size, "sha256": digest.hexdigest()}


def _git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_dirty():
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=Path(__file__).resolve().parent.parent,
            text=True, stderr=subprocess.DEVNULL)
        return bool(output.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def write_manifest(path, *, archive, replay, jump_threshold, report, details=None,
                   details_dir=None, json_path=None, csv_path=None):
    """记录输入/输出哈希、工具版本与参数，便于复核本次运行是否为同一份数据。"""
    outputs = {"metricsJson": _file_info(json_path), "metricsCsv": _file_info(csv_path)}
    if details_dir:
        outputs["details"] = {name: _file_info(Path(details_dir) / f"{name}.csv")
                              for name in DETAIL_FIELDS}
    payload = {
        "schema": "pathai.field-analysis/1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": "tools/analyze_field_experiment.py", "metricVersion": METRIC_VERSION,
                 "scriptSha256": _file_info(__file__)["sha256"],
                 "gitCommit": _git_commit(), "gitDirty": _git_dirty(),
                 "python": platform.python_version()},
        "inputs": {"archive": _file_info(archive), "replay": _file_info(replay)},
        "parameters": {"jumpThresholdM": jump_threshold,
                       "export": report.get("analysisConfig", {}).get("export")},
        "counts": report.get("counts"),
        "outputs": outputs,
        "detailRows": {name: len(items) for name, items in details.items()} if details else None,
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--replay", help="batch replay JSON written by tools/replay_all_field_runs.py")
    parser.add_argument("--jump-threshold", type=float, default=DEFAULT_JUMP_THRESHOLD_M,
                        help="位置跳变判定距离（米），默认 3.0")
    parser.add_argument("--csv", help="把指标表另存为 CSV")
    parser.add_argument("--json", dest="json_path", help="把完整指标另存为 JSON")
    parser.add_argument("--details-dir", help="逐条中间数据 CSV 的输出目录")
    parser.add_argument("--manifest", help="运行清单（输入/输出哈希、参数、版本）的输出路径")
    args = parser.parse_args()
    result = analyze(args.archive, replay=args.replay, jump_threshold=args.jump_threshold,
                     with_details=bool(args.details_dir))
    if args.details_dir:
        report, details = result
        write_details(details, args.details_dir)
    else:
        report, details = result, None
    if args.csv:
        write_csv(report, args.csv)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.manifest:
        write_manifest(args.manifest, archive=args.archive, replay=args.replay,
                       jump_threshold=args.jump_threshold, report=report, details=details,
                       details_dir=args.details_dir, json_path=args.json_path, csv_path=args.csv)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
