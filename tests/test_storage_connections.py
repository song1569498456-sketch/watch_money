"""Database handles must close on all paths, including Windows file cleanup."""
import sqlite3

import pytest

from watch_money.storage import Storage


def test_connection_commits_then_closes_even_when_caller_retains_handle(tmp_path):
    store = Storage(tmp_path)
    with store.connect() as db:
        db.execute('INSERT INTO kv (key, value) VALUES (?, ?)', ('saved', '123'))
    assert store.get('saved') == 123
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')


def test_connection_rolls_back_then_closes_on_error(tmp_path):
    store = Storage(tmp_path)
    with pytest.raises(RuntimeError, match='abort transaction'):
        with store.connect() as db:
            db.execute('INSERT INTO kv (key, value) VALUES (?, ?)', ('aborted', '123'))
            raise RuntimeError('abort transaction')
    assert store.get('aborted') is None
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
