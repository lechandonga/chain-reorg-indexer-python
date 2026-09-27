"""索引服务门面：校验 -> 落库/重组 -> 记录拒绝原因；并提供查询。"""
from __future__ import annotations

import logging

from .errors import IngestError
from .models import Block, IngestResult, Rejection, TxView
from .store import IndexStore

logger = logging.getLogger("chain_reorg_indexer")


class IndexerService:
    def __init__(self, store: IndexStore) -> None:
        self.store = store

    def ingest_payload(self, payload: object, *, seq: int) -> IngestResult:
        try:
            block = self._validate(payload)
        except IngestError as exc:
            rejection = Rejection(
                reason=exc.reason, detail=exc.detail,
                block_hash=exc.block_hash, height=exc.height, seq=seq,
            )
            self.store.log_rejection(rejection)
            logger.info(
                "[拒绝] seq=%s reason=%s height=%s block=%s detail=%s",
                seq, rejection.reason, rejection.height,
                rejection.block_hash, rejection.detail,
            )
            return IngestResult(
                accepted=False, block_hash=exc.block_hash or "",
                height=exc.height if exc.height is not None else -1,
                became_canonical=False, reorged=False, rejection=rejection,
            )
        return self.ingest_block(block, seq=seq)

    def ingest_block(self, block: Block, *, seq: int) -> IngestResult:
        try:
            head, reorged = self.store.ingest(block, seq=seq)
        except IngestError as exc:
            rejection = Rejection(
                reason=exc.reason, detail=exc.detail,
                block_hash=exc.block_hash, height=exc.height, seq=seq,
            )
            self.store.log_rejection(rejection)
            logger.info(
                "[拒绝] seq=%s reason=%s height=%s block=%s detail=%s",
                seq, rejection.reason, rejection.height,
                rejection.block_hash, rejection.detail,
            )
            return IngestResult(
                accepted=False, block_hash=block.hash, height=block.height,
                became_canonical=False, reorged=False, rejection=rejection,
            )

        canonical_head = self.store.head()
        became_canonical = canonical_head is not None
        if canonical_head is not None:
            cur = self.store.get_block_by_height(block.height)
            became_canonical = cur is not None and cur.hash == block.hash

        logger.info(
            "[接收] seq=%s height=%s block=%s canonical=%s reorg=%s head=%s/%s",
            seq, block.height, block.hash, became_canonical, reorged,
            canonical_head.height if canonical_head else "-",
            canonical_head.hash if canonical_head else "-",
        )
        return IngestResult(
            accepted=True, block_hash=block.hash, height=block.height,
            became_canonical=became_canonical, reorged=reorged,
            head=canonical_head,
        )

    # 便于测试/模拟投放绕过 JSON 层
    def _validate(self, payload: object) -> Block:
        from .validation import parse_block
        return parse_block(payload)

    def get_block_by_height(self, height: int) -> Block | None:
        return self.store.get_block_by_height(height)

    def get_tx(self, tx_hash: str) -> TxView | None:
        return self.store.get_tx(tx_hash)

    def get_txs_by_address(self, address: str) -> list[TxView]:
        return self.store.get_txs_by_address(address)

    def rejections(self, limit: int = 100) -> list[Rejection]:
        return self.store.rejections(limit)
