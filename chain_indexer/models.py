"""领域模型：区块 / 交易 / 投放结果 / 拒绝原因。

这一层只定义数据结构与枚举，不包含任何 I/O 与业务流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RejectReason(str, Enum):
    """一次投放被拒绝的具体原因（用于区分拒绝类别）。"""

    MALFORMED_JSON = "malformed_json"
    NOT_AN_OBJECT = "not_an_object"
    MISSING_FIELD = "missing_field"
    BAD_TYPE = "bad_type"
    BAD_VALUE = "bad_value"
    DUPLICATE_TX_IN_BLOCK = "duplicate_tx_in_block"
    BAD_LINK = "bad_link"
    DUPLICATE_GENESIS = "duplicate_genesis"
    DUPLICATE_TX_ON_CHAIN = "duplicate_tx_on_chain"
    UNKNOWN_PARENT = "unknown_parent"


class AcceptKind(str, Enum):
    """一次投放被接受后的处理类别。"""

    ACCEPTED_NEW = "accepted_new"        # 新区块入库
    ACCEPTED_DUPLICATE = "accepted_duplicate"  # 完全相同的区块重复投放（幂等）
    ACCEPTED_PENDING = "accepted_pending"  # 父块未知，先暂存
    REJECTED = "rejected"


@dataclass(frozen=True)
class Transaction:
    tx_hash: str
    block_height: int
    block_hash: str
    from_addr: str
    to_addr: str
    amount: int
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Block:
    height: int
    hash: str
    parent_hash: str
    transactions: tuple[Transaction, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestResult:
    """单次投放的判定结果。"""

    accepted: bool
    kind: AcceptKind
    height: int | None
    block_hash: str | None
    reason: RejectReason | None = None
    detail: str = ""
    reorg: bool = False
    new_tip: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "kind": self.kind.value,
            "height": self.height,
            "block_hash": self.block_hash,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
            "reorg": self.reorg,
            "new_tip": self.new_tip,
        }


class IngestError(ValueError):
    """校验类错误：携带结构化的拒绝原因。"""

    def __init__(self, reason: RejectReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail
