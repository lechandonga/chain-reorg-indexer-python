"""数据模型：区块、交易、链头、投放结果。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Transaction:
    tx_hash: str
    block_hash: str
    from_addr: str
    to_addr: str | None
    value: int
    index: int


@dataclass(frozen=True, slots=True)
class Block:
    height: int
    hash: str
    parent_hash: str
    chainwork: int
    transactions: tuple[Transaction, ...] = ()


@dataclass(frozen=True, slots=True)
class ChainHead:
    height: int
    hash: str
    chainwork: int


@dataclass(frozen=True, slots=True)
class Rejection:
    reason: str
    detail: str
    block_hash: str | None = None
    height: int | None = None
    seq: int | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: bool
    block_hash: str
    height: int
    became_canonical: bool
    reorged: bool
    head: ChainHead | None = None
    rejection: Rejection | None = None


@dataclass(frozen=True, slots=True)
class TxView:
    tx: Transaction
    height: int
