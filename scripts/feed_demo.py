#!/usr/bin/env python3
"""本地模拟区块投放演示。

不依赖真实网络，直接用服务层喂入本地构造的模拟区块，覆盖：
乱序到达、重复投放、等长分叉不重组、长链顶替短链（reorg）、按地址/哈希查询。

运行：
    .venv/bin/python scripts/feed_demo.py
    .venv/bin/python scripts/feed_demo.py --db ./data/demo.db   # 落盘持久化
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chain_indexer.query import QueryService
from chain_indexer.service import IngestService
from chain_indexer.store import Store

ALICE, BOB, CAROL = "0xa11ce", "0xb0b", "0xca201"


def block(height: int, hash_: str, parent: str, txs=None):
    return {
        "height": height,
        "hash": hash_,
        "parent_hash": parent,
        "transactions": txs or [],
    }


def tx(tx_hash: str, frm: str, to: str, amount: int):
    return {"tx_hash": tx_hash, "from": frm, "to": to, "amount": amount}


def main() -> None:
    parser = argparse.ArgumentParser(description="本地链重组索引演示")
    parser.add_argument("--db", default=":memory:", help="SQLite 路径，默认内存")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    store = Store(args.db)
    ingest = IngestService(store)
    query = QueryService(store)

    def feed(seq, blk, note=""):
        print(f"\n--- 投放 seq={seq} height={blk['height']} hash={blk['hash']} {note}")
        r = ingest.ingest(blk, seq=seq)
        print(f"    -> kind={r.kind.value} accepted={r.accepted} "
              f"reason={r.reason.value if r.reason else '-'} "
              f"reorg={r.reorg} tip={r.new_tip}")
        return r

    print("=== 1) 乱序：先到高度1（父未知），后补创世 ===")
    feed(1, block(1, "0x11", "0x00"), "父块缺失，应暂存")
    feed(2, block(0, "0x00", "0x0", [tx("0x7001", ALICE, BOB, 100)]), "补创世，自动连通")

    print("\n=== 2) 重复投放同一区块（幂等，无重复数据）===")
    feed(3, block(0, "0x00", "0x0", [tx("0x7001", ALICE, BOB, 100)]), "重复")

    print("\n=== 3) 短分叉 A 与等长分叉 B（等长不重组）===")
    feed(4, block(2, "0x2a", "0x11", [tx("0x7a01", ALICE, CAROL, 5)]), "短链A")
    feed(5, block(2, "0x2b", "0x11", [tx("0x7b01", BOB, CAROL, 7)]), "等长分叉B，不重组")

    print("\n=== 4) 长分叉 B 继续延伸，顶替 A（reorg）===")
    feed(6, block(3, "0x3b", "0x2b", [tx("0x7b02", CAROL, ALICE, 2)]), "B更长，重组")

    print("\n=== 最终状态 ===")
    print("链头:", query.chain_status())
    print("高度2区块:", query.block_by_height(2)["hash"])
    print("旧分叉交易 0x7a01:", query.tx_by_hash("0x7a01"))
    print("新链交易 0x7b01:", query.tx_by_hash("0x7b01"))
    print("CAROL 的交易:", [t["tx_hash"] for t in
          query.txs_by_address(CAROL)["transactions"]])

    store.close()


if __name__ == "__main__":
    main()
