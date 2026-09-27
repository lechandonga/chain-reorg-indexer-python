"""共享夹具与"按序投放"辅助：日志打印 序号/高度/哈希/判定依据。"""
from __future__ import annotations

import logging
import sys

import pytest

from chain_reorg_indexer.feed import block_to_payload
from chain_reorg_indexer.models import Block
from chain_reorg_indexer.service import IndexerService
from chain_reorg_indexer.store import IndexStore

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("chain_reorg_indexer.tests")


@pytest.fixture
def service():
    store = IndexStore(":memory:")
    yield IndexerService(store)
    store.close()


def feed_in_order(svc: IndexerService, blocks: list[Block], *, start: int = 1,
                  as_payload: bool = True, label: str = "") -> list:
    """按给定顺序投放（调用方可以故意乱序/重复），逐条打印判定依据。"""
    results = []
    for i, b in enumerate(blocks, start=start):
        log.info(
            "—— %s 投放 seq=%d height=%s block=%s txs=%d",
            label, i, b.height, b.hash, len(b.transactions),
        )
        target = block_to_payload(b) if as_payload else b
        r = (svc.ingest_payload(target, seq=i) if as_payload
             else svc.ingest_block(b, seq=i))
        if r.accepted:
            log.info(
                "   判定=接受 canonical=%s reorg=%s 当前链头=%s/%s",
                r.became_canonical, r.reorged,
                r.head.height if r.head else "-",
                r.head.hash if r.head else "-",
            )
        else:
            log.info("   判定=拒绝 reason=%s detail=%s",
                     r.rejection.reason, r.rejection.detail)
        results.append(r)
    return results
