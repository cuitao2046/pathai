from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select
import pytest
from pathai_backend.database import session_scope
from pathai_backend.errors import ApiError
from pathai_backend.models import AnonymousSubject, LocationSequence
from pathai_backend.routers.location import _advance_sequence, _check_sequence


def subject_id():
    with session_scope() as db:
        return db.scalar(select(AnonymousSubject.id))


def test_stale_error_returns_recovery_watermark(client, auth_headers):
    subject = subject_id()
    with session_scope() as db:
        _advance_sequence(db, subject, 'site', 100)
        with pytest.raises(ApiError) as error:
            _check_sequence(db, subject, 'site', 1)
        assert error.value.details == {'lastSequence': 100}


def test_concurrent_first_inserts_and_updates_never_regress(client, auth_headers):
    subject = subject_id()
    values = [5, 100, 3, 20, 99, 1, 80, 2]
    def advance(value):
        with session_scope() as db:
            _advance_sequence(db, subject, 'site', value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(advance, values))
    with session_scope() as db:
        assert db.get(LocationSequence, {'subject_id': subject, 'site_id': 'site'}).last_sequence == 100


def test_stale_identity_map_cannot_overwrite_newer_sequence(client, auth_headers):
    subject = subject_id()
    with session_scope() as stale:
        _advance_sequence(stale, subject, 'site', 1)
        row = stale.get(LocationSequence, {'subject_id': subject, 'site_id': 'site'})
        with session_scope() as newer:
            _advance_sequence(newer, subject, 'site', 100)
        _advance_sequence(stale, subject, 'site', 2)
        assert row.last_sequence == 100
        assert stale.get(LocationSequence, {'subject_id': subject, 'site_id': 'site'}).last_sequence == 100
