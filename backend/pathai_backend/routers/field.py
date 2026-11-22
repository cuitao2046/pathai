"""Field experiment collection, plan publication, and portable export."""
from __future__ import annotations

import csv
from collections import defaultdict
import io
import json
from math import hypot, isinf
import statistics
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import BigInteger, String, cast, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pathai_backend import calibration_service, fingerprint_library
from pathai_backend.auth import get_current_user, get_db_session, get_settings
from pathai_backend.config import Settings
from pathai_backend.errors import ApiError
from pathai_backend.location_solver import BeaconInput, LocationParams, estimate_location
from pathai_backend.models import (
    CalibrationActivePointer, FieldRecord, FingerprintLibraryPointer,
    FingerprintLibraryVersion, LocationParamVersion, NavSession,
)
from pathai_backend.routers.calibration import _rate_limit, require_private_network
from pathai_backend.schemas.location import BeaconObservation

def require_field_user(
    request: Request,
    db: Session = Depends(get_db_session),
    authorization: str | None = Header(default=None),
) -> None:
    request.state.operator = get_current_user(request, db, authorization).user_id


router = APIRouter(prefix="/api/v1", tags=["field-experiment"],
                   dependencies=[Depends(require_private_network), Depends(require_field_user)])

RecordId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
Version = Annotated[str, Field(min_length=1, max_length=80)]
Task = Literal["CAL", "FP", "ST", "DY", "NV", "UT"]
Orientation = Literal["N", "E", "S", "W"]
SITE = "school-building-01"
FIELD_TIMEZONE = timezone(timedelta(hours=8))
CALIBRATION_DURATION_MS = 120_000
CALIBRATION_WINDOW_MS = 40_000
FINGERPRINT_MIN_DURATION_MS = 25_000
FINGERPRINT_LEGACY_DURATION_MS = 30_000
#: 重放时的「挂起库」半径：距 ST/T8 测试点过近的 FP 点会被排除，避免精度虚高。
DEFAULT_SUSPEND_RADIUS_M = 1.5
#: 汇总口径（写入导出 manifest，便于分析脚本与报告口径一致）。
ANALYSIS_METRIC_VERSION = "1.0"
ANALYSIS_JUMP_THRESHOLD_M = 3.0
PLAN_POINT_COUNTS = {"CAL": 12, "ST": 18, "DY": 8, "NV": 8, "UT": 1}
PLAN_TASK_LABELS = {"CAL": "T3 参数标定", "ST": "T6 静态对比", "DY": "T7 行走打点", "NV": "T8 导航对比",
                    "UT": "T9 反向掉头时效"}
FILES = {
    "CAL": "calibration_segments.jsonl", "FP": "fingerprint_segments.jsonl",
    "ST": "static_test_segments.jsonl", "DY": "dynamic_trajectories.jsonl",
    "NV": "nav_sessions.jsonl", "UT": "uturn_sessions.jsonl",
}


class Point(BaseModel):
    pointId: RecordId
    floorId: Literal["1", "2"]
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    measuredOn: date
    anchorA: str = Field(min_length=1, max_length=250)
    anchorB: str = Field(min_length=1, max_length=250)
    distanceA: float = Field(gt=0, le=150)
    distanceB: float = Field(gt=0, le=150)
    checkA: float = Field(gt=0, le=150)
    checkB: float = Field(gt=0, le=150)
    note: str = Field(default="", max_length=500)
    surveyVersion: str | None = Field(default=None, pattern=r"^(base|v[1-9]\d{0,8})$")
    candidateX: float | None = Field(default=None, allow_inf_nan=False)
    candidateY: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def verify_survey_version(self):
        if self.surveyVersion is not None and (self.candidateX is None or self.candidateY is None):
            raise ValueError("survey version requires candidate coordinates")
        if self.surveyVersion == "base" and (self.x, self.y) != (self.candidateX, self.candidateY):
            raise ValueError("base coordinates must equal the candidate coordinates")
        return self


class TaskPlan(BaseModel):
    planId: RecordId
    task: Task
    day: date
    mapVersion: Version
    points: list[Point] = Field(min_length=1, max_length=256)


class FreezeRequest(BaseModel):
    configId: RecordId
    mapVersion: Version


class PublishSamplesRequest(BaseModel):
    mapVersion: Version
    planIds: list[RecordId] = Field(min_length=1, max_length=30)


class ReplayRequest(BaseModel):
    configId: RecordId | None = None
    runId: RecordId
    suspendRadiusM: float = Field(default=DEFAULT_SUSPEND_RADIUS_M, ge=0, le=50)


class ReplayBatchRequest(BaseModel):
    configId: RecordId | None = None
    runIds: list[RecordId] = Field(min_length=1, max_length=50)
    suspendRadiusM: float = Field(default=DEFAULT_SUSPEND_RADIUS_M, ge=0, le=50)


class Frame(BaseModel):
    t: int = Field(gt=0)
    beacon: RecordId
    rssi: int = Field(ge=-127, le=-1)


class Checkpoint(BaseModel):
    t: int = Field(gt=0)
    pointId: RecordId
    undo: bool = False


class Segment(BaseModel):
    segmentId: RecordId
    runId: RecordId
    planId: RecordId | None = None
    task: Task
    batch: RecordId
    pointId: RecordId | None = None
    orientation: Orientation | None = None
    part: int = Field(default=1, ge=1, le=100)
    startedAtEpoch: int = Field(gt=0, le=4_102_444_800_000)
    endedAtEpoch: int = Field(gt=0, le=4_102_444_800_000)
    mapVersion: Version
    paramsVersion: Version
    deviceId: RecordId
    method: Literal["A", "B", "C", "RAW"] = "RAW"
    configId: RecordId | None = None
    status: Literal["complete", "suspect", "aborted"]
    reason: str = Field(default="", max_length=200)
    retryOf: RecordId | None = None
    rssi: list[Frame] = Field(max_length=30000)
    checkpoints: list[Checkpoint] = Field(default_factory=list, max_length=50)
    navigation: list[dict] = Field(default_factory=list, max_length=4000)

    @model_validator(mode="after")
    def verify_record(self):
        elapsed = self.endedAtEpoch - self.startedAtEpoch
        if not 0 <= elapsed <= 30 * 60_000:
            raise ValueError("invalid segment duration")
        if any(not self.startedAtEpoch <= item.t <= self.endedAtEpoch
               for item in [*self.rssi, *self.checkpoints]):
            raise ValueError("sample outside segment")
        if self.task in ("FP", "ST") and (not self.orientation or not self.pointId):
            raise ValueError("point and orientation are required")
        if self.task in ("FP", "ST") and self.orientation != ("N", "E", "S", "W")[(self.part - 1) % 4]:
            raise ValueError("orientation and segment number disagree")
        if self.task in ("FP", "ST") and self.part > 4:
            raise ValueError("invalid orientation segment")
        if self.task == "CAL" and (not self.pointId or self.part > 3):
            raise ValueError("point and calibration segment number are required")
        required_duration = {"CAL": CALIBRATION_DURATION_MS, "FP": FINGERPRINT_MIN_DURATION_MS, "ST": 30_000}.get(self.task)
        if self.status == "complete" and required_duration is not None:
            if self.planId is None:
                required_duration = 10_000
            minimum = 60 if self.planId is not None and self.task in ("FP", "ST") else 1
            if elapsed < required_duration or len(self.rssi) < minimum:
                raise ValueError("completed window is shorter than required or has too few samples")
        if self.task in ("NV", "UT") and (self.method == "RAW" or not self.configId or not self.planId):
            raise ValueError("navigation must use a frozen method and approved plan")
        if self.task == "UT" and self.method not in ("B", "C"):
            raise ValueError("uturn experiment only supports methods B and C")
        return self


class FieldNote(BaseModel):
    noteId: RecordId
    runId: RecordId | None = None
    pointId: RecordId | None = None
    t: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=1000)
    cleared: bool = False
    isArrival: bool = False
    planId: RecordId | None = None
    day: date | None = None
    kind: Literal["observer", "skip"] = "observer"
    turnRating: Literal["on_time", "early", "late", "missed"] | None = None


class Inspection(BaseModel):
    inspectionId: RecordId
    beaconId: RecordId
    mapVersion: Version
    t: int = Field(gt=0)
    action: Literal["installed", "checked", "removed", "issue"]
    location: str = Field(min_length=1, max_length=250)
    mount: str = Field(default="", max_length=80)
    rssi: int | None = Field(default=None, ge=-127, le=-1)
    lastSeen: int | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=500)


def _write(db: Session, request: Request, kind: str, key: str, payload: dict) -> dict:
    operator = request.state.operator
    packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    row = FieldRecord(kind=kind, record_id=key, operator=operator, payload=packed)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = db.get(FieldRecord, (kind, key))
        if previous is None:
            raise
        if (previous.operator, previous.payload) != (operator, packed):
            raise ApiError(409, "FIELD_RECORD_CONFLICT", "记录编号已被不同内容使用")
        return {"recordId": key, "duplicate": True}
    return {"recordId": key, "duplicate": False}


def _rows(db: Session, kind: str):
    for row in db.scalars(select(FieldRecord).where(FieldRecord.kind == kind)
                          .order_by(FieldRecord.record_id)):
        yield {**json.loads(row.payload), "operatorUserId": row.operator}


def frozen_config(db: Session, config_id: str, map_version: str) -> dict:
    row = db.get(FieldRecord, ("config", config_id))
    if row is None:
        raise ApiError(422, "INVALID_FIELD_CONFIG", "找不到冻结配置")
    config = json.loads(row.payload)
    if config["mapVersion"] != map_version:
        raise ApiError(409, "INVALID_FIELD_CONFIG", "当前地图与冻结配置不一致")
    return config


def frozen_location_params(config: dict, method: str) -> LocationParams:
    profile = config["A" if method == "A" else "B"]
    return LocationParams(
        rssi_at_1m=profile["rssiAt1m"], path_loss_n=profile["pathLossN"],
        residual_reliable_m=profile["reliableM"], residual_degraded_m=profile["degradedM"],
        params_version=profile["paramsVersion"],
    )


def require_plan(db: Session, plan_id: str, task: str, map_version: str) -> dict:
    row = db.get(FieldRecord, ("plan", plan_id))
    plan = json.loads(row.payload) if row is not None else None
    if plan is None or (plan["task"], plan["mapVersion"]) != (task, map_version):
        raise ApiError(422, "INVALID_FIELD_PLAN", "采集内容与正式任务清单不一致")
    return plan


def _plan_revision(plan: dict) -> tuple[str, int, str]:
    prefix = plan["day"].replace("-", "") + "-"
    suffix = plan["batch"].removeprefix(prefix)
    sequence = int(suffix) if suffix != plan["batch"] and suffix.isascii() and suffix.isdecimal() else 0
    return plan["day"], sequence, plan["planId"]


def _active_plans(db: Session, day: str) -> dict[tuple[str, str], dict]:
    active = {}
    for plan in _rows(db, "plan"):
        if plan["day"] > day:
            continue
        key = plan["task"], plan["mapVersion"]
        if key not in active or _plan_revision(plan) > _plan_revision(active[key]):
            active[key] = plan
    return active


def _segment_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=FIELD_TIMEZONE).date().isoformat()


def _truth_data(points: list[dict]) -> list[dict]:
    return [{key: value for key, value in point.items() if key != "measuredOn"} for point in points]


@router.get("/field/clock")
def clock():
    return {"serverTimeMs": time.time_ns() // 1_000_000}


@router.get("/field/plans")
def plans(day: date, db: Session = Depends(get_db_session)):
    day_key = day.isoformat()
    scheduled = list(_active_plans(db, day_key).values())
    plan_ids = {p["planId"] for p in scheduled}
    fields = ("segmentId", "runId", "planId", "task", "batch", "pointId", "orientation",
              "part", "status", "startedAtEpoch", "mapVersion")
    progress = []
    for row in _rows(db, "segment"):
        if row["planId"] not in plan_ids:
            continue
        started = _segment_day(row["startedAtEpoch"])
        if started == day_key or (started < day_key and row["task"] in ("CAL", "FP")):
            progress.append({key: row[key] for key in fields})
    return {"plans": scheduled, "progress": progress}


@router.get("/field/fingerprint-makeup")
def fingerprint_makeup(day: date, mapVersion: Version, db: Session = Depends(get_db_session)):
    day_key = day.isoformat()
    plan = _active_plans(db, day_key).get(("FP", mapVersion))
    if plan is None or plan["day"] >= day_key:
        return {"points": []}
    collected = set()
    for row in _rows(db, "segment"):
        if row["status"] == "complete" and row["planId"] == plan["planId"] and _segment_day(row["startedAtEpoch"]) <= day_key:
            collected.add((row["pointId"], row["part"]))
    points = (p["pointId"] for p in plan["points"])
    return {"points": [point for point in points if any((point, part) not in collected for part in range(1, 5))]}


@router.post("/field/plans")
def publish_plan(body: TaskPlan, request: Request, db: Session = Depends(get_db_session)):
    count = len(body.points)
    package = request.app.state.map_repository.get(SITE, body.mapVersion)
    if package is None:
        raise ApiError(422, "INVALID_FIELD_PLAN", "任务清单必须绑定已发布地图")
    survey_points = package.pkg.fieldSurveyPoints or []
    ordered_ids = (
        [point.checkpointId for point in survey_points
         if point.checkpointId and point.uturnCheckpoint]
        if body.task == "UT" else
        [point.checkpointId for point in survey_points
         if point.checkpointId and not point.uturnCheckpoint]
        if body.task == "NV" and any(point.checkpointOnly for point in survey_points) else []
    )
    expected = len(ordered_ids) if ordered_ids else PLAN_POINT_COUNTS.get(body.task)
    if expected is not None and count != expected:
        raise ApiError(400, "INVALID_REQUEST",
                       f"{PLAN_TASK_LABELS[body.task]}须登记 {expected} 个点，当前已登记 {count} 个",
                       details={"field": "points", "expected": expected, "actual": count})
    if len({point.pointId for point in body.points}) != count:
        raise ApiError(400, "INVALID_REQUEST", "清单中有重复点位，请检查真值登记",
                       details={"field": "points"})
    if ordered_ids and [point.pointId for point in body.points] != ordered_ids:
        raise ApiError(400, "INVALID_REQUEST",
                       f"请按地图中的 {PLAN_TASK_LABELS[body.task]} 检查点顺序发布清单",
                       details={"field": "points", "expectedPointIds": ordered_ids})
    payload = body.model_dump(mode="json")
    # Keep the original representation for unversioned points so older
    # clients can still safely retry an already published plan.
    for point in payload["points"]:
        if point["surveyVersion"] is None:
            for key in ("surveyVersion", "candidateX", "candidateY"):
                point.pop(key)
    operator = request.state.operator

    def existing_batch(row: FieldRecord) -> dict:
        existing = json.loads(row.payload)
        batch = existing.pop("batch")
        if row.operator != operator or existing != payload:
            raise ApiError(409, "FIELD_RECORD_CONFLICT", "记录编号已被不同内容使用")
        return {"recordId": body.planId, "duplicate": True, "batch": batch}

    previous = db.get(FieldRecord, ("plan", body.planId))
    if previous is not None:
        return existing_batch(previous)

    day_key = body.day.isoformat().replace("-", "")
    table = FieldRecord.__table__
    # On first use, continue past any matching batch already published by an older client.
    prior = 0
    if db.get(FieldRecord, ("batch", day_key)) is None:
        prefix = day_key + "-"
        for plan in _rows(db, "plan"):
            batch = plan["batch"]
            suffix = batch[len(prefix):]
            if plan["day"] == payload["day"] and batch.startswith(prefix) and suffix.isascii() and suffix.isdecimal():
                prior = max(prior, int(suffix))

    if db.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert

    # Increment and publish in the same transaction. Retrying an already
    # published plan does not advance the sequence, even under concurrency.
    counter = insert(table).values(kind="batch", record_id=day_key, operator=operator, payload=str(prior + 1))
    counter = counter.on_conflict_do_update(
        index_elements=[table.c.kind, table.c.record_id],
        set_={"payload": cast(cast(table.c.payload, BigInteger) + 1, String)},
    ).returning(table.c.payload)
    try:
        sequence = int(db.execute(counter).scalar_one())
        previous = db.get(FieldRecord, ("plan", body.planId))
        if previous is not None:
            result = existing_batch(previous)
            db.rollback()
            return result
        active = _active_plans(db, payload["day"]).get((body.task, body.mapVersion))
        # A fresh measurement date by itself doesn't change a point's truth data.
        if active and _truth_data(active["points"]) == _truth_data(payload["points"]):
            db.rollback()
            return {"recordId": active["planId"], "duplicate": True, "batch": active["batch"], "unchanged": True}
        batch = f"{day_key}-{sequence:03d}"
        packed = json.dumps({**payload, "batch": batch}, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False)
        db.add(FieldRecord(kind="plan", record_id=body.planId, operator=operator, payload=packed))
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = db.get(FieldRecord, ("plan", body.planId))
        if previous is None:
            raise
        return existing_batch(previous)
    return {"recordId": body.planId, "duplicate": False, "batch": batch}


@router.get("/field/configurations")
def configurations(db: Session = Depends(get_db_session)):
    return {"configurations": list(_rows(db, "config"))}


@router.post("/field/configurations")
def freeze(body: FreezeRequest, request: Request, db: Session = Depends(get_db_session)):
    existing = db.get(FieldRecord, ("config", body.configId))
    if existing:
        return _write(db, request, "config", body.configId, frozen_config(db, body.configId, body.mapVersion))
    latest = request.app.state.map_repository.latest(SITE)
    if latest is None or latest.map_version != body.mapVersion:
        raise ApiError(409, "INVALID_FIELD_CONFIG", "请在最新地图上冻结配置")
    pointer = db.get(CalibrationActivePointer, SITE)
    fitted = db.get(LocationParamVersion, pointer.param_version) if pointer else None
    library_pointer = db.get(FingerprintLibraryPointer, SITE)
    library = db.get(FingerprintLibraryVersion, library_pointer.version) if library_pointer else None
    if not fitted or not library or library.details_pruned:
        raise ApiError(422, "INVALID_FIELD_CONFIG", "请先应用现场标定参数，并完成指纹入库")
    if library.map_version and library.map_version != body.mapVersion:
        raise ApiError(409, "INVALID_FIELD_CONFIG", "指纹库与待冻结地图版本不一致")
    base = request.app.state.settings
    settings = request.app.state.runtime_params.get()
    return _write(db, request, "config", body.configId, {
        "configId": body.configId, "mapVersion": body.mapVersion, "frozenAt": time.time_ns() // 1_000_000,
        "A": {"rssiAt1m": base.location_rssi_at_1m, "pathLossN": base.location_path_loss_n,
              "reliableM": base.location_residual_reliable_m, "degradedM": base.location_residual_degraded_m,
              "paramsVersion": base.location_params_version},
        "B": {"rssiAt1m": fitted.rssi_at_1m, "pathLossN": fitted.path_loss_n,
              "reliableM": fitted.residual_reliable_m, "degradedM": fitted.residual_degraded_m,
              "paramsVersion": fitted.version},
        "C": {"libraryVersion": library.version, "fallback": "B",
              "minOverlap": fingerprint_library.MATCH_MIN_OVERLAP,
              "maxRmse": fingerprint_library.MATCH_MAX_RMSE_DB,
              "confidenceRmse": fingerprint_library.MATCH_CONFIDENCE_RMSE_DB,
              "minConfidence": fingerprint_library.MATCH_MIN_CONFIDENCE,
              "minMargin": base.fingerprint_min_margin_db},
        "arrivalRadiusM": settings.nav_arrival_radius_m,
        "turnPromptDistanceM": settings.nav_turn_prompt_distance_m,
    })


@router.post("/field/segments")
def upload_segment(body: Segment, request: Request, db: Session = Depends(get_db_session)):
    if body.planId:
        plan = require_plan(db, body.planId, body.task, body.mapVersion)
        point_ids = {p["pointId"] for p in plan["points"]}
        if plan["day"] > _segment_day(body.startedAtEpoch) or plan["batch"] != body.batch or (
                body.task in ("CAL", "FP", "ST") and body.pointId not in point_ids
              ) or any(c.pointId not in point_ids for c in body.checkpoints):
            raise ApiError(422, "INVALID_FIELD_PLAN", "采集内容与正式任务清单不一致")
        if (body.task == "FP" and body.status == "complete"
                and body.endedAtEpoch - body.startedAtEpoch < FINGERPRINT_LEGACY_DURATION_MS):
            package = request.app.state.map_repository.get(SITE, body.mapVersion)
            if package is None:
                raise ApiError(422, "INVALID_FIELD_PLAN", "找不到采集所用的正式地图")
            beacon_ids = {b.beaconId for b in package.pkg.beacons if b.major is not None and b.minor is not None}
            valid_beacons = {frame.beacon for frame in body.rssi
                             if frame.beacon in beacon_ids and -100 <= frame.rssi <= -20}
            if len(valid_beacons) < 3:
                raise ApiError(422, "FIELD_SEGMENTS_MISSING", "提前结束的指纹采集段须包含至少 3 枚有效信标")
        expected = [p["pointId"] for p in plan["points"]]
        index = 0
        previous = None
        for mark in body.checkpoints:
            if mark.undo:
                if previous is None or previous.undo or previous.pointId != mark.pointId or not 0 <= mark.t - previous.t <= 2000:
                    raise ApiError(422, "INVALID_FIELD_PLAN", "检查点撤销无对应打点")
                index -= 1
            else:
                if index >= len(expected) or mark.pointId != expected[index]:
                    raise ApiError(422, "INVALID_FIELD_PLAN", "检查点顺序与清单不符")
                index += 1
            previous = mark
    if body.task in ("NV", "UT"):
        config = frozen_config(db, body.configId, body.mapVersion)
        expected = config["A" if body.method == "A" else "B"]["paramsVersion"]
        if body.paramsVersion != expected:
            raise ApiError(422, "INVALID_FIELD_CONFIG", "导航记录与冻结参数版本不一致")
        sessions = [e.get("sessionId") for e in body.navigation if e.get("kind") == "session"]
        if body.status == "complete" or sessions:
            session = db.get(NavSession, sessions[0]) if len(sessions) == 1 and isinstance(sessions[0], str) else None
            expected_run = {"planId": body.planId, "configId": body.configId, "runId": body.runId, "method": body.method}
            if not session or not session.experiment_json or json.loads(session.experiment_json) != expected_run:
                raise ApiError(422, "INVALID_FIELD_CONFIG", "导航记录与对应的在线会话不一致")
    payload = body.model_dump(mode="json")
    payload["durationS"] = (body.endedAtEpoch - body.startedAtEpoch) / 1000
    return _write(db, request, "segment", body.segmentId, payload)


def _sample_rows(db: Session, body: PublishSamplesRequest, task: str):
    plans = {key: require_plan(db, key, task, body.mapVersion) for key in body.planIds}
    selected = {}
    steps = {1} if task == "CAL" else {"N", "E", "S", "W"}
    for row in _rows(db, "segment"):
        if row["planId"] not in plans or row["status"] != "complete":
            continue
        key = (row["planId"], row["pointId"], 1 if task == "CAL" else row["orientation"])
        previous = selected.get(key)
        order = (row["part"] if task == "CAL" else 0, row["startedAtEpoch"])
        if previous is None or order < (previous["part"] if task == "CAL" else 0, previous["startedAtEpoch"]):
            selected[key] = row
    missing = []
    for plan_id, plan in plans.items():
        for point in plan["points"]:
            for step in sorted(steps):
                row = selected.get((plan_id, point["pointId"], step))
                if row is None:
                    missing.append(f"{point['pointId']}/{step}")
                else:
                    yield point, row
    if missing:
        raise ApiError(422, "FIELD_SEGMENTS_MISSING", f"还缺 {len(missing)} 段正式采集，如 " + "、".join(missing[:6]))


def _vectors(frames, known):
    vectors = defaultdict(list)
    for obs in frames:
        if obs["beacon"] in known and -100 <= obs["rssi"] <= -20:
            vectors[obs["beacon"]].append(obs["rssi"])
    return vectors


def _calibration_sessions(row: dict, known: set[str]) -> list[dict]:
    started = row["startedAtEpoch"]
    window_count = CALIBRATION_DURATION_MS // CALIBRATION_WINDOW_MS
    frames = [[] for _ in range(window_count)]
    for frame in row["rssi"]:
        offset = frame["t"] - started
        if 0 <= offset <= CALIBRATION_DURATION_MS:
            frames[min(offset // CALIBRATION_WINDOW_MS, window_count - 1)].append(frame)
    sessions = []
    seen = set()
    for index, window in enumerate(frames):
        vectors = _vectors(window, known)
        seen.update(vectors)
        if not vectors:
            continue
        observations = [{"beaconId": beacon, "n": len(values), "rssiMedian": round(statistics.median(values)),
                         "rssiMin": min(values), "rssiMax": max(values), "rssiStd": statistics.pstdev(values)}
                        for beacon, values in sorted(vectors.items())]
        sessions.append({"sessionIndex": index + 1,
                         "startedAt": _iso(started + index * CALIBRATION_WINDOW_MS),
                         "durationS": CALIBRATION_WINDOW_MS // 1000, "trafficWindow": False,
                         "observations": observations})
    if len(seen) < 3:
        raise ApiError(422, "FIELD_SEGMENTS_MISSING", f"{row['pointId']} 的 120 秒标定记录少于三个有效信标")
    return sessions


def _published_for_samples(request, version):
    package = request.app.state.map_repository.latest(SITE)
    if package is None or package.map_version != version:
        raise ApiError(409, "INVALID_FIELD_PLAN", "采集地图不是当前发布版本，请先复核")
    return package


def _iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


@router.post("/field/calibration-sample")
def make_calibration_sample(
    body: PublishSamplesRequest, request: Request, db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    _rate_limit("field_calibration_sample", 3.0)
    package = _published_for_samples(request, body.mapVersion)
    known = {b.beaconId for b in package.pkg.beacons}
    if len(body.planIds) != 1:
        raise ApiError(422, "INVALID_FIELD_PLAN", "请选择一个完整标定清单")
    points = {}
    for point, row in _sample_rows(db, body, "CAL"):
        key = point["pointId"]
        points[key] = {"pointId": key, "floor": int(point["floorId"]), "truth": [point["x"], point["y"]],
                       "scene": "route_sample", "envNote": point["note"],
                       "sessions": _calibration_sessions(row, known)}
    payload = {"type": "positioning_calibration_static", "schemaVersion": "1.0", "deployable": True,
               "venueId": SITE, "mapVersion": body.mapVersion, "windowMs": CALIBRATION_WINDOW_MS,
               "points": list(points.values())}
    try:
        return calibration_service.upload_sample(
            db, settings, file_name=f"experiment_cal_{body.planIds[0]}.json",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"), operator=request.state.operator,
        )
    except calibration_service.CalibrationServiceError as exc:
        raise ApiError(422, "INVALID_FIELD_SAMPLE", str(exc)) from exc


@router.get("/field/fingerprint-build")
def fingerprint_build_plans(mapVersion: Version, request: Request, db: Session = Depends(get_db_session)):
    _published_for_samples(request, mapVersion)
    today = datetime.now(FIELD_TIMEZONE).date().isoformat()
    plans = sorted(
        (p for p in _rows(db, "plan") if p["task"] == "FP"
         and p["mapVersion"] == mapVersion and p["day"] <= today),
        key=_plan_revision, reverse=True,
    )
    return {"plans": [{"planId": p["planId"], "batch": p["batch"], "day": p["day"],
                       "pointCount": len(p["points"]), "active": index == 0}
                      for index, p in enumerate(plans)]}


@router.post("/field/fingerprint-build")
def build_fingerprint_library(body: PublishSamplesRequest, request: Request, db: Session = Depends(get_db_session)):
    _rate_limit("field_fingerprint_build", 5.0)
    package = _published_for_samples(request, body.mapVersion)
    catalog = {b.beaconId: b for b in package.pkg.beacons if b.major is not None and b.minor is not None}
    records = []
    positions = {}
    for point, row in _sample_rows(db, body, "FP"):
        key = point["pointId"]
        coordinates = [point["x"], point["y"]]
        reference = (point["floorId"], coordinates)
        if positions.setdefault(key, reference) != reference:
            raise ApiError(422, "INVALID_FIELD_PLAN", f"{key} 在不同清单中的真值不一致")
        vectors = _vectors(row["rssi"], catalog)
        if len(vectors) < 3:
            raise ApiError(422, "FIELD_SEGMENTS_MISSING", f"{row['segmentId']} 少于三个有效信标")
        beacons = [{"major": catalog[bid].major, "minor": catalog[bid].minor,
                    "uuid": catalog[bid].uuid, "rssi": statistics.median(values), "n": len(values)}
                   for bid, values in sorted(vectors.items())]
        records.append({"fpId": key, "coordinates": coordinates, "floor": point["floorId"],
                        "collectedAt": _iso(row["startedAtEpoch"]), "paramsVersion": row["paramsVersion"],
                        "beacons": beacons})
    content = json.dumps({"type": fingerprint_library.FP_EXPORT_TYPE, "schemaVersion": "1.3.0",
                          "mapVersion": body.mapVersion, "dataset": "field-T4",
                          "mode": "replaceFloor", "replaceFloors": sorted({r["floor"] for r in records}),
                          "records": records}, ensure_ascii=False)
    try:
        result = fingerprint_library.ingest_export(db, file_name="field-experiment.json",
                                                  content=content, operator=request.state.operator)
        db.commit()
        return result
    except fingerprint_library.FingerprintLibraryError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


def _finite(value: float | None) -> float | None:
    return None if value is None or isinf(value) else round(value, 3)


def _error_m(estimate, truth: dict | None) -> float | None:
    if truth is None or estimate.x is None or estimate.y is None:
        return None
    if estimate.floor_id != truth["floorId"]:
        return None
    return round(hypot(estimate.x - truth["x"], estimate.y - truth["y"]), 3)


def _suspend_ids(db: Session, config: dict, points: dict, radius_m: float,
                 task: str) -> list[str]:
    """挂起库：排除距 ST/T8 真值点过近的 FP 点（生产库不改动）。"""
    if task not in ("ST", "NV") or radius_m <= 0 or not points:
        return []
    return fingerprint_library.fp_ids_near(
        db, version=config["C"]["libraryVersion"],
        points=[(point["floorId"], point["x"], point["y"]) for point in points.values()],
        radius_m=radius_m)


def _compare(db: Session, package, config: dict, samples, truth: dict | None,
             timestamp: int, *, exclude_fp_ids: list[str] | None = None) -> dict:
    """用同一段原始 RSSI 对 A/B/C 求定位结果与误差。"""
    vectors = defaultdict(list)
    for sample in samples:
        vectors[sample["beacon"]].append(min(-20, max(-100, sample["rssi"])))
    observations = [BeaconInput(beacon_id=key, rssi=round(statistics.median(values)))
                    for key, values in vectors.items()]
    matches = [BeaconObservation(
        beaconId=obs.beacon_id, rssi=obs.rssi,
        observedAt=datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc))
        for obs in observations]
    a, b = [estimate_location(pkg=package, observations=observations, floor_hint=None,
                              params=frozen_location_params(config, method))
            for method in ("A", "B")]
    hit, reason, diagnostics = fingerprint_library.query_with_diagnostics(
        db, pkg=package, beacons=matches, frozen=config["C"],
        exclude_fp_ids=set(exclude_fp_ids) if exclude_fp_ids else None)
    comparison = {}
    for method, estimate in (("A", a), ("B", b), ("C", hit or b)):
        comparison[method] = {
            "x": estimate.x, "y": estimate.y, "floorId": estimate.floor_id,
            "errorM": _error_m(estimate, truth),
            "floorCorrect": (None if truth is None or estimate.x is None or estimate.y is None
                             else estimate.floor_id == truth["floorId"]),
        }
    result = comparison["C"]
    result.update({"source": "fingerprint" if hit else "trilateration", "fallbackReason": reason})
    if hit is not None:
        result.update({"rmseDb": hit.rmse_db, "overlap": hit.overlap,
                       "marginDb": _finite(hit.margin_db), "confidence": hit.confidence})
    else:
        result["diagnostics"] = diagnostics.as_dict()
    return comparison


def _replay_static(db: Session, request: Request, body: ReplayRequest,
                   segments: list[dict]) -> dict:
    """T6 静态对比：每个 (点位, 朝向) 取最新完整段，按整段中位数对 A/B/C 求误差。"""
    run = next(row for row in segments if row["task"] == "ST" and row["status"] != "aborted")
    package = request.app.state.map_repository.get(SITE, run["mapVersion"])
    if not package:
        raise ApiError(404, "MAP_NOT_PUBLISHED", "试走的地图已不可用")
    config_id = (body.configId
                 or next((row["configId"] for row in segments if row.get("configId")), None))
    if not config_id:
        raise ApiError(422, "INVALID_FIELD_CONFIG", "重放 T6 静态对比需要选择冻结配置")
    config = frozen_config(db, config_id, run["mapVersion"])
    plan = require_plan(db, run["planId"], "ST", run["mapVersion"]) if run["planId"] else None
    points = {point["pointId"]: point for point in plan["points"]} if plan else {}
    latest: dict[tuple[str, str], dict] = {}
    for row in segments:
        if row["task"] != "ST" or row["status"] == "aborted":
            continue
        if row["mapVersion"] != run["mapVersion"]:
            raise ApiError(422, "INVALID_FIELD_PLAN", "同一轮静态采集的地图版本不一致")
        key = (row["pointId"], row["orientation"])
        if key not in latest or row["endedAtEpoch"] > latest[key]["endedAtEpoch"]:
            latest[key] = row
    suspend_ids = _suspend_ids(db, config, points, body.suspendRadiusM, "ST")
    checkpoints = []
    for (point_id, orientation), row in sorted(latest.items()):
        checkpoints.append({
            "pointId": point_id, "orientation": orientation, "segmentId": row["segmentId"],
            "t": row["startedAtEpoch"], "timeSource": "segment",
            "methods": _compare(db, package.pkg, config, row["rssi"], points.get(point_id),
                                row["startedAtEpoch"], exclude_fp_ids=suspend_ids),
        })
    covered = {item["pointId"] for item in checkpoints}
    return {"runId": body.runId, "task": "ST", "configId": config_id, "trial": plan is None,
            "suspendRadiusM": body.suspendRadiusM, "suspendedFpIds": suspend_ids,
            "missingPointIds": [point_id for point_id in points if point_id not in covered],
            "checkpoints": checkpoints}


def _replay_run(db: Session, request: Request, body: ReplayRequest) -> dict:
    """Compare A/B/C on the same raw window around each checkpoint."""
    segments = [row for row in _rows(db, "segment") if row["runId"] == body.runId]
    if not segments or all(row["status"] == "aborted" for row in segments):
        raise ApiError(404, "FIELD_RUN_NOT_FOUND", "找不到已上传的可重放路线")
    if (any(row["task"] == "ST" for row in segments)
            and not any(row["task"] in ("DY", "NV") for row in segments)):
        return _replay_static(db, request, body, segments)
    run = next((row for row in segments if row["task"] in ("DY", "NV")), None)
    if run is None or run["status"] == "aborted":
        raise ApiError(404, "FIELD_RUN_NOT_FOUND", "找不到已上传的可重放路线")
    package = request.app.state.map_repository.get(SITE, run["mapVersion"])
    if not package:
        raise ApiError(404, "MAP_NOT_PUBLISHED", "试走的地图已不可用")
    config_id = run["configId"] if run["task"] in ("NV", "UT") else body.configId
    if not config_id:
        raise ApiError(422, "INVALID_FIELD_CONFIG", "重放 T7 试走需要选择冻结配置")
    config = frozen_config(db, config_id, run["mapVersion"])
    plan = require_plan(db, run["planId"], run["task"], run["mapVersion"]) if run["planId"] else None
    points = {p["pointId"]: p for p in plan["points"]} if plan else {}
    marks = {}
    for event in run["checkpoints"]:
        if event["undo"]:
            marks.pop(event["pointId"], None)
        else:
            marks[event["pointId"]] = (event["t"], "operator")
    for row in _rows(db, "note"):
        if (row["runId"] == body.runId and row["pointId"] and row.get("isArrival")
                and (plan is None or row["pointId"] in points)
                and run["startedAtEpoch"] <= row["t"] <= run["endedAtEpoch"]):
            marks[row["pointId"]] = (row["t"], "observer")
    suspend_ids = _suspend_ids(db, config, points, body.suspendRadiusM, run["task"])
    result = []
    for point_id, (timestamp, reference) in marks.items():
        samples = [sample for sample in run["rssi"]
                   if abs(sample["t"] - timestamp) <= 1000]
        result.append({
            "pointId": point_id, "t": timestamp, "timeSource": reference,
            "methods": _compare(db, package.pkg, config, samples, points.get(point_id),
                                timestamp, exclude_fp_ids=suspend_ids),
        })
    return {"runId": body.runId, "task": run["task"], "configId": config_id,
            "trial": plan is None, "suspendRadiusM": body.suspendRadiusM,
            "suspendedFpIds": suspend_ids,
            "missingPointIds": [point_id for point_id in points if point_id not in marks],
            "checkpoints": result}


@router.post("/field/replay")
def replay_checkpoints(body: ReplayRequest, request: Request, db: Session = Depends(get_db_session)):
    _rate_limit("field_replay", 1.0)
    return _replay_run(db, request, body)


@router.post("/field/replay/batch")
def replay_batch(body: ReplayBatchRequest, request: Request, db: Session = Depends(get_db_session)):
    """One-shot replay of many runs; per-run failures return an error item instead of failing the batch."""
    _rate_limit("field_replay_batch", 1.0)
    results = []
    for run_id in dict.fromkeys(body.runIds):
        try:
            results.append(_replay_run(db, request, ReplayRequest(
                configId=body.configId, runId=run_id, suspendRadiusM=body.suspendRadiusM)))
        except ApiError as exc:
            results.append({"runId": run_id, "error": {
                "code": exc.code, "message": exc.message, "statusCode": exc.status_code}})
    return {"results": results}


@router.post("/field/notes")
def note(body: FieldNote, request: Request, db: Session = Depends(get_db_session)):
    if body.kind == "skip" and (not body.pointId or not body.day):
        raise ApiError(422, "INVALID_FIELD_PLAN", "跳过记录需要点位和日期")
    return _write(db, request, "note", body.noteId, body.model_dump(mode="json"))


@router.post("/field/inspections")
def inspection(body: Inspection, request: Request, db: Session = Depends(get_db_session)):
    return _write(db, request, "inspection", body.inspectionId, body.model_dump(mode="json"))


def _csv(rows, columns):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                         else value for key, value in row.items()})
    return stream.getvalue()


@router.get("/field/inspection-export")
def inspection_export(db: Session = Depends(get_db_session)):
    return Response(_csv(_rows(db, "inspection"), (
        "beaconId", "mapVersion", "t", "action", "location", "mount",
        "rssi", "lastSeen", "note", "operatorUserId",
    )), media_type="text/csv")


@router.get("/export/experiment")
def export_experiment(request: Request, include_trial: bool = False,
                      db: Session = Depends(get_db_session)):
    task_plans = {p["planId"]: p for p in _rows(db, "plan")}
    counts = dict.fromkeys(FILES, 0)
    excluded = []
    exported_runs = set()
    versions = set()
    c_estimates = {"fingerprintHits": 0, "fallbacksToB": 0, "unavailable": 0}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for task, name in FILES.items():
            with bundle.open(name, "w") as stream:
                for segment in _rows(db, "segment"):
                    if segment["task"] != task:
                        continue
                    if not include_trial and not segment["planId"]:
                        continue
                    if segment["status"] != "complete":
                        excluded.append({"segmentId": segment["segmentId"], "reason": segment["reason"]})
                    counts[task] += 1
                    if task in ("NV", "UT") and segment["method"] == "C" and segment["status"] == "complete":
                        for event in segment["navigation"]:
                            if event.get("kind") == "estimate":
                                if event.get("source") == "fingerprint":
                                    c_estimates["fingerprintHits"] += 1
                                elif event.get("source") == "trilateration":
                                    c_estimates["fallbacksToB"] += 1
                            elif event.get("kind") == "unavailable":
                                c_estimates["unavailable"] += 1
                    exported_runs.add(segment["runId"])
                    versions.add((segment["mapVersion"], segment["paramsVersion"]))
                    stream.write((json.dumps(segment, ensure_ascii=False) + "\n").encode("utf-8"))
        bundle.writestr("observer_records.csv", _csv(
            (n for n in _rows(db, "note") if not n["runId"] or n["runId"] in exported_runs),
            ("runId", "pointId", "t", "kind", "planId", "day", "isArrival", "note", "turnRating", "cleared", "operatorUserId"),
        ))
        bundle.writestr("ground_truth.csv", _csv(
            ({**point, "planId": p["planId"], "task": p["task"], "operatorUserId": p["operatorUserId"]}
             for p in task_plans.values() for point in p["points"]),
            ("planId", "task", "pointId", "floorId", "x", "y", "surveyVersion", "candidateX", "candidateY",
             "measuredOn", "operatorUserId"),
        ))
        bundle.writestr("manifest.json", json.dumps({
            "schemaVersion": "1.0", "exportedOn": date.today().isoformat(), "includeTrial": include_trial,
            "counts": counts, "excluded": excluded, "mapAndParamVersions": sorted(versions),
            "cEstimateCounts": c_estimates,
            "analysisConfig": {
                "metricVersion": ANALYSIS_METRIC_VERSION,
                "suspendRadiusM": DEFAULT_SUSPEND_RADIUS_M,
                "jumpThresholdM": ANALYSIS_JUMP_THRESHOLD_M,
                "expectedUpdateIntervalMs": {"standby": 8000, "preview": 2500, "navigating": 1000},
                "matchThresholds": {
                    "minOverlap": fingerprint_library.MATCH_MIN_OVERLAP,
                    "maxRmseDb": fingerprint_library.MATCH_MAX_RMSE_DB,
                    "confidenceRmseDb": fingerprint_library.MATCH_CONFIDENCE_RMSE_DB,
                    "minConfidence": fingerprint_library.MATCH_MIN_CONFIDENCE,
                    "minMarginDb": request.app.state.settings.fingerprint_min_margin_db,
                },
            },
            "plans": list(task_plans.values()), "frozenConfigurations": list(_rows(db, "config")),
        }, ensure_ascii=False))
    name = f"experiment_export_{date.today().isoformat()}.zip"
    return Response(archive.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
