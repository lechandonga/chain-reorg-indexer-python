"""对外查询：按地址 / 交易哈希 / 高度读取。

只读取 canonical / transactions 两张「当前规范链」视图，因此重组后
绝不会返回已回滚的数据；底层单锁 + 事务保证边写边查读到的是完整状态。
"""

from __future__ import annotations

import json
from typing import Any

from .store import Store


class QueryService:
    def __init__(self, store: Store) -> None:
        self._store = store

    # ---- 链状态 ---------------------------------------------------------

    def chain_status(self) -> dict[str, Any]:
        tip_height = self._store.get_tip_height()
        tip_hash = (
            self._store.get_canonical_hash(tip_height)
            if tip_height is not None
            else None
        )
        return {"tip_height": tip_height, "tip_hash": tip_hash}

    # ---- 区块 -----------------------------------------------------------

    def block_by_height(self, height: int) -> dict[str, Any] | None:
        if not isinstance(height, int) or isinstance(height, bool) or height < 0:
            raise ValueError("height 必须是非负整数")
        row = self._store.fetch_block_by_height(height)
        return _row_to_block(row) if row else None

    def block_by_hash(self, block_hash: str) -> dict[str, Any] | None:
        payload = self._store.get_block_payload(block_hash.lower())
        if payload is None:
            return None
        # 只有当前规范链上的块才对外可见
        if self._store.get_canonical_hash(int(payload["height"])) != block_hash.lower():
            return None
        return {
            "height": payload["height"],
            "hash": payload["hash"],
            "parent_hash": payload["parent_hash"],
            "transactions": payload.get("transactions", []),
        }

    # ---- 交易 -----------------------------------------------------------

    def tx_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        row = self._store.fetch_tx(tx_hash.lower())
        return _row_to_tx(row) if row else None

    def txs_by_address(self, address: str) -> dict[str, Any]:
        addr = address.lower()
        rows = self._store.fetch_txs_by_address(addr)
        return {"address": addr, "transactions": [_row_to_tx(r) for r in rows]}


def _row_to_tx(row: Any) -> dict[str, Any]:
    return {
        "tx_hash": row["tx_hash"],
        "block_hash": row["block_hash"],
        "block_height": row["block_height"],
        "from": row["from_addr"],
        "to": row["to_addr"],
        "amount": row["amount"],
    }


def _row_to_block(row: Any) -> dict[str, Any]:
    payload = json.loads(row["payload"])
    return {
        "height": row["height"],
        "hash": row["hash"],
        "parent_hash": row["parent_hash"],
        "transactions": payload.get("transactions", []),
    }
