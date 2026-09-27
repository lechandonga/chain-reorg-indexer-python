"""乱序到达 / 重复投放：最终状态一致、不丢数据、链头不倒退。"""
from __future__ import annotations

from chain_reorg_indexer.feed import chain, genesis, make_block, make_tx
from tests.conftest import feed_in_order


def test_out_of_order_arrival_eventually_forms_chain(service):
    g = genesis()
    a1, a2, a3 = chain("A", 3, parent=g, tx_factory=lambda h, bh: (
        make_tx(f"t{h}", bh, f"addr{h}", "pool"),))

    # 故意乱序：h2 -> h3 -> h1 -> 创世
    results = feed_in_order(service, [a2, a3, a1, g], label="乱序")
    assert [r.accepted for r in results] == [True, True, True, True]
    # 在创世到达之前没有规范链；之后级联到 h3，链头不倒退
    assert service.store.head().hash == "A-h3"
    assert service.store.head().height == 3
    for h in range(4):
        assert service.get_block_by_height(h) is not None
    assert service.get_tx("t2").height == 2


def test_head_never_goes_backwards_while_receiving(service):
    g = genesis()
    a1, a2 = chain("A", 2, parent=g)
    feed_in_order(service, [g, a1, a2], label="正序")
    head_before = service.store.head()

    # 之后才到达的"旧块"（高度更低 / 孤儿）不能让链头倒退
    late = make_block(1, "LATE-h1", g.hash, chainwork=1)
    r = service.ingest_block(late, seq=9)
    assert r.accepted
    assert service.store.head() == head_before


def test_duplicate_block_same_payload_is_idempotent(service):
    g = genesis()
    a1 = make_block(1, "0xA", g.hash,
                    (make_tx("t1", "0xA", "alice", "bob"),))
    feed_in_order(service, [g, a1], label="首次")
    first = service.store.head()

    results = feed_in_order(service, [a1, a1], start=3, label="重复投放")
    assert all(r.accepted for r in results)
    # 没有重复块、没有重复交易
    import sqlite3
    conn = service.store._conn
    n_blocks = conn.execute(
        "SELECT COUNT(*) FROM blocks WHERE hash='0xA'").fetchone()[0]
    n_txs = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE tx_hash='t1'").fetchone()[0]
    assert n_blocks == 1
    assert n_txs == 1
    assert service.store.head() == first


def test_same_height_same_hash_conflicting_payload_rejected(service):
    g = genesis()
    a1 = make_block(1, "0xA", g.hash, chainwork=2)
    a1_conflict = make_block(1, "0xA", g.hash, chainwork=99)
    feed_in_order(service, [g, a1], label="原始")
    r = service.ingest_block(a1_conflict, seq=3)
    assert not r.accepted
    assert r.rejection.reason == "duplicate_block"
    assert service.store.head().chainwork == 2


def test_orphan_and_then_its_parent_arrives(service):
    g = genesis()
    a1 = make_block(1, "A-h1", g.hash)
    a2 = make_block(2, "A-h2", "A-h1")
    # 先到 h2（父 h1 未知）：接受但保持孤儿，不上规范链、查询不可见
    r2 = service.ingest_block(a2, seq=1)
    assert r2.accepted
    assert service.store.head() is None
    assert service.get_block_by_height(2) is None
    # 再到 g, h1：连通性级联，h2 自动上线
    feed_in_order(service, [g, a1], start=2, label="父块补投")
    head = service.store.head()
    assert (head.height, head.hash) == (2, "A-h2")
