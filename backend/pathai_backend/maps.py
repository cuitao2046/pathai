from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GEOJSON = ROOT / "result" / "school_building_01_map_v9.geojson"
DEFAULT_BEACONS = ROOT / "result" / "ble_deployment.json"


class MapPackage:
    def __init__(self, geo_path: Path | None = None, beacon_path: Path | None = None):
        self.geo_path = geo_path or Path(os.getenv("PATHAI_GEOJSON_PATH", DEFAULT_GEOJSON))
        self.beacon_path = beacon_path or Path(os.getenv("PATHAI_BEACONS_PATH", DEFAULT_BEACONS))
        self._cached: tuple[int, int, dict[str, Any]] | None = None

    def _validate_package(self, package: dict[str, Any]) -> None:
        if not package.get("siteId") or not package.get("mapVersion") or not package.get("floors"):
            raise ValueError("map package is missing site, version, or floors")
        floor_ids = [floor["floorId"] for floor in package["floors"]]
        if len(floor_ids) != len(set(floor_ids)):
            raise ValueError("duplicate map floor id")
        known_floors = set(floor_ids)
        nodes = package["nodes"]
        node_ids = list(nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate map node id")
        for node_id, node in nodes.items():
            if node_id != node.get("id") or node.get("floorId") not in known_floors:
                raise ValueError(f"invalid map node reference: {node_id}")
            if node.get("type") not in {"room", "doorway", "intersection", "facility", "facility_entrance"}:
                raise ValueError(f"invalid map node type: {node_id}")
            if not all(math.isfinite(float(node.get(axis, math.nan))) for axis in ("x", "y")):
                raise ValueError(f"invalid map node coordinates: {node_id}")
        edge_ids: set[str] = set()
        for edge in package["edges"]:
            if edge.get("from") not in nodes or edge.get("to") not in nodes:
                raise ValueError("map edge references an unknown node")
            if not edge.get("id") or edge["id"] in edge_ids:
                raise ValueError(f"missing or duplicate map edge id: {edge.get('id')}")
            edge_ids.add(edge["id"])
            if edge.get("distance") is not None and (not math.isfinite(float(edge["distance"])) or float(edge["distance"]) < 0):
                raise ValueError("invalid map edge distance")
        place_ids: set[str] = set()
        for place in package["places"]:
            node = nodes.get(place.get("nodeId"))
            if place["placeId"] in place_ids or node is None or place.get("floorId") not in known_floors or node["floorId"] != place["floorId"]:
                raise ValueError("invalid or duplicate map place reference")
            place_ids.add(place["placeId"])
        beacon_ids: set[str] = set()
        hardware_ids: set[tuple[str, int, int]] = set()
        for beacon in package["beacons"]:
            if beacon["beaconId"] in beacon_ids or beacon.get("floorId") not in known_floors:
                raise ValueError("invalid or duplicate beacon reference")
            beacon_ids.add(beacon["beaconId"])
            uuid = str(beacon.get("uuid", "")).upper()
            major, minor = beacon.get("major"), beacon.get("minor")
            if (not re.fullmatch(r"[0-9A-F]{8}-(?:[0-9A-F]{4}-){3}[0-9A-F]{12}", uuid) or
                    not isinstance(major, int) or isinstance(major, bool) or not 0 <= major <= 65535 or
                    not isinstance(minor, int) or isinstance(minor, bool) or not 0 <= minor <= 65535):
                raise ValueError(f"invalid beacon hardware identity: {beacon['beaconId']}")
            hardware_id = (uuid, major, minor)
            if hardware_id in hardware_ids:
                raise ValueError(f"duplicate beacon hardware identity: {beacon['beaconId']}")
            hardware_ids.add(hardware_id)
            if beacon.get("deploymentStatus") == "deployed" and (beacon.get("x") is None or beacon.get("y") is None):
                raise ValueError("deployed beacon is missing coordinates")
            if beacon.get("sourceNodeType") and beacon.get("sourceNodeId") not in nodes:
                raise ValueError("beacon references an unknown source node")
        canonical = {key: value for key, value in package.items() if key != "sha256"}
        digest = sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if package.get("sha256") != digest:
            raise ValueError("map package SHA-256 mismatch")

    def load(self) -> dict[str, Any]:
        stats = (self.geo_path.stat().st_mtime_ns, self.beacon_path.stat().st_mtime_ns)
        if self._cached and self._cached[:2] == stats:
            return self._cached[2]
        geo = json.loads(self.geo_path.read_text(encoding="utf-8"))
        beacon_source = json.loads(self.beacon_path.read_text(encoding="utf-8"))
        floors = []
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        places: list[dict[str, Any]] = []
        walls: list[dict[str, Any]] = []
        for floor_id, floor in geo.get("floors", {}).items():
            for wall in (floor.get("geometry", {}) or {}).get("walls") or []:
                geometry = wall.get("geometry", {})
                if geometry.get("type") == "LineString" and len(geometry.get("coordinates", [])) >= 2:
                    walls.append({"floorId": str(floor_id), "coordinates": geometry["coordinates"],
                                  "thickness": float((wall.get("properties") or {}).get("thickness") or 0.2)})
            topology = floor.get("topology", {})
            for raw in topology.get("nodes", []):
                coords = raw.get("coordinates")
                if not coords or len(coords) < 2:
                    continue
                if raw["id"] in nodes:
                    raise ValueError(f"duplicate map node id: {raw['id']}")
                node = {
                    "id": raw["id"], "type": raw.get("type"), "roomType": raw.get("roomType"),
                    "facilityType": raw.get("facilityType"), "label": raw.get("label") or raw.get("roomType") or raw["id"],
                    "floorId": str(floor_id), "x": float(coords[0]), "y": float(coords[1]),
                    "public": bool(raw.get("public", True)), "accessible": bool(raw.get("accessible", True)),
                    "blindAccessible": bool(raw.get("blindAccessible", True)), "doorType": raw.get("doorType"),
                    "rooms": raw.get("rooms") or [], "roomId": raw.get("roomId"),
                    "isNormallyOpen": raw.get("isNormallyOpen"),
                }
                nodes[node["id"]] = node
                if node["public"] and node["accessible"] and node["type"] in {"room", "facility", "facility_entrance"}:
                    name = str(node["label"]).strip()
                    if name:
                        places.append({"placeId": node["id"], "name": name, "spokenName": name,
                                       "aliases": [], "type": node.get("roomType") or node.get("facilityType") or node["type"],
                                       "floorId": node["floorId"], "nodeId": node["id"], "accessible": node["accessible"]})
            for raw in topology.get("edges", []):
                edges.append({**raw, "floorId": str(floor_id), "crossFloor": bool(raw.get("crossFloor", False))})
            floors.append({"floorId": str(floor_id), "displayName": f"{floor_id}层", "level": int(floor_id)})
        edges.extend(geo.get("crossFloorEdges", []))
        beacons = []
        for raw in beacon_source.get("beacons", []):
            explicit_status = str(raw.get("deploymentStatus", "")).casefold()
            deployed = explicit_status in {"installed", "deployed", "active"}
            planned_coords = raw.get("originalPlannedCoordinates") or raw.get("coordinates")
            deployed_coords = raw.get("coordinates") if deployed else None
            if not planned_coords and not deployed_coords:
                continue
            beacons.append({"beaconId": raw["beaconId"], "uuid": str(raw.get("uuid", "")).upper(),
                            "major": raw.get("major"), "minor": raw.get("minor"),
                            "x": float(deployed_coords[0]) if deployed_coords else None,
                            "y": float(deployed_coords[1]) if deployed_coords else None,
                            "floorId": str(raw.get("floor", "")), "txPower": int(raw.get("txPower", -59)),
                            "plannedCoordinates": planned_coords,
                            "sourceNodeId": raw.get("sourceNodeId"), "installDate": raw.get("installDate"),
                            "deploymentStatus": "deployed" if deployed else "unverified", "source": "beacon_deployment"})
        package = {
            "schemaVersion": "1.0.0", "siteId": geo["venueId"], "mapVersion": geo["version"],
            "publishedAt": beacon_source.get("generatedAt"),
            "coordinateSystem": {"unit": "m", "originDescription": "扩展 GeoJSON 局部坐标系", "headingReference": "map_north"},
            "floors": sorted(floors, key=lambda f: f["level"]), "places": sorted(places, key=lambda p: (p["floorId"], p["name"], p["placeId"])),
            "nodes": nodes, "edges": edges, "walls": walls, "beacons": beacons,
            "source": {"geojsonVersion": geo["version"], "beaconSchemaVersion": beacon_source.get("schemaVersion")},
        }
        canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        package["sha256"] = sha256(canonical).hexdigest()
        self._validate_package(package)
        self._cached = (stats[0], stats[1], package)
        return package

    def manifest(self) -> dict[str, Any]:
        package = self.load()
        return {key: package[key] for key in ("siteId", "mapVersion", "publishedAt", "coordinateSystem", "floors", "sha256")}


map_package = MapPackage()
