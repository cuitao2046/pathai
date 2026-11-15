"""Bound trusted sensor observations independently of request sequence numbers."""
from datetime import datetime, timezone

MAX_OBSERVATION_AGE_S = 15.0
MAX_FUTURE_SKEW_S = 5.0


def is_fresh(observed: datetime, now: datetime | None = None) -> bool:
    if observed.tzinfo is None or observed.utcoffset() is None:
        return False
    age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
    return -MAX_FUTURE_SKEW_S <= age <= MAX_OBSERVATION_AGE_S
