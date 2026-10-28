from unittest.mock import MagicMock

import pytest

from app.database import Database


def test_session_failure_rolls_back_and_closes(settings):
    database = Database(settings)
    session = MagicMock()
    session.__enter__.return_value = session
    database.session_factory = MagicMock(return_value=session)
    try:
        with pytest.raises(RuntimeError), database.session():
            raise RuntimeError("failed use case")
        session.rollback.assert_called_once()
        session.__exit__.assert_called_once()
        session.commit.assert_not_called()
    finally:
        database.dispose()


def test_successful_session_does_not_implicitly_commit(settings):
    database = Database(settings)
    session = MagicMock()
    session.__enter__.return_value = session
    database.session_factory = MagicMock(return_value=session)
    try:
        with database.session():
            pass
        session.commit.assert_not_called()
        session.__exit__.assert_called_once()
    finally:
        database.dispose()
