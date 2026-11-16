from datetime import datetime, timedelta, timezone

import pytest

from pathai_backend.observation_time import (
    GUIDANCE_MAX_AGE_S,
    SANITY_MAX_AGE_S,
    is_fresh_for_guidance,
    is_sane,
    observation_age_s,
)


def test_observation_age_seconds():
    now = datetime.now(timezone.utc)
    assert observation_age_s(now - timedelta(seconds=18), now) == pytest.approx(18.0)
    assert observation_age_s(datetime(2026, 11, 15), now) is None


@pytest.mark.parametrize('offset,valid', [
    # offset 为「相对 now 的时间戳偏移」：负=过去，正=未来
    (60, True), (61, False), (0, True), (-18, True), (-299, True), (-301, False),
])
def test_sanity_window_only_rejects_clearly_invalid(offset, valid):
    now = datetime.now(timezone.utc)
    assert is_sane(now + timedelta(seconds=offset), now) is valid


def test_sanity_limit_is_far_beyond_freshness_limit():
    # 18 秒的起点不再被拒绝，只由客户端判断是否提示重新定位
    now = datetime.now(timezone.utc)
    assert is_sane(now - timedelta(seconds=18), now)
    assert SANITY_MAX_AGE_S > GUIDANCE_MAX_AGE_S


@pytest.mark.parametrize('offset,valid', [(-16, False), (-15, True), (0, True), (5, True), (6, False)])
def test_guidance_window(offset, valid):
    now = datetime.now(timezone.utc)
    assert is_fresh_for_guidance(now + timedelta(seconds=offset), now) is valid


def test_naive_timestamp_is_neither_sane_nor_fresh():
    naive = datetime(2026, 11, 15)
    assert not is_sane(naive)
    assert not is_fresh_for_guidance(naive)
