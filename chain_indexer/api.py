"""FastAPI HTTP 接口：投放区块、查询、链头状态。

状态码约定（确定性）：
- 201 单投区块被接受（含幂等重复 / 暂存）
- 200 批量投放全部通过 / 查询
- 422 数据被拒绝（响应体带 reason 区分原因：缺字段、类型错、坏链接……）
- 404 查询对象在当前规范链上不存在
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .query import QueryService
from .service import IngestService
from .store import Store

REJECT_STATUS = 422


def create_app(store: Store | None = None) -> FastAPI:
    own_store = store is None
    store = store or Store(os.environ.get("INDEXER_DB", ":memory:"))
    ingest_service = IngestService(store)
    query_service = QueryService(store)

    app = FastAPI(title="本地链上数据索引服务", version="0.1.0")
    app.state.store = store
    app.state.ingest = ingest_service
    app.state.query = query_service

    @app.post("/blocks")
    async def post_block(request: Request) -> JSONResponse:
        body = await _read_json(request)
        if isinstance(body, JSONResponse):
            return body
        data = ingest_service.ingest(body).to_dict()
        status = REJECT_STATUS if data["reason"] else 201
        return JSONResponse(content=data, status_code=status)

    @app.post("/blocks/batch")
    async def post_blocks(request: Request) -> JSONResponse:
        body = await _read_json(request)
        if isinstance(body, JSONResponse):
            return body
        blocks = body.get("blocks") if isinstance(body, dict) else None
        if not isinstance(blocks, list):
            return JSONResponse(
                status_code=REJECT_STATUS,
                content={
                    "accepted": False,
                    "reason": "missing_field",
                    "detail": '批量投放需要 {"blocks": [...]}',
                    "results": [],
                },
            )
        results = [
            ingest_service.ingest(item, seq=i).to_dict()
            for i, item in enumerate(blocks)
        ]
        any_rejected = any(r.get("reason") for r in results)
        return JSONResponse(
            content={"results": results},
            status_code=REJECT_STATUS if any_rejected else 200,
        )

    @app.get("/status")
    def status() -> dict[str, Any]:
        return query_service.chain_status()

    @app.get("/blocks/height/{height}")
    def block_at_height(height: int) -> JSONResponse:
        block = query_service.block_by_height(height)
        if block is None:
            return JSONResponse(
                status_code=404, content={"detail": "该高度在当前规范链上不存在"}
            )
        return JSONResponse(content=block)

    @app.get("/blocks/hash/{block_hash}")
    def block_at_hash(block_hash: str) -> JSONResponse:
        block = query_service.block_by_hash(block_hash)
        if block is None:
            return JSONResponse(
                status_code=404, content={"detail": "该区块不在当前规范链上"}
            )
        return JSONResponse(content=block)

    @app.get("/transactions/{tx_hash}")
    def transaction(tx_hash: str) -> JSONResponse:
        tx = query_service.tx_by_hash(tx_hash)
        if tx is None:
            return JSONResponse(
                status_code=404,
                content={"detail": "该交易在当前规范链上不存在"},
            )
        return JSONResponse(content=tx)

    @app.get("/addresses/{address}/transactions")
    def address_transactions(address: str) -> dict[str, Any]:
        return query_service.txs_by_address(address)

    if own_store:
        @app.on_event("shutdown")
        def _close() -> None:
            store.close()

    return app


async def _read_json(request: Request) -> Any | JSONResponse:
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=REJECT_STATUS,
            content={
                "accepted": False,
                "reason": "malformed_json",
                "detail": "请求体不是合法 JSON",
            },
        )


app = create_app()
