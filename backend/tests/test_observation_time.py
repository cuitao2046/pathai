from datetime import datetime, timedelta, timezone
import pytest
from pathai_backend.observation_time import is_fresh


@pytest.mark.parametrize('offset,valid', [(-16, False), (-15, True), (0, True), (5, True), (6, False)])
def test_observation_window(offset, valid):
    now = datetime.now(timezone.utc)
    assert is_fresh(now + timedelta(seconds=offset), now) is valid


def test_naive_timestamp_is_not_trusted():
    assert not is_fresh(datetime(2026, 11, 15))
