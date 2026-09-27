"""边写边查：并发重组期间，每个查询要么看到旧链要么看到新链，绝不半截。

读方使用独立连接（WAL 模式下只能读到已提交快照），与真实查询客户端一致。
"""
from __future__ import annotations

import threading

from chain_reorg_indexer.feed import block_to_payload, chain, genesis
from chain_reorg_indexer.service import IndexerService
from chain_reorg_indexer.store import IndexStore


def test_concurrent_writes_and_reads_always_consistent(tmp_path):
    db = str(tmp_path / "conc.db")
    writer_store = IndexStore(db)
    reader_store = IndexStore(db)  # 独立连接，只读已提交快照
    svc = IndexerService(writer_store)
    reader_svc = IndexerService(reader_store)
    g = genesis()
    svc.ingest_payload(block_to_payload(g), seq=0)

    stop = threading.Event()
    errors: list[Exception] = []

    def reader():
        while not stop.is_set():
            try:
                head = reader_store.head()
                if head is None:
                    continue
                # 不变量 1：规范链从高度 0 连续到链头
                for h in range(head.height + 1):
                    blk = reader_svc.get_block_by_height(h)
                    assert blk is not None, f"高度 {h} 在链头 {head} 下缺失"
                # 不变量 2：链头块的每笔交易都可查且挂在链头高度
                head_block = reader_svc.get_block_by_height(head.height)
                for tx in head_block.transactions:
                    view = reader_svc.get_tx(tx.tx_hash)
                    assert view is not None, f"交易 {tx.tx_hash} 不可见"
                    assert view.height == head.height
                    assert view.tx.block_hash == head_block.hash
                # 不变量 3：规范表每个高度唯一
                dup = reader_store._conn.execute(
                    "SELECT height FROM canonical "
                    "GROUP BY height HAVING COUNT(*) > 1"
                ).fetchall()
                assert dup == []
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    # 两条分叉轮流延长、反复互相顶替，制造持续重组
    a = chain("A", 20, parent=g, work_step=2)
    b = chain("B", 20, parent=g, work_step=3)
    seq = 1
    for aa, bb in zip(a, b):
        svc.ingest_payload(block_to_payload(aa), seq=seq)
        seq += 1
        svc.ingest_payload(block_to_payload(bb), seq=seq)
        seq += 1

    stop.set()
    t.join(timeout=10)
    assert errors == [], errors[:3]
    # B 每步工作量更大，最终 B 为规范链
    assert writer_store.head().hash == "B-h20"
    writer_store.close()
    reader_store.close()
