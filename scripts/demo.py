"""本地一键演示：乱序、重复、非法拒绝、链重组全链路（不依赖 HTTP）。

运行：uv run python scripts/demo.py
"""
from __future__ import annotations

import logging
import sys

sys.path.insert(0, ".")

from chain_reorg_indexer.feed import (  # noqa: E402
    block_to_payload, chain, genesis, make_block, make_tx,
)
from chain_reorg_indexer.service import IndexerService  # noqa: E402
from chain_reorg_indexer.store import IndexStore  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(message)s", stream=sys.stdout)


def show(svc: IndexerService, title: str) -> None:
    h = svc.store.head()
    print(f"\n===== {title} =====")
    print(f"链头: {('- 无规范链 -' if h is None else f'{h.height}/{h.hash} (work={h.chainwork})')}")


def main() -> None:
    svc = IndexerService(IndexStore(":memory:"))
    g = genesis()
    a = chain("A", 3, parent=g, tx_factory=lambda h, bh: (
        make_tx(f"A-tx{h}", bh, f"alice{h}", "pool"),))
    b = chain("B", 2, parent=g, work_step=3, tx_factory=lambda h, bh: (
        make_tx(f"B-tx{h}", bh, "bob", "pool"),))

    show(svc, "1. 乱序到达（先投 A-h2、A-h1，最后才到创世）")
    for seq, blk in enumerate((a[1], a[0], g), 1):
        r = svc.ingest_payload(block_to_payload(blk), seq=seq)
        print(f"  seq={seq} 投放 {blk.height}/{blk.hash} -> "
              f"{'接受' if r.accepted else '拒绝:' + r.rejection.reason}")
    print("  补投 A-h3：")
    r = svc.ingest_payload(block_to_payload(a[2]), seq=4)
    print(f"  -> 接受, reorg={r.reorged}; 链头应为 3/A-h3")

    show(svc, "2. 重复投放 A-h3（幂等，不产生重复数据）")
    r = svc.ingest_payload(block_to_payload(a[2]), seq=5)
    n = svc.store._conn.execute(
        "SELECT COUNT(*) FROM blocks WHERE hash='A-h3'").fetchone()[0]
    print(f"  重复投放 accepted={r.accepted}; A-h3 在 blocks 中行数={n}（应为 1）")

    show(svc, "3. 非法数据：不同原因分别拒绝")
    bad_cases = [
        ({"height": 1}, "缺 hash"),
        ({"height": "x", "hash": "z"}, "height 类型错"),
        ({"height": 2, "hash": "z"}, "非创世缺 parent_hash"),
    ]
    for seq, (payload, label) in enumerate(bad_cases, 6):
        r = svc.ingest_payload(payload, seq=seq)
        print(f"  [{label}] -> reason={r.rejection.reason}")

    show(svc, "4. 链重组：工作量更大的 B 链（2 块，累计 work=7）顶替 A（work=4）")
    for seq, blk in enumerate(b, 9):
        r = svc.ingest_payload(block_to_payload(blk), seq=seq)
        print(f"  投放 {blk.height}/{blk.hash} -> reorg={r.reorged}")
    h = svc.store.head()
    print(f"  当前链头: {h.height}/{h.hash}")
    print(f"  A-tx1 查询（应回滚不可见）: {svc.get_tx('A-tx1')}")
    print(f"  B-tx2 查询（应可见, height=2）: {svc.get_tx('B-tx2')}")
    print(f"  地址 alice1 相关交易（应为空）: "
          f"{[v.tx.tx_hash for v in svc.get_txs_by_address('alice1')]}")
    print("  /rejections 记录的原因:",
          sorted({x.reason for x in svc.rejections()}))


if __name__ == "__main__":
    main()
