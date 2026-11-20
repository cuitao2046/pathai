import io
import json
import time
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from pathai_backend.fingerprint_library import ingest_export
from pathai_backend.map_repository import MapRepository

from pathai_backend.database import session_scope
from pathai_backend.models import (
    AnonymousSubject, CalibrationActivePointer, ClientPermission, LocationParamVersion,
)
from tools.analyze_field_experiment import analyze, turn_reference_timing

SITE = "school-building-01"
VERSION = "2026-10-25.1"
DAY = "2026-11-19"
FIRST_BATCH = "20261119-001"
START = int(datetime.fromisoformat(f"{DAY}T10:00:00+08:00").timestamp() * 1000)


def grant():
    with session_scope() as db:
        subject = db.query(AnonymousSubject).one()
        db.add(ClientPermission(subject_id=subject.id, is_tester=True, can_apply_config=True))
        db.commit()


def point(id):
    return dict(pointId=id, floorId="1", x=-60, y=-18, measuredOn=DAY,
                anchorA="wall-a", anchorB="wall-b", distanceA=1, distanceB=2,
                checkA=1, checkB=2)


def plan(task="FP", points=None):
    return dict(planId=f"plan-{task}", task=task, mapVersion=VERSION, day=DAY,
                points=points or [point("FP-1-C01")])


def segment(**extra):
    return dict(dict(segmentId="A-FP-FP-1-C01-N-1", runId="r-1", task="FP", batch=FIRST_BATCH,
                     pointId="FP-1-C01", part=1, orientation="N", startedAtEpoch=START,
                     endedAtEpoch=START + 30_000, mapVersion=VERSION, paramsVersion="p0",
                     deviceId="d-1", status="complete", planId="plan-FP",
                     rssi=[dict(t=START + i * 350, beacon="BK-01-001", rssi=-68)
                           for i in range(60)]), **extra)


def test_approval_queue_idempotency_and_export(client, auth_headers):
    assert client.post("/api/v1/field/plans", json=plan()).status_code == 401
    assert client.get("/api/v1/auth/me", headers=auth_headers).json()["isTester"] is False
    assert client.put("/api/v1/config/nav-policy", headers=auth_headers, json={
        "arrivalRadiusM": 2, "turnPromptDistanceM": 3,
    }).status_code == 403
    def post(route, body):
        return client.post(f"/api/v1/{route}", headers=auth_headers, json=body)
    assert post("field/plans", plan()).status_code == 200
    assert client.get(f"/api/v1/field/plans?day={DAY}", headers=auth_headers).json()["plans"][0]["planId"] == "plan-FP"
    assert post("field/segments", segment(pointId="outside")).status_code == 422
    assert post("field/segments", segment(endedAtEpoch=START + 24_990)).status_code == 400
    assert post("field/segments", segment()).json()["duplicate"] is False
    assert post("field/segments", segment()).json()["duplicate"] is True
    assert post("field/segments", segment(deviceId="other")).status_code == 409
    assert post("field/segments", segment(segmentId="trial-1", planId=None)).status_code == 200
    result = client.get("/api/v1/export/experiment", headers=auth_headers)
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as exported:
        assert len(exported.namelist()) == 8
        info = json.loads(exported.read("manifest.json"))
        assert info["counts"]["FP"] == 1
        analysis = info["analysisConfig"]
        assert analysis["metricVersion"] == "1.0"
        assert (analysis["suspendRadiusM"], analysis["jumpThresholdM"]) == (1.5, 3.0)
        assert analysis["expectedUpdateIntervalMs"] == {"standby": 8000, "preview": 2500, "navigating": 1000}
        assert analysis["matchThresholds"]["minOverlap"] == 3
        assert len(exported.read("fingerprint_segments.jsonl").splitlines()) == 1
        assert b"FP-1-C01" in exported.read("ground_truth.csv")


def test_shorter_fingerprint_window_keeps_sample_and_beacon_requirements(client, app, auth_headers):
    url = "/api/v1/field/segments"
    assert client.post("/api/v1/field/plans", headers=auth_headers, json=plan()).status_code == 200
    beacons = [b.beaconId for b in app.state.map_repository.latest(SITE).pkg.beacons
               if b.major is not None and b.minor is not None][:3]
    frames = [dict(t=START + 400 * i, beacon=beacons[i % 3], rssi=-65) for i in range(60)]
    def upload(duration, rssi):
        return client.post(url, headers=auth_headers, json=segment(endedAtEpoch=START + duration, rssi=rssi))
    assert upload(24_999, frames).status_code == 400
    assert upload(25_000, frames[:-1]).status_code == 400
    unknown = [{**f, "beacon": "unknown"} if f["beacon"] == beacons[2] else f for f in frames]
    assert upload(25_000, unknown).status_code == 422
    invalid = [{**f, "rssi": -110} if f["beacon"] == beacons[2] else f for f in frames]
    assert upload(25_000, invalid).status_code == 422
    assert upload(25_000, frames).status_code == 200


def test_route_checkpoint_order_and_undo(client, auth_headers):
    points = [point(f"CP{i}") for i in range(1, 9)]
    response = client.post("/api/v1/field/plans", headers=auth_headers, json=plan("DY", points))
    assert response.status_code == 200, response.text
    def upload(checkpoints):
        body = segment(task="DY", planId="plan-DY", segmentId="dy-01", orientation=None,
                       pointId=None, checkpoints=checkpoints)
        return client.post("/api/v1/field/segments", headers=auth_headers, json=body)
    assert upload([dict(t=START + 1000, pointId="CP2")]).status_code == 422
    records = [
        dict(t=START + 1000, pointId="CP1"),
        dict(t=START + 2300, pointId="CP1", undo=True),
        dict(t=START + 2800, pointId="CP1"),
        dict(t=START + 5000, pointId="CP2"),
    ]
    assert upload(records).status_code == 200


def test_t8_publishes_all_route_points_in_map_order(client, app, auth_headers, maps_dir):
    version = "2026-11-20.3"
    relative = Path(SITE, f"{version}.json")
    source = Path(__file__).resolve().parents[1] / "maps_data" / relative
    (maps_dir / relative).write_bytes(source.read_bytes())
    app.state.map_repository = MapRepository.load(maps_dir, "http://testserver")
    ids = ["CP-01", "CP-02", "P-03", "CP-03", "P-04", "CP-04",
           "P-05", "CP-05", "P-06", "CP-06", "CP-07", "CP-08"]
    package = app.state.map_repository.get(SITE, version).pkg
    survey = {p.checkpointId: p for p in package.fieldSurveyPoints if p.checkpointId}
    points = [{**point(id), "x": survey[id].truth[0], "y": survey[id].truth[1]} for id in ids]
    url = "/api/v1/field/plans"
    body = {**plan("NV", points), "mapVersion": version}
    assert client.post(url, headers=auth_headers, json={**body, "points": points[:-1]}).status_code == 400
    wrong = client.post(url, headers=auth_headers, json={**body, "points": points[::-1]})
    assert wrong.json()["error"]["details"]["expectedPointIds"] == ids
    response = client.post(url, headers=auth_headers, json=body)
    assert response.status_code == 200, response.text
    published = client.get(f"{url}?day={DAY}", headers=auth_headers).json()["plans"][0]["points"]
    assert [p["pointId"] for p in published] == ids


def test_turn_timing_uses_unified_checkpoints_and_legacy_marks():
    events = [
        {"kind": "turn_reference", "pointId": "CP-03", "announcementId": "P-03", "nodeId": "turn-3"},
        {"kind": "speech_started", "source": "nav:instruction:i3", "t": START + 800},
        {"kind": "turn_haptic", "instructionId": "i3", "t": START + 4000},
    ]
    instructions = {"i3": {"instruction": {"trigger": {"nodeId": "turn-3"}}}}
    p_mark = {"pointId": "P-03", "t": START + 1000}
    cp_mark = {"pointId": "CP-03", "t": START + 3700}
    unified = turn_reference_timing({"checkpoints": [p_mark, cp_mark]}, events, instructions)["CP-03"]
    assert unified["speech"]["deltaMs"] == -200
    assert unified["haptic"]["deltaMs"] == 300
    undo = {"pointId": "P-03", "t": START + 1200, "undo": True}
    undone = turn_reference_timing({"checkpoints": [p_mark, undo, cp_mark]}, events, instructions)
    assert undone["CP-03"]["speech"]["status"] == "reference_missing"
    legacy = {"kind": "announcement_mark", "announcementId": "P-03", "t": START + 900}
    assert turn_reference_timing({"checkpoints": [cp_mark]}, [*events, legacy], instructions
                                 )["CP-03"]["speech"]["deltaMs"] == -100


def test_t4_build_lists_current_batch_and_previous_revisions(client, auth_headers):
    build = "/api/v1/field/fingerprint-build"
    publish = "/api/v1/field/plans"
    query = f"{build}?mapVersion={VERSION}"
    assert client.get(query, headers=auth_headers).json() == {"plans": []}
    future = (datetime.now(timezone(timedelta(hours=8))).date() + timedelta(days=1)).isoformat()
    bodies = [
        {**plan(), "planId": "t4-first"},
        {**plan(points=[{**point("FP-1-C01"), "x": -59}]), "planId": "t4-revision"},
        {**plan(), "planId": "t4-future", "day": future},
        plan("DY", [point(f"CP{i}") for i in range(1, 9)]),
    ]
    batches = []
    for body in bodies:
        response = client.post(publish, headers=auth_headers, json=body)
        assert response.status_code == 200, response.text
        batches.append(response.json()["batch"])
    assert client.get(query, headers=auth_headers).json() == {"plans": [
        {"planId": "t4-revision", "batch": batches[1], "day": DAY, "pointCount": 1, "active": True},
        {"planId": "t4-first", "batch": batches[0], "day": DAY, "pointCount": 1, "active": False},
    ]}
    assert client.get(f"{build}?mapVersion=unpublished", headers=auth_headers).status_code == 409


def test_survey_version_and_candidate_coordinates_are_frozen_in_published_plan(client, auth_headers):
    original = {**point("FP-1-C01"), "surveyVersion": "base", "candidateX": -60, "candidateY": -18}
    corrected = {**original, "surveyVersion": "v1", "x": -59.8, "distanceA": 1.2}
    url = "/api/v1/field/plans"
    assert client.post(url, headers=auth_headers, json=plan(points=[original])).status_code == 200
    invalid = {**corrected, "surveyVersion": "base"}
    rejected = client.post(url, headers=auth_headers, json=plan(points=[invalid]))
    assert rejected.status_code == 400
    assert rejected.json()["error"]["details"]["field"] == "body.points.0"
    updated = {**plan(points=[corrected]), "planId": "plan-FP-revision"}
    assert client.post(url, headers=auth_headers, json=updated).status_code == 200
    items = client.get(f"{url}?day={DAY}", headers=auth_headers).json()["plans"]
    assert [row["planId"] for row in items] == ["plan-FP-revision"]
    archive = client.get("/api/v1/export/experiment", headers=auth_headers)
    with zipfile.ZipFile(io.BytesIO(archive.content)) as exported:
        items = json.loads(exported.read("manifest.json"))["plans"]
    assert [(row["points"][0]["surveyVersion"], row["points"][0]["x"]) for row in items] == [
        ("base", -60), ("v1", -59.8),
    ]


def test_daily_batch_number_is_assigned_once_even_under_concurrent_publication(client, auth_headers):
    url = "/api/v1/field/plans"

    def publish(body):
        response = client.post(url, headers=auth_headers, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    first = plan()
    rejected = client.post(url, headers=auth_headers, json=plan("CAL"))
    assert rejected.status_code == 400
    assert rejected.json()["error"]["details"] == {"field": "points", "expected": 12, "actual": 1}
    assert "当前已登记 1 个" in rejected.json()["error"]["message"]
    revisions = [{**first, "planId": f"new-{i}", "points": [point(f"FP-{i}")]} for i in (1, 2)]
    with ThreadPoolExecutor(max_workers=4) as workers:
        replies = list(workers.map(publish, [first, first, *revisions]))
    duplicates = [r for r in replies if r["recordId"] == first["planId"]]
    assert len({r["batch"] for r in duplicates}) == 1
    assert sum(r["duplicate"] for r in duplicates) == 1
    assert sorted({r["batch"] for r in replies}) == ["20261119-001", "20261119-002", "20261119-003"]
    legacy = {**first, "planId": "legacy-client", "batch": "manual", "points": [point("FP-legacy")]}
    assert publish(legacy)["batch"] == "20261119-004"
    unchanged = publish({**legacy, "planId": "copy", "day": "2026-11-20",
                         "points": [{**point("FP-legacy"), "measuredOn": "2026-11-20"}]})
    assert unchanged == {"recordId": "legacy-client", "batch": "20261119-004", "duplicate": True, "unchanged": True}
    assert publish({**first, "planId": "tomorrow", "day": "2026-11-20"})["batch"] == "20261120-001"


def test_published_plans_carry_forward_with_collection_day_progress(client, auth_headers):
    def post(path, body):
        return client.post(f"/api/v1/field/{path}", headers=auth_headers, json=body)

    tomorrow = "2026-11-20"
    fp = post("plans", plan())
    assert fp.status_code == 200
    st = post("plans", plan("ST", [point(f"ST-{i}") for i in range(18)]))
    assert st.status_code == 200
    fp_segment = segment()
    st_segment = segment(task="ST", planId="plan-ST", batch=st.json()["batch"], pointId="ST-0", segmentId="st-old")
    assert post("segments", fp_segment).status_code == 200
    assert post("segments", st_segment).status_code == 200

    day_ms = 86_400_000
    next_st = {**st_segment, "segmentId": "st-next", "runId": "st-next",
               "startedAtEpoch": START + day_ms, "endedAtEpoch": START + day_ms + 30_000,
               "rssi": [{**frame, "t": frame["t"] + day_ms} for frame in st_segment["rssi"]]}
    assert post("segments", next_st).status_code == 200
    before = {**next_st, "segmentId": "st-before", "startedAtEpoch": START - day_ms,
              "endedAtEpoch": START - day_ms + 30_000,
              "rssi": [{**frame, "t": frame["t"] - day_ms} for frame in st_segment["rssi"]]}
    assert post("segments", before).status_code == 422

    def get_plans(day):
        return client.get(f"/api/v1/field/plans?day={day}", headers=auth_headers).json()

    assert get_plans("2026-11-18") == {"plans": [], "progress": []}
    next_day = get_plans(tomorrow)
    assert {p["planId"] for p in next_day["plans"]} == {"plan-FP", "plan-ST"}
    assert {row["segmentId"] for row in next_day["progress"]} == {fp_segment["segmentId"], "st-next"}
    assert {row["segmentId"] for row in get_plans(DAY)["progress"]} == {fp_segment["segmentId"], "st-old"}
    revised = {**plan(points=[point("FP-02")]), "planId": "fp-revised", "day": tomorrow}
    assert post("plans", revised).status_code == 200
    assert {p["planId"] for p in get_plans(tomorrow)["plans"]} == {"fp-revised", "plan-ST"}
    assert {p["planId"] for p in get_plans(DAY)["plans"]} == {"plan-FP", "plan-ST"}


def test_short_candidate_samples_can_upload_without_relaxing_formal_duration(client, auth_headers):
    def post(path, body):
        return client.post(f"/api/v1/field/{path}", headers=auth_headers, json=body)

    assert post("plans", plan()).status_code == 200
    raw = [dict(t=START + 1000, beacon="BK-01-001", rssi=-68)]
    for task in ("CAL", "FP", "ST"):
        record = segment(task=task, segmentId=f"trial-{task}", planId=None,
                         pointId=f"{task}-01", orientation=None if task == "CAL" else "N",
                         rssi=raw, endedAtEpoch=START + 10_000)
        assert post("segments", {**record, "endedAtEpoch": START + 9999}).status_code == 400
        assert post("segments", {**record, "rssi": []}).status_code == 400
        result = post("segments", record)
        assert result.status_code == 200, result.text
    formal = segment(endedAtEpoch=START + 10_000,
                     rssi=[dict(t=START + i * 100, beacon="BK-01-001", rssi=-68) for i in range(60)])
    assert post("segments", formal).status_code == 400


def test_freeze_and_c_fallback_use_b_even_after_active_params_change(client, app, auth_headers, tmp_path, monkeypatch):
    app.state.settings = replace(app.state.settings, fingerprint_min_margin_db=1.25)
    latest = app.state.map_repository.latest(SITE)
    assert latest.map_version == VERSION
    beacons = [b for b in latest.pkg.beacons if b.major is not None][:3]
    assert len(beacons) == 3
    with session_scope() as db:
        db.add(LocationParamVersion(
            version="field-B", site_id=SITE, rssi_at_1m=-65, path_loss_n=2.5,
            residual_reliable_m=3, residual_degraded_m=6, grid_version="grid",
            run_id="sample-run", source="test", created_at_epoch=int(time.time()),
        ))
        db.add(CalibrationActivePointer(site_id=SITE, param_version="field-B",
                                       grid_version="grid", updated_at_epoch=int(time.time())))
        db.commit()
    content = {"type": "ble_fingerprint_collection", "schemaVersion": "1.3.0", "records": [{
        "fpId": "FP-TEST", "floor": "1", "coordinates": [-60, -18],
        "collectedAt": datetime.now(timezone.utc).isoformat(),
        "beacons": [{"major": b.major, "minor": b.minor, "rssi": -50} for b in beacons],
    }]}
    content["records"].append({
        **content["records"][0], "fpId": "FP-OTHER", "coordinates": [-65, -18],
        "beacons": [{"major": b.major, "minor": b.minor, "rssi": -54} for b in beacons],
    })
    with session_scope() as db:
        ingest_export(db, file_name="field.json", content=json.dumps(content))
        db.commit()
    response = client.post("/api/v1/field/configurations", headers=auth_headers,
                           json={"configId": "field-v1", "mapVersion": VERSION})
    assert response.status_code == 200, response.text
    app.state.settings = replace(app.state.settings, fingerprint_min_margin_db=10)
    assert client.get("/api/v1/field/configurations", headers=auth_headers).json()["configurations"][0]["C"]["minMargin"] == 1.25
    with session_scope() as db:
        db.delete(db.get(CalibrationActivePointer, SITE))
        db.commit()
    app.state.runtime_params.invalidate()
    assert client.get("/api/v1/config/location-params").json()["paramsVersion"] != "field-B"
    now = datetime.now(timezone.utc).isoformat()
    def estimate(method, sequence, rssi):
        result = client.post("/api/v1/locations/estimate", headers=auth_headers, json={
            "siteId": SITE, "mapVersion": VERSION, "sequence": sequence, "collectedAt": now,
            "experimentConfigId": "field-v1", "experimentMethod": method,
            "beacons": [{"beaconId": b.beaconId, "rssi": rssi, "observedAt": now} for b in beacons],
        })
        assert result.status_code == 200, result.text
        return result.json()
    a = estimate("A", 1, -90)
    b = estimate("B", 2, -90)
    c = estimate("C", 3, -90)
    hit = estimate("C", 4, -50)
    assert a["paramsVersion"] != b["paramsVersion"] == c["paramsVersion"] == "field-B"
    assert c["positionSource"] == "trilateration" and c["fallbackReason"]
    assert c["position"] == b["position"]
    assert hit["positionSource"] == "fingerprint" and hit["fallbackReason"] is None

    nv_plan = plan("NV", [point(f"CP{i}") for i in range(1, 9)])
    assert client.post("/api/v1/field/plans", headers=auth_headers, json=nv_plan).status_code == 200
    start_node = next(n for n in latest.pkg.floors["1"].topology.nodes if n.id == "F1-TR-0016")
    assert start_node.coordinates
    nav = client.post("/api/v1/nav/sessions", headers={**auth_headers, "Idempotency-Key": "field-test"},
                      json={"siteId": SITE, "mapVersion": VERSION, "destinationId": "F1-RM-0003",
                            "experiment": {"configId": "field-v1", "runId": "nav-run", "planId": nv_plan["planId"], "method": "C"},
                            "origin": {"x": start_node.coordinates[0], "y": start_node.coordinates[1],
                                       "floorId": "1", "confidence": 0.95, "observedAt": now}})
    assert nav.status_code == 201, nav.text
    created = nav.json()
    assert created["paramsVersion"] == "field-B"
    record = segment(task="NV", segmentId="A-NV-C-1", runId="nav-run", planId=nv_plan["planId"],
                     pointId=None, orientation=None, configId="field-v1", method="C",
                     checkpoints=[dict(t=START + 1200, pointId="CP1")],
                     rssi=[dict(t=START + 1250, beacon=b.beaconId, rssi=-50) for b in beacons],
                     paramsVersion="field-B", navigation=[
                         {"kind": "session", "t": START, "sessionId": created["sessionId"], "route": created["route"]},
                         {"kind": "estimate", "t": START + 1000, "source": "fingerprint", "position": hit["position"]},
                         {"kind": "estimate", "t": START + 2000, "source": "trilateration", "fallbackReason": "rmse_too_large"},
                         {"kind": "end", "t": START + 2500, "byArrival": True},
                         {"kind": "arrival_manual", "t": START + 3500},
                     ])
    uploaded = client.post("/api/v1/field/segments", headers=auth_headers, json=record)
    assert uploaded.status_code == 200, uploaded.text
    archive = client.get("/api/v1/export/experiment", headers=auth_headers)
    with zipfile.ZipFile(io.BytesIO(archive.content)) as exported:
        counts = json.loads(exported.read("manifest.json"))["cEstimateCounts"]
        assert counts == {"fingerprintHits": 1, "fallbacksToB": 1, "unavailable": 0}
    zip_path = tmp_path / "field.zip"
    zip_path.write_bytes(archive.content)
    report = analyze(zip_path)
    assert report["C"]["fallbackRate"] == 0.5
    assert report["navigation"][0]["arrival"]["deltaMs"] == -1000
    from pathai_backend.routers import calibration
    monkeypatch.delitem(calibration._LAST_WRITE, "field_replay", raising=False)
    replay = client.post("/api/v1/field/replay", headers=auth_headers, json={"runId": "nav-run"})
    assert replay.status_code == 200, replay.text
    paired = replay.json()
    assert (paired["task"], paired["configId"]) == ("NV", "field-v1")
    assert paired["missingPointIds"] == [f"CP{i}" for i in range(2, 9)]
    # 挂起库：与 CP1 真值重合的 FP 点被排除，生产库不改动
    assert (paired["suspendRadiusM"], paired["suspendedFpIds"]) == (1.5, ["FP-TEST"])
    matched = paired["checkpoints"][0]["methods"]["C"]
    assert (matched["source"], matched["errorM"]) == ("fingerprint", 5.0)
    assert (matched["rmseDb"], matched["overlap"], matched["confidence"]) == (4.0, 3, 0.8)
    assert matched["marginDb"] is None

    monkeypatch.delitem(calibration._LAST_WRITE, "field_replay")
    baseline = client.post("/api/v1/field/replay", headers=auth_headers,
                           json={"runId": "nav-run", "suspendRadiusM": 0})
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["suspendedFpIds"] == []
    assert baseline.json()["checkpoints"][0]["methods"]["C"]["errorM"] == 0

    st_points = [point(f"ST-{index:02}") for index in range(18)]
    st_plan = plan("ST", st_points)
    st_published = client.post("/api/v1/field/plans", headers=auth_headers, json=st_plan)
    assert st_published.status_code == 200, st_published.text
    weak = [dict(t=START + index * 500, beacon=beacon.beaconId, rssi=-90)
            for index in range(60) for beacon in beacons]
    st_record = segment(task="ST", pointId=st_points[0]["pointId"], orientation="N", part=1,
                        planId=st_plan["planId"], batch=st_published.json()["batch"],
                        segmentId="A-ST-1", runId="static-run", endedAtEpoch=START + 30_000,
                        rssi=weak)
    assert client.post("/api/v1/field/segments", headers=auth_headers, json=st_record).status_code == 200
    monkeypatch.delitem(calibration._LAST_WRITE, "field_replay")
    static = client.post("/api/v1/field/replay", headers=auth_headers,
                         json={"configId": "field-v1", "runId": "static-run"})
    assert static.status_code == 200, static.text
    st_body = static.json()
    assert (st_body["task"], st_body["trial"]) == ("ST", False)
    assert [(item["pointId"], item["orientation"], item["timeSource"])
            for item in st_body["checkpoints"]] == [("ST-00", "N", "segment")]
    static_c = st_body["checkpoints"][0]["methods"]["C"]
    assert (static_c["source"], static_c["fallbackReason"]) == ("trilateration", "rmse_too_large")
    assert (static_c["diagnostics"]["best"]["fpId"], static_c["diagnostics"]["excludedCount"]) == ("FP-OTHER", 1)
    assert st_body["missingPointIds"] == [entry["pointId"] for entry in st_points[1:]]

    monkeypatch.delitem(calibration._LAST_WRITE, "field_replay_batch", raising=False)
    batch = client.post("/api/v1/field/replay/batch", headers=auth_headers,
                        json={"configId": "field-v1", "runIds": ["nav-run", "static-run", "missing-run"]})
    assert batch.status_code == 200, batch.text
    items = batch.json()["results"]
    assert [item.get("task") for item in items] == ["NV", "ST", None]
    assert items[2]["error"]["code"] == "FIELD_RUN_NOT_FOUND"

    dy_plan = plan("DY", [point(f"CP{i}") for i in range(1, 9)])
    dy_published = client.post("/api/v1/field/plans", headers=auth_headers, json=dy_plan)
    assert dy_published.status_code == 200
    recorded = segment(task="DY", pointId=None, orientation=None, planId=dy_plan["planId"],
                       batch=dy_published.json()["batch"],
                       segmentId="A-DY-1", runId="trial-run", checkpoints=[dict(t=START + 1200, pointId="CP1")],
                       rssi=[dict(t=START + 1250, beacon=b.beaconId, rssi=-50) for b in beacons])
    assert client.post("/api/v1/field/segments", headers=auth_headers, json=recorded).status_code == 200
    monkeypatch.delitem(calibration._LAST_WRITE, "field_replay")
    replay = client.post("/api/v1/field/replay", headers=auth_headers,
                         json={"configId": "field-v1", "runId": "trial-run"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["checkpoints"][0]["methods"]["C"]["errorM"] == 0

    replay_path = tmp_path / "replay.json"
    replay_path.write_text(json.dumps({"results": [paired, st_body]}), encoding="utf-8")
    metrics = analyze(zip_path, replay_path)
    assert metrics["accuracy"]["tasks"]["ST"]["C"]["all"]["n"] == 1
    assert metrics["accuracy"]["tasks"]["ST"]["C"]["hitOnly"]["n"] == 0
    assert metrics["accuracy"]["tasks"]["NV"]["C"]["hitOnly"]["mean"] == 5.0
    assert metrics["accuracy"]["C"]["fallbackRate"] == 0.5
    assert (metrics["usability"]["estimates"], metrics["usability"]["positionedEstimates"]) == (2, 1)
    assert metrics["usability"]["validLocationRate"] == 0.5
    assert metrics["usability"]["updateIntervalMs"]["n"] == 1
    assert metrics["timeliness"]["arrivalDetectionDeltaMs"]["n"] == 1
    assert metrics["analysisConfig"]["metricVersion"] == "1.0"


def test_raw_collection_feeds_existing_fit_and_library(client, app, auth_headers, tmp_path):
    app.state.settings = replace(app.state.settings, calibration_data_dir=tmp_path / "calibration")
    latest = app.state.map_repository.latest(SITE)
    beacon_ids = [b.beaconId for b in latest.pkg.beacons if b.major is not None][:3]
    frames = [dict(t=START + i * 200, beacon=beacon_ids[i % 3], rssi=-65 - i % 3) for i in range(60)]
    def post(route, body):
        response = client.post(f"/api/v1/field/{route}", headers=auth_headers, json=body)
        assert response.status_code == 200, response.text
        return response.json()
    cal_points = [point(f"CAL-{i:02}") for i in range(1, 13)]
    post("plans", plan("CAL", cal_points))
    cal_frames = [
        {**frame, "t": frame["t"] + window * 40_000, "rssi": frame["rssi"] - window * 5}
        for window in range(3) for frame in frames
    ]
    for index, entry in enumerate(cal_points):
        part = 2 if index == 0 else 1  # A complete legacy segment can be reused.
        post("segments", segment(task="CAL", planId="plan-CAL", pointId=entry["pointId"],
                                 segmentId=f'{entry["pointId"]}-{part}', part=part, orientation=None,
                                 endedAtEpoch=START + 120_000, rssi=cal_frames))
    post("segments", segment(task="CAL", planId="plan-CAL", pointId="CAL-02",
                             segmentId="CAL-02-2", part=2, orientation=None,
                             endedAtEpoch=START + 120_000, rssi=frames))
    sample = post("calibration-sample", {"mapVersion": VERSION, "planIds": ["plan-CAL"]})
    assert client.get(f"/api/v1/calibration/samples/{sample['sampleId']}", headers=auth_headers).status_code == 403
    grant()
    lookup = client.get(f"/api/v1/calibration/samples/{sample['sampleId']}", headers=auth_headers)
    assert lookup.json()["deployable"] is True
    payload = json.loads(next((tmp_path / "calibration" / "samples").glob("*.json")).read_text(encoding="utf-8"))
    assert payload["windowMs"] == 40_000
    assert sum(len(p["sessions"]) for p in payload["points"]) == 36
    assert all(len(session["observations"]) == 3 for p in payload["points"] for session in p["sessions"])
    assert [session["observations"][0]["rssiMedian"] for session in payload["points"][1]["sessions"]] == [
        -65, -70, -75,
    ]
    assert [session["observations"][0]["n"] for session in payload["points"][1]["sessions"]] == [20, 20, 20]

    fp_plan = post("plans", plan())
    for step, direction in enumerate("NESW", 1):
        offset = 32_000 * (step - 1)
        duration = 25_000 if step % 2 else 30_000
        post("segments", segment(segmentId=f"fp-{direction}", part=step, orientation=direction, batch=fp_plan["batch"],
                                 startedAtEpoch=START + offset, endedAtEpoch=START + offset + duration,
                                 rssi=[{**f, "t": f["t"] + offset} for f in frames]))
    built = post("fingerprint-build", {"mapVersion": VERSION, "planIds": ["plan-FP"]})
    assert built["active"] and built["recordCount"] == 4 and built["fpCount"] == 1
