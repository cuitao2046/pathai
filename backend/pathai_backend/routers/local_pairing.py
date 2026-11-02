from __future__ import annotations

import hmac
import http.client
import ipaddress

from fastapi import APIRouter, Request

from pathai_backend.database import session_scope
from pathai_backend.errors import ApiError
from pathai_backend.local_identity_protocol import is_identifier, is_private_ipv4, parse_json

router = APIRouter(tags=["local-pairing"])


def _service(request: Request):
    if not request.app.state.local_pairing_ready:
        raise ApiError(503, "PAIRING_DISABLED", "配对能力未启用")
    return request.app.state.local_pairing


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("not loopback")
    except ValueError as exc:
        raise ApiError(403, "LOCAL_ONLY", "配对管理仅允许本机操作") from exc
    hostname = request.url.hostname or ""
    if hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ApiError(403, "LOCAL_ONLY", "配对管理仅允许本机操作")
    origin = request.headers.get("origin")
    if origin != str(request.base_url).rstrip("/"):
        raise ApiError(403, "LOCAL_ONLY", "跨源请求被拒绝")
    expected = request.app.state.settings.local_pairing_admin_token
    supplied = request.headers.get("X-Local-Pairing-Admin", "")
    if len(expected) < 32 or not hmac.compare_digest(expected, supplied):
        raise ApiError(403, "LOCAL_ONLY", "配对管理未授权")


def _require_enabled(request: Request) -> None:
    if not request.app.state.local_pairing_ready:
        raise ApiError(503, "PAIRING_DISABLED", "配对能力未启用")


async def _bounded_body(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > limit):
        raise ApiError(400, "INVALID_REQUEST", "请求格式不合法")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise ApiError(400, "INVALID_REQUEST", "请求格式不合法")
    return bytes(raw)


@router.post("/local/v1/admin/pair/open")
async def open_pair(request: Request) -> dict:
    _require_local(request)
    _require_enabled(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ApiError(400, "INVALID_REQUEST", "请求格式不合法")
    raw = await _bounded_body(request, 256)
    try:
        body = parse_json(raw, 256)
        ipv4 = body["ipv4"]
        port = request.app.state.settings.local_business_port
        if set(body) != {"ipv4"} or not isinstance(ipv4, str) or not is_private_ipv4(ipv4) \
                or not 1 <= port <= 65535:
            raise ValueError("invalid endpoint")
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ApiError(400, "INVALID_REQUEST", "请求格式不合法") from exc
    try:
        from fastapi.concurrency import run_in_threadpool
        await run_in_threadpool(_check_local_endpoint, ipv4, port)
    except (OSError, ValueError) as exc:
        raise ApiError(503, "LOCAL_ENDPOINT_UNAVAILABLE", "业务端口身份未确认") from exc
    with session_scope() as session:
        try:
            return _service(request).open_window(session, ipv4, port)
        except ValueError as exc:
            raise ApiError(409, "PAIR_WINDOW_OPEN", "当前配对窗口仍有效") from exc


@router.post("/local/v1/admin/pair/revoke")
async def revoke_pair(request: Request) -> dict:
    _require_local(request)
    _require_enabled(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ApiError(400, "INVALID_REQUEST", "请求格式不合法")
    try:
        body = parse_json(await _bounded_body(request, 256), 256)
        pair_id = body["pairId"]
        if set(body) != {"pairId"} or not is_identifier(pair_id, "pair"):
            raise ValueError("invalid pair")
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ApiError(400, "INVALID_REQUEST", "请求格式不合法") from exc
    with session_scope() as session:
        return {"revoked": _service(request).revoke(session, pair_id)}


@router.post("/local/v1/pair/activate")
async def activate_pair(request: Request) -> dict:
    _require_enabled(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ApiError(400, "INVALID_REQUEST", "请求格式不合法")
    with session_scope() as session:
        result = _service(request).activate(session, await _bounded_body(request, 1024))
    if result is None:
        raise ApiError(403, "PAIRING_UNAVAILABLE", "配对不可用")
    return result


@router.get("/local/v1/admin/address")
def address_pair(request: Request, ipv4: str) -> dict:
    _require_local(request)
    _require_enabled(request)
    port = request.app.state.settings.local_business_port
    if not is_private_ipv4(ipv4):
        raise ApiError(400, "INVALID_REQUEST", "请求格式不合法")
    try:
        _check_local_endpoint(ipv4, port)
    except (OSError, ValueError) as exc:
        raise ApiError(503, "LOCAL_ENDPOINT_UNAVAILABLE", "业务端口身份未确认") from exc
    with session_scope() as session:
        device_id = _service(request).device_id(session)
    return {"kind": "local-service-address", "version": 1, "serviceName": "my-backend",
            "deviceId": device_id, "endpointHint": {"ipv4": ipv4, "port": port}}


def _check_local_endpoint(ipv4: str, port: int) -> None:
    connection = http.client.HTTPConnection(ipv4, port, timeout=1)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        if response.status != 200 or response.read(128) != b'{"status":"ok"}':
            raise ValueError("unexpected health response")
    finally:
        connection.close()


@router.get("/local/v1/probe")
def probe_pair(request: Request, pairId: str, challenge: str) -> dict:
    _require_enabled(request)
    with session_scope() as session:
        result = _service(request).probe(session, pairId, challenge)
    if result is None:
        raise ApiError(403, "PAIRING_UNAVAILABLE", "配对不可用")
    return result
