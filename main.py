"""本地启动入口：uvicorn main:app --reload，或 python main.py"""
from __future__ import annotations

import os

import uvicorn

from chain_reorg_indexer.api import create_app
from chain_reorg_indexer.service import IndexerService
from chain_reorg_indexer.store import IndexStore

DB_PATH = os.environ.get("INDEXER_DB", "indexer.db")

store = IndexStore(DB_PATH)
service = IndexerService(store)
app = create_app(service)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
