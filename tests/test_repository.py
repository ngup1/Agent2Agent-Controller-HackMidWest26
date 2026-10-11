from uuid import uuid4
from unittest.mock import MagicMock
from backend.database.repository import TranscriptRepository


def setup_cursor_with_rows(rows):
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = rows
    return TranscriptRepository(connection_factory=lambda: connection), cursor


def test_save_segment_checks_business_and_writes_embedding():
    bid = uuid4(); iid = uuid4()
    repo, cursor = setup_cursor_with_rows([(iid,), (0,), (21,)])
    seg = repo.save_segment(bid, iid, "hello", [0.5] * 1024)
    assert seg == 21
    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("business_id=%s" in s for s in sqls)
    assert any("INSERT INTO transcript_embeddings" in s for s in sqls)


def test_get_segment_is_business_scoped():
    bid = uuid4(); iid = uuid4()
    repo, cursor = setup_cursor_with_rows([(4, "hello", "caller", iid)])
    row = repo.get_segment(bid, 4)
    assert row["transcript"] == "hello"
    assert "i.business_id=%s" in cursor.execute.call_args.args[0]


def test_reject_cross_business_save():
    bid = uuid4(); iid = uuid4()
    repo, _ = setup_cursor_with_rows([None])
    try:
        repo.save_segment(bid, iid, "no", [0.5] * 1024)
    except ValueError as exc:
        assert "Unknown interaction" in str(exc)
    else:
        raise AssertionError("Expected tenant-scoped save to fail")
