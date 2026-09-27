"""重复投放：同高度 / 同交易重复投放不能产生重复数据，且结果幂等。"""

from __future__ import annotations

from .conftest import make_block


def test_same_block_twice_is_idempotent(ingester):
    g = make_block(0, "0x00", "0x0", {"0xaaaa": ("0xa1", "0xb1", 10)})
    r1 = ingester.feed(g, label="genesis#1")
    r2 = ingester.feed(g, label="genesis#2")
    assert r1.kind.value == "accepted_new"
    assert r2.kind.value == "accepted_duplicate"
    assert r2.new_tip == r1.new_tip
    # 交易只有一条
    txs = ingester.store.fetch_txs_by_address("0xa1")
    assert len(txs) == 1
    assert txs[0]["tx_hash"] == "0xaaaa"


def test_duplicate_tx_delivery_via_block_unchanged(ingester):
    # 同一区块（携带同一笔交易）重放三次，交易行数恒为 1
    g = make_block(0, "0x00", "0x0", {"0xaaaa": ("0xa1", "0xb1", 10)})
    for _ in range(3):
        ingester.feed(g)
    assert ingester.store.fetch_tx("0xaaaa") is not None
    assert len(ingester.store.fetch_txs_by_address("0xb1")) == 1


def test_same_hash_with_mutated_payload_rejected(ingester):
    g1 = make_block(0, "0x00", "0x0", {"0xaaaa": ("0xa1", "0xb1", 10)})
    g2 = make_block(0, "0x00", "0x0", {"0xaaaa": ("0xa1", "0xb1", 11)})
    ingester.feed(g1)
    r = ingester.feed(g2, label="同哈希改金额")
    assert not r.accepted
    assert r.reason.value == "bad_value"
    # 原数据未被覆盖
    assert ingester.store.fetch_tx("0xaaaa")["amount"] == 10


def test_same_height_distinct_hash_is_a_fork_not_duplicate(ingester):
    # 同高度不同哈希不是重复数据，应作为分叉归档
    a = make_block(0, "0xaa", "0x0")
    b = make_block(0, "0xbb", "0x0")
    ingester.feed(a)
    r = ingester.feed(b, label="第二个创世")
    assert not r.accepted
    assert r.reason.value == "duplicate_genesis"


def test_duplicate_block_after_reorg_still_idempotent(ingester):
    # 建链 0-1-2a，随后长链 2b-3b 顶替；再重复投放 2a 不应报错/重复
    ingester.feed(make_block(0, "0x00", "0x0"))
    ingester.feed(make_block(1, "0x11", "0x00"))
    ingester.feed(make_block(2, "0x2a", "0x11", {"0x7aaa": ("0xa1", "0xb1", 1)}))
    ingester.feed(make_block(2, "0x2b", "0x11", {"0x7bbb": ("0xa2", "0xb2", 2)}))
    ingester.feed(make_block(3, "0x3b", "0x2b", {"0x7ccc": ("0xa3", "0xb3", 3)}))
    assert ingester.store.get_canonical_hash(2) == "0x2b"

    r = ingester.feed(make_block(2, "0x2a", "0x11", {"0x7aaa": ("0xa1", "0xb1", 1)}))
    assert r.kind.value == "accepted_duplicate"
    # 已回滚的交易不会因为重复投放而复活
    assert ingester.store.fetch_tx("0x7aaa") is None
