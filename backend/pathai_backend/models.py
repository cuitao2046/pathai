# -*- coding: utf-8 -*-
"""ORM 模型基类。"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AnonymousSubject(Base):
    """匿名主体：仅存加盐 openid 哈希，不存明文身份。"""

    __tablename__ = "anonymous_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    openid_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(21), nullable=False, unique=True)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AccessToken(Base):
    """不透明访问令牌：服务端仅存 token 哈希。"""

    __tablename__ = "access_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), nullable=False
    )
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_access_tokens_subject", AccessToken.subject_id)


class LocalDeviceIdentity(Base):
    __tablename__ = "local_device_identities"

    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)


class LocalPairing(Base):
    __tablename__ = "local_pairings"

    pair_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key_b64: Mapped[str] = mapped_column(String(43), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class LocationSequence(Base):
    """定位批次序号。"""

    __tablename__ = "location_sequences"

    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), primary_key=True
    )
    site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class NavSession(Base):
    """导航会话。"""

    __tablename__ = "nav_sessions"

    session_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), nullable=False
    )
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    map_version: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_place_id: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_name: Mapped[str] = mapped_column(String(128), nullable=False)
    destination_floor_id: Mapped[str] = mapped_column(String(16), nullable=False)
    user_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    route_json: Mapped[str] = mapped_column(Text, nullable=False)
    current_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 会话创建时固化的定位参数版本（docs/28 §5.4）
    location_params_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="unset"
    )
    route_rules_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    ended_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


Index("ix_nav_sessions_subject", NavSession.subject_id)


class NavIdempotency(Base):
    """导航创建幂等记录。"""

    __tablename__ = "nav_idempotency_keys"

    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), primary_key=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(40), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)


class AdminAccount(Base):
    """管理后台固定账户：仅存带随机盐的密码哈希，不存明文。

    must_change_password：首版初始化 admin/admin 后为真，成功改密后置假。
    """

    __tablename__ = "admin_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    password_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_params: Mapped[str] = mapped_column(String(64), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AdminSession(Base):
    """管理员会话：服务端只存 token/CSRF 的 SHA-256 摘要，cookie 携带原文。"""

    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class MapDraft(Base):
    """每场所唯一活动草稿：只保存白名单编辑差异，不复制整份地图包。"""

    __tablename__ = "map_drafts"

    site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_version: Mapped[str] = mapped_column(String(32), nullable=False)
    patch_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AdminAuditEvent(Base):
    """关键管理操作审计：登录成功/失败、改密、退出、草稿重置、发布、回滚。

    不记录密码、token 或字段级编辑差异（map-admin-spec §6 审计）。
    """

    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class CalibrationSample(Base):
    """标定样本文件：内容落盘，库内只存索引与哈希（无鉴权，靠私网+审计防护）。"""

    __tablename__ = "calibration_samples"

    sample_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(160), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_type: Mapped[str] = mapped_column(String(48), nullable=False)
    deployable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    map_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    uploaded_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_calibration_samples_site", CalibrationSample.site_id)


class CalibrationRun(Base):
    """一次计算任务：computed / compute_failed / applied / superseded / rolled_back / stale。"""

    __tablename__ = "calibration_runs"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_id: Mapped[str] = mapped_column(String(40), nullable=False)
    sample_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    grid_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_grid_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    grid_staging_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_calibration_runs_site", CalibrationRun.site_id)
Index("ix_calibration_runs_sample", CalibrationRun.sample_id)


class LocationParamVersion(Base):
    """定位参数版本：不可变；任何修改都是新增一行。"""

    __tablename__ = "location_param_versions"

    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rssi_at_1m: Mapped[float] = mapped_column(nullable=False)
    path_loss_n: Mapped[float] = mapped_column(nullable=False)
    residual_reliable_m: Mapped[float] = mapped_column(nullable=False)
    residual_degraded_m: Mapped[float] = mapped_column(nullable=False)
    grid_version: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_location_param_versions_site", LocationParamVersion.site_id)


class FingerprintGridVersion(Base):
    """指纹网格版本：与参数版本成对存在。"""

    __tablename__ = "fingerprint_grid_versions"

    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    base_grid_version: Mapped[str] = mapped_column(String(40), nullable=False)
    map_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rssi_at_1m: Mapped[float] = mapped_column(nullable=False)
    path_loss_n: Mapped[float] = mapped_column(nullable=False)
    overlay_path: Mapped[str] = mapped_column(String(512), nullable=False)
    manifest_path: Mapped[str] = mapped_column(String(512), nullable=False)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    collectable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_fingerprint_grid_versions_site", FingerprintGridVersion.site_id)


class CalibrationActivePointer(Base):
    """每个场地唯一的生效指针：参数版本与网格版本必须成对指向。"""

    __tablename__ = "calibration_active_pointer"

    site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    param_version: Mapped[str] = mapped_column(String(40), nullable=False)
    grid_version: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class BeaconDeploymentRecord(Base):
    """真实部署台账：每场地每 beaconId 一条（幂等 upsert）；规划台账保持只读。

    written/verify 以 JSON 保存完整写入值与校验结果（docs/27 §6.8）。
    """

    __tablename__ = "beacon_deployment_records"
    __table_args__ = (
        UniqueConstraint("site_id", "beacon_id", name="uq_beacon_record_site_beacon"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    beacon_id: Mapped[str] = mapped_column(String(32), nullable=False)
    map_version: Mapped[str] = mapped_column(String(32), nullable=False)
    device_mac: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name_write: Mapped[str | None] = mapped_column(String(16), nullable=True)
    written_json: Mapped[str] = mapped_column(Text, nullable=False)
    verify_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    configured_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_beacon_records_site", BeaconDeploymentRecord.site_id)


class BeaconProvisioningRound(Base):
    """入库轮次汇总（commit 时生成）：完整性校验结果快照，用于发布 gate 与审计。"""

    __tablename__ = "beacon_provisioning_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    map_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded: Mapped[int] = mapped_column(Integer, nullable=False)
    verified: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_beacon_rounds_site", BeaconProvisioningRound.site_id)


class FingerprintLibraryVersion(Base):
    """指纹库版本：不可变快照；每次有效入库新增一行并切换 active。"""

    __tablename__ = "fingerprint_library_versions"
    #: 内容幂等键按场所隔离：同内容入库到不同场所各自成版本。
    __table_args__ = (
        UniqueConstraint(
            "site_id", "content_sha256", name="uq_fpl_versions_site_content"
        ),
    )

    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fp_count: Mapped[int] = mapped_column(Integer, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False)
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    map_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    grid_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    params_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 四类计数：record_count（本版最终条数）= inserted + updated + retained；
    #: previous_count 为上一 active 版本条数，配合 deleted 可完整复算变更。
    updated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    deleted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    retained_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    previous_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: 入库模式：merge（默认）/ replaceFloor（显式整层重采）。
    ingest_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="merge"
    )
    replace_floors_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 保留策略：明细（records/points）已被裁剪；摘要行永久保留供审计。
    details_pruned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )
    pruned_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)


Index("ix_fingerprint_library_versions_site", FingerprintLibraryVersion.site_id)


class FingerprintLibraryRecord(Base):
    """版本内的原始指纹记录：按 (version, fpId, collectedAt) 唯一，供审计/重算。"""

    __tablename__ = "fingerprint_library_records"

    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    fp_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    collected_at: Mapped[str] = mapped_column(String(40), primary_key=True)
    floor_id: Mapped[str] = mapped_column(String(16), nullable=False)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)


Index("ix_fingerprint_library_records_version", FingerprintLibraryRecord.version)


class FingerprintLibraryPoint(Base):
    """版本内的按点聚合向量（查询模型）：beaconId → 中位 RSSI。"""

    __tablename__ = "fingerprint_library_points"

    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    fp_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    floor_id: Mapped[str] = mapped_column(String(16), nullable=False)
    x: Mapped[float] = mapped_column(nullable=False)
    y: Mapped[float] = mapped_column(nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    beacon_count: Mapped[int] = mapped_column(Integer, nullable=False)
    vector_json: Mapped[str] = mapped_column(Text, nullable=False)


Index("ix_fingerprint_library_points_version", FingerprintLibraryPoint.version)


class FingerprintLibraryPointer(Base):
    """每个场地当前生效的指纹库版本。"""

    __tablename__ = "fingerprint_library_pointer"

    site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)

