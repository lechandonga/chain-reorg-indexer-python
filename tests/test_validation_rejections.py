"""非法/缺字段数据必须被拒绝且原因可区分，不能静默丢弃。"""
from __future__ import annotations

from tests.conftest import feed_in_order


def _post(svc, payload, seq=1):
    return svc.ingest_payload(payload, seq=seq)


def test_missing_height(service):
    r = _post(service, {"hash": "0xA", "transactions": []})
    assert not r.accepted
    assert r.rejection.reason == "missing_field"


def test_missing_hash(service):
    r = _post(service, {"height": 1, "parent_hash": "0xG0"})
    assert r.rejection.reason == "missing_field"


def test_body_not_object(service):
    r = _post(service, [1, 2, 3])
    assert r.rejection.reason == "invalid_format"


def test_wrong_types(service):
    r = _post(service, {"height": "1", "hash": "0xA", "parent_hash": "0xG0"})
    assert r.rejection.reason == "invalid_format"


def test_bool_height_rejected(service):
    r = _post(service, {"height": True, "hash": "0xA"})
    assert r.rejection.reason == "invalid_format"


def test_negative_value(service):
    r = _post(service, {
        "height": 0, "hash": "0xG0",
        "transactions": [{"tx_hash": "t1", "from": "a", "to": "b", "value": -1}],
    })
    assert r.rejection.reason == "invalid_format"


def test_empty_string_hash(service):
    r = _post(service, {"height": 0, "hash": "   "})
    assert r.rejection.reason == "invalid_format"


def test_genesis_with_parent_is_parent_chain_error(service):
    r = _post(service, {"height": 0, "hash": "0xG0", "parent_hash": "0x?"})
    assert r.rejection.reason == "parent_chain_error"


def test_non_genesis_without_parent(service):
    r = _post(service, {"height": 2, "hash": "0xA"})
    assert r.rejection.reason == "parent_chain_error"


def test_tx_block_hash_mismatch(service):
    r = _post(service, {
        "height": 0, "hash": "0xG0",
        "transactions": [
            {"tx_hash": "t1", "from": "a", "to": "b",
             "block_hash": "0xOTHER"},
        ],
    })
    assert r.rejection.reason == "block_txs_mismatch"


def test_duplicate_tx_inside_one_block(service):
    r = _post(service, {
        "height": 0, "hash": "0xG0",
        "transactions": [
            {"tx_hash": "dup", "from": "a", "to": "b"},
            {"tx_hash": "dup", "from": "c", "to": "d"},
        ],
    })
    assert r.rejection.reason == "duplicate_tx"


def test_rejected_payload_is_recorded_and_not_stored(service):
    r = _post(service, {"height": 0, "hash": ""})
    assert not r.accepted
    reasons = [x.reason for x in service.rejections()]
    assert "invalid_format" in reasons
    assert service.store.head() is None


def test_duplicate_tx_across_different_blocks_rejected(service):
    from chain_reorg_indexer.feed import genesis, make_block, make_tx
    g = genesis()
    a = make_block(1, "0xA", g.hash, (make_tx("shared", "0xA", "a", "b"),))
    c = make_block(1, "0xC", g.hash, (make_tx("shared", "0xC", "c", "d"),))
    feed_in_order(service, [g, a], label="跨块重复交易")
    r = service.ingest_block(c, seq=3)
    assert not r.accepted
    assert r.rejection.reason == "duplicate_tx"
