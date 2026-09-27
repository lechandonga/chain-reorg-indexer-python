"""FastAPI HTTP 层：投放区块、按高度/哈希/地址查询。

所有查询只 JOIN 规范链，链重组期间写事务持锁，读请求拿到的永远是
某一条完整规范链的快照视图，不会读到回滚到一半的数据。
"""
from __future__ import annotations

import itertools
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .errors import REASON_TO_STATUS
from .service import IndexerService

_seq = itertools.count(1)


def _tx_json(tx, height: int) -> dict[str, Any]:
    return {
        "tx_hash": tx.tx_hash,
        "block_hash": tx.block_hash,
        "height": height,
        "index": tx.index,
        "from": tx.from_addr,
        "to": tx.to_addr,
        "value": tx.value,
    }


def _block_json(block) -> dict[str, Any]:
    return {
        "height": block.height,
        "hash": block.hash,
        "parent_hash": block.parent_hash,
        "chainwork": block.chainwork,
        "transactions": [
            _tx_json(tx, block.height) for tx in block.transactions
        ],
    }


def create_app(service: IndexerService) -> FastAPI:
    app = FastAPI(title="chain-reorg-indexer", version="0.1.0")

    @app.post("/blocks")
    async def post_block(request: Request):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
        seq = next(_seq)
        result = service.ingest_payload(payload, seq=seq)
        if not result.accepted:
            r = result.rejection
            assert r is not None
            status = REASON_TO_STATUS.get(r.reason, 400)
            raise HTTPException(
                status_code=status,
                detail={
                    "rejected": True,
                    "reason": r.reason,
                    "detail": r.detail,
                    "block_hash": r.block_hash,
                    "height": r.height,
                    "seq": r.seq,
                },
            )
        return {
            "accepted": True,
            "seq": seq,
            "height": result.height,
            "block_hash": result.block_hash,
            "became_canonical": result.became_canonical,
            "reorg": result.reorged,
            "head": None
            if result.head is None
            else {
                "height": result.head.height,
                "hash": result.head.hash,
                "chainwork": result.head.chainwork,
            },
        }

    @app.get("/blocks/height/{height}")
    def block_by_height(height: int):
        if height < 0:
            raise HTTPException(422, "height 不能为负")
        block = service.get_block_by_height(height)
        if block is None:
            raise HTTPException(404, f"规范链上不存在高度 {height} 的区块")
        return _block_json(block)

    @app.get("/tx/{tx_hash}")
    def tx_by_hash(tx_hash: str):
        view = service.get_tx(tx_hash)
        if view is None:
            raise HTTPException(404, f"规范链上不存在交易 {tx_hash}")
        return _tx_json(view.tx, view.height)

    @app.get("/address/{address}")
    def txs_by_address(address: str):
        views = service.get_txs_by_address(address)
        return {
            "address": address,
            "count": len(views),
            "transactions": [_tx_json(v.tx, v.height) for v in views],
        }

    @app.get("/head")
    def chain_head():
        head = service.store.head()
        if head is None:
            return {"height": None, "hash": None, "chainwork": None}
        return {"height": head.height, "hash": head.hash,
                "chainwork": head.chainwork}

    @app.get("/rejections")
    def list_rejections(limit: int = 100):
        items = service.rejections(limit=limit)
        return {
            "count": len(items),
            "items": [
                {
                    "reason": r.reason,
                    "detail": r.detail,
                    "block_hash": r.block_hash,
                    "height": r.height,
                    "seq": r.seq,
                }
                for r in items
            ],
        }

    return app
