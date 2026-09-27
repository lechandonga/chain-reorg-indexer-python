"""投放失败原因。reason 为对外暴露的稳定枚举字符串。"""
from __future__ import annotations


class IngestError(Exception):
    reason = "ingest_error"
    http_status = 400

    def __init__(self, detail: str, *, block_hash: str | None = None,
                 height: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.block_hash = block_hash
        self.height = height


class MissingFieldError(IngestError):
    reason = "missing_field"
    http_status = 422


class InvalidFormatError(IngestError):
    reason = "invalid_format"
    http_status = 422


class DuplicateBlockError(IngestError):
    reason = "duplicate_block"
    http_status = 409


class DuplicateTxError(IngestError):
    reason = "duplicate_tx"
    http_status = 409


class BlockTxsMismatchError(IngestError):
    reason = "block_txs_mismatch"
    http_status = 422


class ParentChainError(IngestError):
    reason = "parent_chain_error"
    http_status = 422


REASON_TO_STATUS = {
    cls.reason: cls.http_status
    for cls in (
        MissingFieldError,
        InvalidFormatError,
        DuplicateBlockError,
        DuplicateTxError,
        BlockTxsMismatchError,
        ParentChainError,
    )
}
