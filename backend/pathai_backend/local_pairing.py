from __future__ import annotations

import secrets
import time
from threading import RLock

from sqlalchemy.orm import Session

from pathai_backend.local_identity_protocol import (
    PROTOCOL, SERVICE_NAME, authenticated, decode_key, encode_key, is_identifier,
    is_nonce, is_private_ipv4, parse_json, verify,
)
from pathai_backend.models import LocalDeviceIdentity, LocalPairing


class LocalPairingService:
    def __init__(self, boot_id: str):
        self.boot_id = boot_id
        self._lock = RLock()

    @staticmethod
    def device_id(session: Session) -> str:
        identity = session.get(LocalDeviceIdentity, 1)
        if identity is None:
            identity = LocalDeviceIdentity(slot=1, device_id=f"srv-{secrets.token_hex(16)}")
            session.add(identity)
            session.commit()
        return identity.device_id

    def open_window(self, session: Session, ipv4: str, port: int) -> dict[str, object]:
        if not is_private_ipv4(ipv4) or not 1 <= port <= 65535:
            raise ValueError("invalid endpoint")
        with self._lock:
            now = int(time.time())
            for pending in session.query(LocalPairing).filter(LocalPairing.status == "pending"):
                if pending.expires_at_epoch <= now:
                    pending.status = "expired"
                    pending.key_b64 = ""
                else:
                    raise ValueError("pairing window already open")
            pair_id = f"pair-{secrets.token_hex(16)}"
            key = secrets.token_bytes(32)
            session.add(LocalPairing(pair_id=pair_id, key_b64=encode_key(key), status="pending",
                                     expires_at_epoch=now + 120, created_at_epoch=now))
            device_id = self.device_id(session)
            session.commit()
            return {
                "kind": "local-service-pairing", "version": 1, "serviceName": SERVICE_NAME,
                "deviceId": device_id, "pairId": pair_id, "pairingKey": encode_key(key),
                "endpointHint": {"ipv4": ipv4, "port": port}, "expiresAt": now + 120,
            }

    def revoke(self, session: Session, pair_id: str) -> bool:
        with self._lock:
            record = session.get(LocalPairing, pair_id)
            if record is None:
                return False
            record.status = "revoked"
            record.key_b64 = ""
            session.commit()
            return True

    def active_key(self, session: Session, pair_id: str) -> bytes | None:
        if not is_identifier(pair_id, "pair"):
            return None
        record = session.get(LocalPairing, pair_id)
        if record is None or record.status != "active":
            return None
        try:
            return decode_key(record.key_b64)
        except ValueError:
            return None

    def activate(self, session: Session, raw: bytes) -> dict[str, object] | None:
        try:
            request = parse_json(raw, 1024)
        except (UnicodeError, ValueError):
            return None
        if set(request) != {"protocol", "version", "type", "serviceName", "deviceId", "pairId", "nonce", "auth"}:
            return None
        if (request["protocol"] != PROTOCOL or type(request["version"]) is not int or request["version"] != 1
                or request["type"] != "ACTIVATE" or request["serviceName"] != SERVICE_NAME
                or not is_identifier(request["deviceId"], "srv") or not is_identifier(request["pairId"], "pair")
                or not is_nonce(request["nonce"])):
            return None
        with self._lock:
            record = session.get(LocalPairing, request["pairId"])
            if record is None or record.status not in ("pending", "active"):
                return None
            if record.status == "pending" and record.expires_at_epoch <= int(time.time()):
                record.status = "expired"
                record.key_b64 = ""
                session.commit()
                return None
            if request["deviceId"] != self.device_id(session):
                return None
            try:
                key = decode_key(record.key_b64)
            except ValueError:
                return None
            if not verify("ACTIVATE", request, key):
                return None
            record.status = "active"
            session.commit()
            return authenticated("ACTIVATE", {
                "protocol": PROTOCOL, "version": 1, "type": "ACTIVATED", "serviceName": SERVICE_NAME,
                "deviceId": request["deviceId"], "pairId": request["pairId"],
                "nonce": request["nonce"], "bootId": self.boot_id,
            }, key)

    def probe(self, session: Session, pair_id: str, challenge: str) -> dict[str, object] | None:
        if not is_identifier(pair_id, "pair") or not is_nonce(challenge):
            return None
        with self._lock:
            key = self.active_key(session, pair_id)
            if key is None:
                return None
            return authenticated("PROBE", {
                "protocol": PROTOCOL, "version": 1, "type": "PROBE", "serviceName": SERVICE_NAME,
                "deviceId": self.device_id(session), "pairId": pair_id,
                "bootId": self.boot_id, "challenge": challenge, "ready": True,
                "apiVersion": 1, "serverTimeMs": int(time.time() * 1000),
            }, key)
