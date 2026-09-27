"""链重组：短分叉被更长链顶替时，旧块/旧交易索引干净回滚，结果确定。"""
from __future__ import annotations

from chain_reorg_indexer.feed import chain, genesis, make_block, make_tx
from tests.conftest import feed_in_order


def _tx_factory(prefix: str):
    def factory(height: int, block_hash: str):
        return (make_tx(f"{prefix}-tx{height}", block_hash,
                        f"{prefix}-from{height}", f"{prefix}-to{height}"),)
    return factory


def test_longer_chain_replaces_short_fork(service):
    g = genesis()
    # 主链 A：g -> A1 -> A2（累计工作量 3）
    a1, a2 = chain("A", 2, parent=g, tx_factory=_tx_factory("A"))
    feed_in_order(service, [g, a1, a2], label="主链A")
    assert service.store.head().hash == "A-h2"
    assert service.get_tx("A-tx1").height == 1
    assert service.get_tx("A-tx2").height == 2

    # 分叉 B 到达：每个块工作量更大，g -> B1 -> B2 -> B3（累计 6 > 3）
    b1, b2, b3 = chain("B", 3, parent=g, work_step=2,
                       tx_factory=_tx_factory("B"))
    results = feed_in_order(service, [b1, b2, b3], start=4, label="分叉B")
    # B-h2 落库时工作量 5 超过 A-h2 的 3 -> 此刻发生重组；B-h3 只是正常延长
    assert results[0].reorged is False
    assert results[1].reorged is True
    assert results[2].reorged is False
    head = service.store.head()
    assert (head.height, head.hash) == (3, "B-h3")

    # 旧链交易在规范查询中彻底不可见（无孤儿交易）
    assert service.get_tx("A-tx1") is None
    assert service.get_tx("A-tx2") is None
    assert service.get_tx("B-tx3").height == 3

    # 地址查询也不能出现已回滚的交易
    froms = [v.tx.tx_hash for v in service.get_txs_by_address("A-from1")]
    assert froms == []
    assert [v.tx.tx_hash for v in
            service.get_txs_by_address("B-to2")] == ["B-tx2"]

    # 每个高度在规范链上唯一
    conn = service.store._conn
    dup = conn.execute(
        "SELECT height, COUNT(*) c FROM canonical GROUP BY height HAVING c>1"
    ).fetchall()
    assert dup == []
    # 被替换的块仍保留在 blocks 中（分叉历史），但不在 canonical
    assert conn.execute(
        "SELECT COUNT(*) FROM blocks WHERE hash='A-h2'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM canonical WHERE block_hash='A-h2'"
    ).fetchone()[0] == 0


def test_shorter_fork_does_not_reorg(service):
    g = genesis()
    a1, a2 = chain("A", 2, parent=g, tx_factory=_tx_factory("A"))
    feed_in_order(service, [g, a1, a2], label="主链A")
    # 等长但工作量更小的分叉：不应顶替
    c1 = make_block(1, "C-h1", g.hash,
                    (make_tx("C-tx1", "C-h1", "c", "d"),), chainwork=1)
    c2 = make_block(2, "C-h2", "C-h1",
                    (make_tx("C-tx2", "C-h2", "c", "d"),), chainwork=2)
    feed_in_order(service, [c1, c2], start=3, label="短分叉C")
    assert service.store.head().hash == "A-h2"
    assert service.get_tx("C-tx1") is None
    assert service.get_tx("A-tx2") is not None


def test_reorg_then_old_chain_extends_and_replaces_back(service):
    g = genesis()
    a1, a2 = chain("A", 2, parent=g, tx_factory=_tx_factory("A"))
    b1, b2, b3 = chain("B", 3, parent=g, work_step=2,
                       tx_factory=_tx_factory("B"))
    feed_in_order(service, [g, a1, a2], label="A先到")
    feed_in_order(service, [b1, b2, b3], start=3, label="B更长顶替")
    assert service.store.head().hash == "B-h3"

    # A 链继续延长到更高工作量（每步 +3，A4=6,A5=9 超过 B-h3 的 6）
    a3 = make_block(3, "A-h3", "A-h2",
                    (make_tx("A-tx3", "A-h3", "a", "d"),), chainwork=6)
    a4 = make_block(4, "A-h4", "A-h3",
                    (make_tx("A-tx4", "A-h4", "a", "d"),), chainwork=9)
    feed_in_order(service, [a3, a4], start=6, label="A反超")
    head = service.store.head()
    assert (head.height, head.hash) == (4, "A-h4")
    # B 链交易全部回滚，A 链交易恢复可见
    assert service.get_tx("B-tx3") is None
    assert service.get_tx("A-tx1").height == 1
    assert service.get_tx("A-tx4").height == 4


def test_equal_work_tie_break_is_deterministic(service):
    """同等工作量时以 tip hash 字典序更小者胜，与投放顺序无关。"""
    g = genesis()
    z1 = make_block(1, "Z-h1", g.hash, chainwork=2)
    z2 = make_block(2, "Z-h2", "Z-h1", chainwork=3)
    a1 = make_block(1, "A-h1", g.hash, chainwork=2)
    a2 = make_block(2, "A-h2", "A-h1", chainwork=3)
    feed_in_order(service, [g, z1, z2], label="先Z")
    feed_in_order(service, [a1, a2], start=4, label="后A(同工作量)")
    assert service.store.head().hash == "A-h2"  # A-h2 < Z-h2


def test_no_half_state_after_failed_block(service):
    """整批写入失败（如跨块重复交易）时事务回滚，不残留半块。"""
    g = genesis()
    a1 = make_block(1, "A-h1", g.hash,
                    (make_tx("shared", "A-h1", "a", "b"),))
    feed_in_order(service, [g, a1], label="基础")
    bad = make_block(1, "D-h1", g.hash,
                     (make_tx("shared", "D-h1", "c", "d"),))
    r = service.ingest_block(bad, seq=3)
    assert not r.accepted
    conn = service.store._conn
    assert conn.execute(
        "SELECT COUNT(*) FROM blocks WHERE hash='D-h1'").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE block_hash='D-h1'"
    ).fetchone()[0] == 0
    assert service.store.head().hash == "A-h1"
