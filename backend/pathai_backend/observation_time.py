"""观测时间戳口径：脏数据下限由服务端把关，起点新鲜度由客户端决定。

两级职责分离：

1. ``is_sane`` —— 服务端唯一会因时间戳拒绝请求的检查。只拦截**明显非法**的数据：
   缺时区、未来偏差过大、旧到不可能是本次采集。
2. ``is_fresh_for_guidance`` —— 导航推进门槛（15 s）。它只决定「这条位置能否推进
   路线/触发到达」，过期时降级为 recovering，**不返回错误**。

「预览/起点够不够新」交给客户端：客户端掌握本地时钟、采集节拍与用户是否停留，
服务端的硬失败只会把本可纠正的情况变成 422。服务端改为在响应里回带观测龄与
stale 标记（``observation_age_s`` / ``originObservationAgeS`` 等），供客户端提示与排障。
"""
from datetime import datetime, timezone

#: 脏数据下限：观测龄超过该值视为不是本次会话的观测，拒绝请求。
#: 依据：客户端采集窗口 2 s、待命/预览上报节拍 ≤8 s；即使离线补传，5 分钟前的 RSSI
#: 已无空间参考价值（1.5 m/s × 300 s ≈ 450 m，远超建筑尺度）。
SANITY_MAX_AGE_S = 300.0

#: 脏数据下限：允许的最大未来偏差。NTP 同步后的手机时钟偏差通常 < 1 s，
#: 60 s 只用于拦截手动改时间、年份错写等明显非法值。
SANITY_MAX_FUTURE_SKEW_S = 60.0

#: 「够不够新」的提示阈值：与客户端预览复用窗口（10 s 播报结束起算）同一量级，
#: 仅用于回带 stale 提示，不参与拒绝。
ADVISORY_STALE_AGE_S = 15.0

#: 导航推进门槛：超过该观测龄的位置不推进路线（降级 recovering，不拒绝请求）。
GUIDANCE_MAX_AGE_S = 15.0
GUIDANCE_MAX_FUTURE_SKEW_S = 5.0


def observation_age_s(observed: datetime, now: datetime | None = None) -> float | None:
    """观测龄（秒）；时区缺失返回 None（调用方按非法处理）。"""
    if observed.tzinfo is None or observed.utcoffset() is None:
        return None
    return ((now or datetime.now(timezone.utc)) - observed).total_seconds()


def is_sane(observed: datetime, now: datetime | None = None) -> bool:
    """时间戳是否可用于业务计算（不判断新鲜度）。"""
    age = observation_age_s(observed, now)
    return age is not None and -SANITY_MAX_FUTURE_SKEW_S <= age <= SANITY_MAX_AGE_S


def is_fresh_for_guidance(observed: datetime, now: datetime | None = None) -> bool:
    """导航推进专用：过期位置不得推进路线，但请求本身仍然成功。"""
    age = observation_age_s(observed, now)
    return age is not None and -GUIDANCE_MAX_FUTURE_SKEW_S <= age <= GUIDANCE_MAX_AGE_S
