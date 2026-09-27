"""链重组：短分叉被更长链顶替时干净回滚，结果确定一致。"""

from __future__ import annotations

from chain_indexer.query import QueryService

from .conftest import make_block


def _build_fork(ingester, *, short=("0x2a",), long_=("0x2b", "0x3b")):
    """构建: 0-1，短尾 2a，长尾 2b-3b。返回 QueryService。"""
    ingester.feed(make_block(0, "0x00", "0x0", {"0x60d0": ("0xa0", "0xb0", 1)}))
    ingester.feed(make_block(1, "0x11", "0x00"))
    ingester.feed(make_block(2, short[0], "0x11",
                             {"0x7aaa": ("0xaa", "0xba", 10)}))
    ingester.feed(make_block(2, long_[0], "0x11",
                             {"0x7bbb": ("0xab", "0xbb", 20)}))
    ingester.feed(make_block(3, long_[1], long_[0],
                             {"0x7ccc": ("0xac", "0xbc", 30)}))
    return QueryService(ingester.store)


def test_longer_chain_replaces_shorter(ingester):
    q = _build_fork(ingester)
    # 重组前短链是规范链（等长不重组），长链接出第三块后才顶替
    assert ingester.store.get_canonical_hash(2) == "0x2b"
    assert ingester.store.get_canonical_hash(3) == "0x3b"
    assert q.chain_status()["tip_hash"] == "0x3b"


def test_rolled_back_tx_indexes_are_gone(ingester):
    q = _build_fork(ingester)
    # 短链块的交易索引被清除，且无地址倒排残留
    assert q.tx_by_hash("0x7aaa") is None
    assert ingester.store.fetch_txs_by_address("0xaa") == []
    # 新链交易可查
    assert q.tx_by_hash("0x7bbb")["block_hash"] == "0x2b"
    assert q.tx_by_hash("0x7ccc")["block_hash"] == "0x3b"
    # 共享前缀的交易仍在
    assert q.tx_by_hash("0x60d0") is not None


def test_rolled_back_block_hidden_from_queries(ingester):
    q = _build_fork(ingester)
    assert q.block_by_height(2)["hash"] == "0x2b"
    assert q.block_by_hash("0x2a") is None  # 已回滚块对外不可见
    assert q.block_by_height(3)["hash"] == "0x3b"


def test_equal_length_fork_does_not_reorg(ingester):
    ingester.feed(make_block(0, "0x00", "0x0"))
    ingester.feed(make_block(1, "0x11", "0x00"))
    r1 = ingester.feed(make_block(2, "0x2a", "0x11"), label="短链先到")
    r2 = ingester.feed(make_block(2, "0x2b", "0x11"), label="等长分叉")
    assert r1.new_tip == "0x2a"
    assert not r2.reorg  # 等长保留现有链头，不抖动
    assert ingester.store.get_canonical_hash(2) == "0x2a"


def test_reorg_is_atomic_no_half_state(ingester):
    # 新长尾里含与共享前缀重复的交易 -> 整个重组拒绝，状态停留在重组前
    ingester.feed(make_block(0, "0x00", "0x0", {"0xd0c0": ("0xa0", "0xb0", 1)}))
    ingester.feed(make_block(1, "0x11", "0x00"))
    ingester.feed(make_block(2, "0x2a", "0x11"))
    ingester.feed(make_block(2, "0x2b", "0x11", {"0xd0c0": ("0xa1", "0xb1", 2)}))
    r = ingester.feed(make_block(3, "0x3b", "0x2b"), label="触发重组但交易冲突")
    assert not r.accepted
    assert r.reason.value == "duplicate_tx_on_chain"
    # 链头/索引完全停留在短链，没有写一半
    assert ingester.store.get_canonical_hash(2) == "0x2a"
    assert ingester.store.get_canonical_hash(3) is None
    # 冲突发生在新长尾（0x2b 的 0x7bbb 仍被前缀检查拦下，未写入索引）
    assert ingester.store.fetch_tx("0x7bbb") is None


def test_reorg_then_rollback_forward_is_deterministic(ingester):
    # 短链->长链A 重组；再出现更长链 B，最终以 B 为准
    ingester.feed(make_block(0, "0x00", "0x0"))
    ingester.feed(make_block(1, "0x11", "0x00"))
    ingester.feed(make_block(2, "0x2a", "0x11", {"0xaaaa": ("0xa1", "0xb1", 1)}))
    ingester.feed(make_block(2, "0x2b", "0x11", {"0xbbbb": ("0xa1", "0xb1", 1)}))
    ingester.feed(make_block(3, "0x3b", "0x2b"))
    assert ingester.store.get_tip_height() == 3
    # 再造一条更长的 B：2c-3c-4c
    ingester.feed(make_block(2, "0x2c", "0x11", {"0xcccc": ("0xa1", "0xb1", 1)}))
    ingester.feed(make_block(3, "0x3c", "0x2c"))
    r4 = ingester.feed(make_block(4, "0x4c", "0x3c"))
    assert r4.reorg and r4.new_tip == "0x4c"
    assert ingester.store.get_canonical_hash(2) == "0x2c"
    assert ingester.store.fetch_tx("0xbbbb") is None
    assert ingester.store.fetch_tx("0xcccc") is not None
