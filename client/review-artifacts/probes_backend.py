"""Isolated review probes: passing means the described defect is reproducible."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pathai_backend.auth import get_current_user
from pathai_backend.errors import ApiError
from pathai_backend.nav_text import _validated_texts
from pathai_backend.routers.nav import _nearest_on_route
from pathai_backend.schemas.location import LocationEstimateRequest


def test_rewrite_accepts_opposite_direction():
    route = SimpleNamespace(segments=[SimpleNamespace(instructionId='ins_a', text='前方5米向左转')])
    result = _validated_texts({'instructions': [{'instructionId': 'ins_a', 'text': '前方5米向右转'}]}, route)
    assert result == {'ins_a': '前方5米向右转'}


def test_nearest_route_uses_vertices_not_segments():
    route = SimpleNamespace(polyline=[SimpleNamespace(floorId='1', x=0, y=0), SimpleNamespace(floorId='1', x=20, y=0)])
    assert _nearest_on_route(route, '1', 10, 0)[1] == 10


def test_surroundings_request_rejected():
    with pytest.raises(ValidationError) as info:
        LocationEstimateRequest.model_validate({
            'siteId': 'x', 'mapVersion': 'v', 'sequence': 1,
            'collectedAt': '2026-11-15T00:00:00Z',
            'beacons': [{'beaconId': 'b', 'rssi': -60, 'observedAt': '2026-11-15T00:00:00Z'}],
            'includeSurroundings': True,
        })
    assert any(e['type'] == 'extra_forbidden' and e['loc'] == ('includeSurroundings',) for e in info.value.errors())


def test_missing_client_bearer_rejected():
    with pytest.raises(ApiError) as info:
        get_current_user(None, None, None)
    assert info.value.code == 'UNAUTHENTICATED'
