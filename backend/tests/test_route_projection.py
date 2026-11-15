from types import SimpleNamespace
import pytest
from pathai_backend.routers.nav import _nearest_on_route


def route(*points):
    return SimpleNamespace(polyline=[SimpleNamespace(x=x, y=y, floorId=floor) for x, y, floor in points])


@pytest.mark.parametrize('x,y,distance,remaining', [(10,0,0,10), (10,3,3,13), (-2,0,2,22), (22,0,2,2)])
def test_long_segment_projection(x,y,distance,remaining):
    result = _nearest_on_route(route((0,0,'1'), (20,0,'1')), '1', x,y)
    assert result[1] == pytest.approx(distance)
    assert result[2] == pytest.approx(remaining)


def test_corner_remaining_uses_polyline():
    result = _nearest_on_route(route((0,0,'1'), (20,0,'1'), (20,10,'1')), '1', 10,0)
    assert result[1:] == (0,20)


def test_no_projection_across_floors():
    result = _nearest_on_route(route((0,0,'1'), (20,0,'2')), '1', 10,0)
    assert result[1] == 10
    assert _nearest_on_route(route((0,0,'1'), (20,0,'2')), '3', 10,0) is None


def test_zero_length_segment():
    assert _nearest_on_route(route((0,0,'1'), (0,0,'1')), '1', 3,4)[1:] == (5,5)
