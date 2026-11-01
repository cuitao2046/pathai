from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PROTOCOL = "local-service-discovery"
SERVICE_NAME = "pathai-p0-probe"
MAX_PACKET_BYTES = 1200
MAX_REPLY_RECORDS = 128


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_discover(payload: bytes) -> str | None:
    if len(payload) > MAX_PACKET_BYTES:
        return None
    try:
        request = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError):
        return None
    if not isinstance(request, dict):
        return None
    if (
        type(request.get("version")) is not int
        or request.get("protocol") != PROTOCOL
        or request.get("version") != 1
        or request.get("type") != "DISCOVER"
    ):
        return None
    if request.get("serviceName") != SERVICE_NAME:
        return None
    request_id = request.get("requestId")
    if not isinstance(request_id, str) or len(request_id) != 32:
        return None
    if any(character not in "0123456789abcdef" for character in request_id):
        return None
    return request_id


def private_ipv4(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return address in ipaddress.IPv4Network("10.0.0.0/8") or address in ipaddress.IPv4Network(
        "172.16.0.0/12"
    ) or address in ipaddress.IPv4Network("192.168.0.0/16")


def create_handler():
    class ProbeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/local/p0/probe":
                self.send_error(404)
                return
            body = json.dumps({"protocol": PROTOCOL, "ready": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return ProbeHandler


def serve_probe(udp_port: int = 39001, http_port: int = 39002, stop: threading.Event | None = None):
    if not 1 <= udp_port <= 65535 or not 1 <= http_port <= 65535:
        raise ValueError("端口范围须为 1..65535")
    stop = stop or threading.Event()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.bind(("0.0.0.0", udp_port))
        udp.settimeout(0.2)
        http_server = ThreadingHTTPServer(("0.0.0.0", http_port), create_handler())
    except BaseException:
        udp.close()
        raise
    http_server.daemon_threads = True
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    try:
        last_response: dict[tuple[str, int, str], float] = {}
        while not stop.is_set():
            try:
                payload, source = udp.recvfrom(MAX_PACKET_BYTES + 1)
            except socket.timeout:
                continue
            request_id = parse_discover(payload)
            if request_id is None or not private_ipv4(source[0]):
                continue
            now = time.monotonic()
            last_response = {key: value for key, value in last_response.items() if now - value < 3}
            key = (source[0], source[1], request_id)
            if key in last_response or len(last_response) >= MAX_REPLY_RECORDS:
                continue
            last_response[key] = now
            offer = json.dumps(
                {
                    "protocol": PROTOCOL,
                    "version": 1,
                    "type": "P0_OFFER",
                    "serviceName": SERVICE_NAME,
                    "requestId": request_id,
                    "port": http_port,
                    "authenticated": False,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            udp.sendto(offer, source)
    finally:
        udp.close()
        http_server.shutdown()
        http_server.server_close()
        http_thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description="PathAI P0 无凭据局域网能力探测")
    parser.add_argument("--udp-port", type=int, default=39001)
    parser.add_argument("--http-port", type=int, default=39002)
    args = parser.parse_args()
    serve_probe(args.udp_port, args.http_port)


if __name__ == "__main__":
    main()
