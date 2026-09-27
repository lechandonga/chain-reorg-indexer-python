"""对外查询：只反映当前链头，重组后旧数据不可见，缺失对象返回 404 语义。"""

from __future__ import annotations

from chain_indexer.query import QueryService

from .conftest import make_block


def _reorg_scenario(ingester):
    q = QueryService(ingester.store)
    ingester.feed(make_block(0, "0x00", "0x0",
                             {"0x60d0": ("0x5eade2", "0x2ec0", 1)}))
    ingester.feed(make_block(1, "0x11", "0x00"))
    ingester.feed(make_block(2, "0x2a", "0x11",
                             {"0xa001": ("0x5eade2", "0x01dd", 5)}))
    ingester.feed(make_block(2, "0x2b", "0x11",
                             {"0xb001": ("0x5eade2", "0x17e4", 9)}))
    ingester.feed(make_block(3, "0x3b", "0x2b"))
    return q


def test_tx_lookup_follows_canonical_chain(ingester):
    q = _reorg_scenario(ingester)
    assert q.tx_by_hash("0xa001") is None      # 被回滚
    assert q.tx_by_hash("0xb001")["block_hash"] == "0x2b"
    assert q.tx_by_hash("0x60d0")["block_hash"] == "0x00"


def test_address_lookup_only_canonical(ingester):
    q = _reorg_scenario(ingester)
    hashes = {t["tx_hash"] for t in q.txs_by_address("0x5eade2")["transactions"]}
    assert hashes == {"0x60d0", "0xb001"}  # 旧分叉上的 0xa001 不在其中
    assert q.txs_by_address("0x01dd")["transactions"] == []


def test_height_and_hash_lookup(ingester):
    q = _reorg_scenario(ingester)
    assert q.block_by_height(2)["hash"] == "0x2b"
    assert q.block_by_hash("0x2a") is None
    assert q.block_by_hash("0x2b")["height"] == 2
    assert q.block_by_height(99) is None


def test_status_tip(ingester):
    q = _reorg_scenario(ingester)
    status = q.chain_status()
    assert status == {"tip_height": 3, "tip_hash": "0x3b"}
