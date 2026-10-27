# -*- coding: utf-8 -*-
"""管理后台安全原语：scrypt 密码哈希、opaque token 与 CSRF token 生成。"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32

#: 版本化哈希参数串：算法:n:r:p:dklen，验证时按此解析，便于后续升级参数。
HASH_PARAMS = f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{_DKLEN}"

PASSWORD_MIN_BYTES = 8


def hash_password(password: str) -> tuple[str, str]:
    """返回 (hash_hex, salt_hex)；密码按 UTF-8 编码后送入 scrypt。"""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt)
    return digest.hex(), salt.hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    """常量时间比较；参数异常视为验证失败，不抛出。"""
    try:
        salt = bytes.fromhex(salt_hex)
        digest = _scrypt(password, salt)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected_hash_hex)


def password_too_short(password: str) -> bool:
    """产品规则：UTF-8 编码后至少 8 字节（spec FR-01）。"""
    return len(password.encode("utf-8")) < PASSWORD_MIN_BYTES


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
