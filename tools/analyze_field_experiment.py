"""Validate the experiment ZIP and independently score its recorded navigation runs."""
from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from math import hypot
from zipfile import ZipFile

FILES = {
    "CAL": "calibration_segments.jsonl", "FP": "fingerprint_segments.jsonl",
    "ST": "static_test_segments.jsonl", "DY": "dynamic_trajectories.jsonl",
    "NV": "nav_sessions.jsonl",
}
TURN_ACTIONS = {"turn_left", "turn_right", "u_turn"}


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


def analyze(path):
    with ZipFile(path) as bundle:
        if set(bundle.namelist()) != {*FILES.values(), "ground_truth.csv", "observer_records.csv", "manifest.json"}:
            raise ValueError("实验 ZIP 的八类产物不齐")
        info = json.loads(bundle.read("manifest.json"))
        configs = {c["configId"]: c for c in info["frozenConfigurations"]}
        ids = set()
        nav = []
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
            "navigation": nav}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    args = parser.parse_args()
    print(json.dumps(analyze(args.archive), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
