"""入参校验：把外部 JSON 转成领域对象，并区分拒绝原因。

校验是「全有或全无」的：任何一条不过，整个区块都不会落库，
并抛出携带 RejectReason 的 IngestError，调用方据此区分拒绝原因。

期望的区块结构（字段缺失 / 类型错误 / 取值非法都会被拒绝）::

    {
      "height": 0,
      "hash": "0xabc...",
      "parent_hash": "0x0" | "0x...",
      "transactions": [
        {
          "tx_hash": "0x...",
          "from": "0xAddr",
          "to": "0xAddr",
          "amount": 100
        }
      ]
    }
"""

from __future__ import annotations

import re
from typing import Any

from .models import Block, IngestError, RejectReason, Transaction

_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{2,64}$")
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{2,64}$")

_HASH_FIELDS = ("height", "hash", "parent_hash")
_TX_FIELDS = ("tx_hash", "from", "to", "amount")


def _require_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise IngestError(RejectReason.NOT_AN_OBJECT, "区块负载必须是 JSON 对象")
    return payload


def _require_fields(obj: dict[str, Any], fields: tuple[str, ...], what: str) -> None:
    missing = [f for f in fields if f not in obj]
    if missing:
        raise IngestError(
            RejectReason.MISSING_FIELD,
            f"{what}缺少字段: {', '.join(missing)}",
        )


def _parse_hash(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise IngestError(RejectReason.BAD_TYPE, f"字段 {field} 必须是字符串")
    if not _HASH_RE.match(value):
        raise IngestError(
            RejectReason.BAD_VALUE,
            f"字段 {field} 必须是 0x 开头的十六进制哈希，收到: {value!r}",
        )
    return value.lower()


def _parse_address(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise IngestError(RejectReason.BAD_TYPE, f"字段 {field} 必须是字符串")
    if not _ADDR_RE.match(value):
        raise IngestError(
            RejectReason.BAD_VALUE,
            f"字段 {field} 必须是 0x 开头的十六进制地址，收到: {value!r}",
        )
    return value.lower()


def _parse_parent_hash(value: Any) -> str:
    if not isinstance(value, str):
        raise IngestError(RejectReason.BAD_TYPE, "字段 parent_hash 必须是字符串")
    if value == "0x0":
        return value
    if not _HASH_RE.match(value):
        raise IngestError(
            RejectReason.BAD_VALUE,
            f"字段 parent_hash 必须是 0x 哈希或创世占位 0x0，收到: {value!r}",
        )
    return value.lower()


def _parse_height(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IngestError(RejectReason.BAD_TYPE, "字段 height 必须是非负整数")
    if value < 0:
        raise IngestError(RejectReason.BAD_VALUE, "字段 height 不能为负数")
    return value


def _parse_amount(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IngestError(RejectReason.BAD_TYPE, "字段 amount 必须是非负整数")
    if value < 0:
        raise IngestError(RejectReason.BAD_VALUE, "字段 amount 不能为负数")
    return value


def _parse_tx(raw_tx: Any, height: int, block_hash: str) -> Transaction:
    if not isinstance(raw_tx, dict):
        raise IngestError(RejectReason.NOT_AN_OBJECT, "transactions 中每项必须是对象")
    _require_fields(raw_tx, _TX_FIELDS, "交易")
    tx_hash = _parse_hash(raw_tx["tx_hash"], "tx_hash")
    return Transaction(
        tx_hash=tx_hash,
        block_height=height,
        block_hash=block_hash,
        from_addr=_parse_address(raw_tx["from"], "from"),
        to_addr=_parse_address(raw_tx["to"], "to"),
        amount=_parse_amount(raw_tx["amount"]),
        raw=raw_tx,
    )


def parse_block(payload: Any) -> Block:
    """把外部 JSON 解析为 Block，非法时抛 IngestError（携带 RejectReason）。"""
    data = _require_object(payload)
    _require_fields(data, _HASH_FIELDS, "区块")

    height = _parse_height(data["height"])
    block_hash = _parse_hash(data["hash"], "hash")
    parent_hash = _parse_parent_hash(data["parent_hash"])

    if height == 0 and parent_hash != "0x0":
        raise IngestError(
            RejectReason.BAD_LINK,
            f"创世块(height=0) 的 parent_hash 必须是 0x0，收到 {parent_hash!r}",
        )
    if height > 0 and parent_hash == "0x0":
        raise IngestError(
            RejectReason.BAD_LINK,
            f"高度 {height} 的非创世块 parent_hash 不能是 0x0",
        )
    if parent_hash == block_hash:
        raise IngestError(
            RejectReason.BAD_LINK, "区块 hash 与 parent_hash 相同，不能自指"
        )

    raw_txs = data.get("transactions", [])
    if not isinstance(raw_txs, list):
        raise IngestError(RejectReason.BAD_TYPE, "字段 transactions 必须是数组")

    txs: list[Transaction] = []
    seen: set[str] = set()
    for raw_tx in raw_txs:
        tx = _parse_tx(raw_tx, height, block_hash)
        if tx.tx_hash in seen:
            raise IngestError(
                RejectReason.DUPLICATE_TX_IN_BLOCK,
                f"区块 {block_hash} 内交易哈希重复: {tx.tx_hash}",
            )
        seen.add(tx.tx_hash)
        txs.append(tx)

    return Block(
        height=height,
        hash=block_hash,
        parent_hash=parent_hash,
        transactions=tuple(txs),
        raw=data,
    )

