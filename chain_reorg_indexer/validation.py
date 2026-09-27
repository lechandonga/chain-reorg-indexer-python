"""把外部 JSON(dict) 校验并转换为领域模型。

拒绝原因（见 errors.py）：
- missing_field       必填空缺（height / hash / transactions 内的 tx_hash、from 等）
- invalid_format      类型错误、取值非法（负数、布尔、非字符串哈希等）
- duplicate_tx        同一个区块负载里交易哈希重复
- block_txs_mismatch  交易声明的 block_hash 与区块 hash 不一致
- parent_chain_error  高度与父哈希结构矛盾（高度 0 却带父哈希 / 高度>0 却无父哈希）
"""
from __future__ import annotations

from .errors import (
    BlockTxsMismatchError,
    DuplicateTxError,
    InvalidFormatError,
    MissingFieldError,
    ParentChainError,
)
from .models import Block, Transaction

GENESIS_PARENT = ""


def _require(payload: dict, key: str):
    if key not in payload or payload[key] is None:
        raise MissingFieldError(f"缺少必填字段 {key!r}", block_hash=payload.get("hash"))
    return payload[key]


def _as_int(value, name: str, *, block_hash: str | None) -> int:
    # 注意：bool 是 int 的子类，必须显式拒绝
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidFormatError(
            f"字段 {name!r} 必须是整数，收到 {type(value).__name__}: {value!r}",
            block_hash=block_hash,
        )
    return value


def _as_nonempty_str(value, name: str, *, block_hash: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidFormatError(
            f"字段 {name!r} 必须是非空字符串，收到 {value!r}", block_hash=block_hash
        )
    return value


def _parse_tx(raw: object, pos: int, block_hash: str, height: int) -> Transaction:
    if not isinstance(raw, dict):
        raise InvalidFormatError(
            f"transactions[{pos}] 必须是对象，收到 {type(raw).__name__}",
            block_hash=block_hash, height=height,
        )

    tx_hash = _require(raw, "tx_hash")
    tx_hash = _as_nonempty_str(
        tx_hash, f"transactions[{pos}].tx_hash", block_hash=block_hash
    )

    from_addr = _require(raw, "from")
    from_addr = _as_nonempty_str(
        from_addr, f"transactions[{pos}].from", block_hash=block_hash
    )

    to_raw = raw.get("to")
    if to_raw is None:
        to_addr = None  # 合约创建等无目标交易
    else:
        to_addr = _as_nonempty_str(
            to_raw, f"transactions[{pos}].to", block_hash=block_hash
        )

    value = _as_int(
        raw.get("value", 0), f"transactions[{pos}].value", block_hash=block_hash
    )
    if value < 0:
        raise InvalidFormatError(
            f"transactions[{pos}].value 不能为负数: {value}", block_hash=block_hash
        )

    index = raw.get("index", pos)
    index = _as_int(index, f"transactions[{pos}].index", block_hash=block_hash)
    if index < 0:
        raise InvalidFormatError(
            f"transactions[{pos}].index 不能为负数: {index}", block_hash=block_hash
        )

    declared_parent = raw.get("block_hash")
    if declared_parent is not None:
        declared_parent = _as_nonempty_str(
            declared_parent, f"transactions[{pos}].block_hash", block_hash=block_hash
        )
        if declared_parent != block_hash:
            raise BlockTxsMismatchError(
                f"交易 {tx_hash} 声明 block_hash={declared_parent}，"
                f"与区块 hash={block_hash} 不一致",
                block_hash=block_hash, height=height,
            )

    return Transaction(
        tx_hash=tx_hash, block_hash=block_hash, from_addr=from_addr,
        to_addr=to_addr, value=value, index=index,
    )


def parse_block(payload: object) -> Block:
    """校验并构造 Block。任何问题都以 IngestError 子类抛出，绝不静默丢弃。"""
    if not isinstance(payload, dict):
        raise InvalidFormatError(
            f"请求体必须是 JSON 对象，收到 {type(payload).__name__}"
        )

    block_hash = payload.get("hash") if isinstance(payload, dict) else None
    block_hash = _require(payload, "hash")
    block_hash = _as_nonempty_str(block_hash, "hash", block_hash=None)

    height = _as_int(_require(payload, "height"), "height", block_hash=block_hash)
    if height < 0:
        raise InvalidFormatError(f"height 不能为负数: {height}", block_hash=block_hash)

    parent_raw = payload.get("parent_hash")
    if parent_raw is None:
        parent_hash = GENESIS_PARENT
    else:
        # 允许空串（表示创世无父），但必须是字符串
        if not isinstance(parent_raw, str):
            raise InvalidFormatError(
                f"字段 'parent_hash' 必须是字符串，收到 {type(parent_raw).__name__}",
                block_hash=block_hash,
            )
        parent_hash = parent_raw

    # 高度与父哈希的结构一致性
    if height == 0 and parent_hash != GENESIS_PARENT:
        raise ParentChainError(
            f"高度为 0 的创世块不能带父哈希，收到 parent_hash={parent_hash!r}",
            block_hash=block_hash, height=height,
        )
    if height > 0 and parent_hash == GENESIS_PARENT:
        raise ParentChainError(
            f"高度 {height} 的区块必须指定非空 parent_hash",
            block_hash=block_hash, height=height,
        )

    # chainwork 可选：缺省按"每块 1 工作量"累计，即 height + 1
    chainwork = payload.get("chainwork", height + 1)
    chainwork = _as_int(chainwork, "chainwork", block_hash=block_hash)
    if chainwork < 0:
        raise InvalidFormatError(
            f"chainwork 不能为负数: {chainwork}", block_hash=block_hash
        )

    raw_txs = payload.get("transactions", [])
    if not isinstance(raw_txs, list):
        raise InvalidFormatError(
            f"transactions 必须是数组，收到 {type(raw_txs).__name__}",
            block_hash=block_hash, height=height,
        )

    txs: list[Transaction] = []
    seen: set[str] = set()
    for pos, raw in enumerate(raw_txs):
        tx = _parse_tx(raw, pos, block_hash, height)
        if tx.tx_hash in seen:
            raise DuplicateTxError(
                f"区块 {block_hash} 内交易哈希重复: {tx.tx_hash}",
                block_hash=block_hash, height=height,
            )
        seen.add(tx.tx_hash)
        txs.append(tx)

    return Block(
        height=height, hash=block_hash, parent_hash=parent_hash,
        chainwork=chainwork, transactions=tuple(txs),
    )
