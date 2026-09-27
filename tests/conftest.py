"""测试公共件：确定性的区块构造与带「投放顺序」日志的服务工厂。"""

from __future__ import annotations

import logging

import pytest

from chain_indexer.service import IngestService
from chain_indexer.store import Store


def make_block(
    height: int,
    hash_: str,
    parent: str,
    txs: dict[str, tuple[str, str, int]] | None = None,
) -> dict:
    """构造区块；txs 为 {tx_hash: (from, to, amount)}。"""
    txs = txs or {}
    return {
        "height": height,
        "hash": hash_,
        "parent_hash": parent,
        "transactions": [
            {"tx_hash": h, "from": f, "to": t, "amount": a}
            for h, (f, t, a) in txs.items()
        ],
    }


class SequencedIngester:
    """记录投放顺序，并在每次判定时把高度/顺序/依据打到日志。"""

    def __init__(self) -> None:
        self.store = Store()
        self.service = IngestService(self.store)
        self.seq = 0
        self.log = logging.getLogger("chain_indexer.ingest")

    def feed(self, block: dict, *, label: str = ""):
        self.seq += 1
        seq = self.seq
        self.log.info(
            "投放 seq=%s height=%s hash=%s label=%s",
            seq, block.get("height"), block.get("hash"), label or "-",
        )
        result = self.service.ingest(block, seq=seq)
        self.log.info(
            "判定 seq=%s kind=%s accepted=%s reason=%s reorg=%s tip=%s 依据=%s",
            seq, result.kind.value, result.accepted,
            result.reason.value if result.reason else "-",
            result.reorg, result.new_tip, result.detail or "-",
        )
        return result


@pytest.fixture
def ingester(caplog):
    caplog.set_level(logging.INFO, logger="chain_indexer.ingest")
    return SequencedIngester()
