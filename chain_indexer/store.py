"""SQLite 存储层：建表、原始区块归档、规范链视图、事务索引。

表设计：

- ``blocks``       所有到达的区块（含未连通的暂存块），只增不删。
- ``canonical``    当前规范链上每个高度对应的区块哈希，链头的权威视图。
- ``transactions`` 当前规范链上的交易索引（含金额、收发地址）。
- ``tx_addresses`` 交易 <-> 参与地址 的倒排表，支持按地址查询。

写操作约定：
- 所有写都在单把可重入锁 + 单个数据库事务（BEGIN IMMEDIATE）内完成，
  重组时「删旧链 / 插新链」要么全部生效要么全部回滚，不会留半成品。
- 读操作在同一把锁下开快照（普通事务），因此边写边查读到的要么是
  重组前的完整链、要么是重组后的完整链。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from .models import Block, Transaction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    height       INTEGER NOT NULL,
    hash         TEXT    NOT NULL PRIMARY KEY,
    parent_hash  TEXT    NOT NULL,
    payload      TEXT    NOT NULL,
    archived_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height);
CREATE INDEX IF NOT EXISTS idx_blocks_parent ON blocks(parent_hash);

CREATE TABLE IF NOT EXISTS canonical (
    height      INTEGER NOT NULL PRIMARY KEY,
    block_hash  TEXT    NOT NULL,
    FOREIGN KEY (block_hash) REFERENCES blocks(hash)
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_hash       TEXT    NOT NULL PRIMARY KEY,
    block_hash    TEXT    NOT NULL,
    block_height  INTEGER NOT NULL,
    from_addr     TEXT    NOT NULL,
    to_addr       TEXT    NOT NULL,
    amount        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_block   ON transactions(block_hash);
CREATE INDEX IF NOT EXISTS idx_tx_height  ON transactions(block_height);

CREATE TABLE IF NOT EXISTS tx_addresses (
    address  TEXT    NOT NULL,
    tx_hash  TEXT    NOT NULL,
    PRIMARY KEY (address, tx_hash)
);
CREATE INDEX IF NOT EXISTS idx_addr_tx ON tx_addresses(address);
"""


class Store:
    """线程安全的 SQLite 存储。"""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,  # 手工事务
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 基础读取 -------------------------------------------------------

    def get_block_row(self, block_hash: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT height, hash, parent_hash, payload FROM blocks WHERE hash=?",
                (block_hash,),
            )
            return cur.fetchone()

    def has_block(self, block_hash: str) -> bool:
        return self.get_block_row(block_hash) is not None

    def get_children(self, block_hash: str) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT height, hash, parent_hash FROM blocks WHERE parent_hash=?",
                (block_hash,),
            )
            return list(cur.fetchall())

    def get_tip_height(self) -> int | None:
        with self._lock:
            cur = self._conn.execute("SELECT MAX(height) AS h FROM canonical")
            row = cur.fetchone()
            return None if row is None or row["h"] is None else row["h"]

    def get_canonical_hash(self, height: int) -> str | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT block_hash FROM canonical WHERE height=?", (height,)
            )
            row = cur.fetchone()
            return row["block_hash"] if row else None

    def get_canonical_hashes(self) -> dict[int, str]:
        with self._lock:
            cur = self._conn.execute("SELECT height, block_hash FROM canonical")
            return {row["height"]: row["block_hash"] for row in cur.fetchall()}

    def canonical_contains(self, block_hash: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM canonical WHERE block_hash=?", (block_hash,)
            )
            return cur.fetchone() is not None

    def canonical_tx_hashes(self, block_hashes: Iterable[str]) -> set[str]:
        """给定一组（共享前缀）块哈希，返回这些块上已索引的交易哈希。"""
        hashes = list(block_hashes)
        if not hashes:
            return set()
        marks = ",".join("?" for _ in hashes)
        with self._lock:
            cur = self._conn.execute(
                f"SELECT tx_hash FROM transactions WHERE block_hash IN ({marks})",
                hashes,
            )
            return {row["tx_hash"] for row in cur.fetchall()}

    def has_canonical_tx(self, tx_hash: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM transactions WHERE tx_hash=?", (tx_hash,)
            )
            return cur.fetchone() is not None

    # ---- 写操作（调用方通常在服务层事务中编排） --------------------------

    def archive_block(self, block: Block) -> None:
        """归档原始区块（含交易负载），重复哈希由调用方先判定。"""
        payload = json.dumps(block.raw, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._conn.execute(
                "INSERT INTO blocks(height, hash, parent_hash, payload) "
                "VALUES (?, ?, ?, ?)",
                (block.height, block.hash, block.parent_hash, payload),
            )

    def begin_write(self) -> None:
        self._lock.acquire()
        self._conn.execute("BEGIN IMMEDIATE")

    def commit_write(self) -> None:
        try:
            self._conn.execute("COMMIT")
        finally:
            self._lock.release()

    def rollback_write(self) -> None:
        try:
            self._conn.execute("ROLLBACK")
        finally:
            self._lock.release()

    def set_canonical_at(self, height: int, block_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO canonical(height, block_hash) VALUES (?, ?) "
                "ON CONFLICT(height) DO UPDATE SET block_hash=excluded.block_hash",
                (height, block_hash),
            )

    def clear_canonical_from(self, height: int) -> None:
        """删除 height 及以上的规范链映射与交易索引（重组回滚旧尾）。"""
        with self._lock:
            old_hashes = [
                r["block_hash"]
                for r in self._conn.execute(
                    "SELECT block_hash FROM canonical WHERE height>=?", (height,)
                ).fetchall()
            ]
            tx_hashes: list[str] = []
            if old_hashes:
                marks = ",".join("?" for _ in old_hashes)
                tx_hashes = [
                    r["tx_hash"]
                    for r in self._conn.execute(
                        f"SELECT tx_hash FROM transactions WHERE block_hash IN ({marks})",
                        old_hashes,
                    ).fetchall()
                ]
            self._conn.execute("DELETE FROM canonical WHERE height>=?", (height,))
            if old_hashes:
                marks = ",".join("?" for _ in old_hashes)
                self._conn.execute(
                    f"DELETE FROM transactions WHERE block_hash IN ({marks})",
                    old_hashes,
                )
            if tx_hashes:
                marks = ",".join("?" for _ in tx_hashes)
                self._conn.execute(
                    f"DELETE FROM tx_addresses WHERE tx_hash IN ({marks})", tx_hashes
                )

    def insert_tx_indexes(self, txs: Iterable[Transaction]) -> None:
        with self._lock:
            for tx in txs:
                self._conn.execute(
                    "INSERT INTO transactions"
                    "(tx_hash, block_hash, block_height, from_addr, to_addr, amount)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        tx.tx_hash,
                        tx.block_hash,
                        tx.block_height,
                        tx.from_addr,
                        tx.to_addr,
                        tx.amount,
                    ),
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO tx_addresses(address, tx_hash) VALUES (?, ?)",
                    [(tx.from_addr, tx.tx_hash), (tx.to_addr, tx.tx_hash)],
                )

    # ---- 查询读模型 -----------------------------------------------------

    def get_block_payload(self, block_hash: str) -> dict | None:
        row = self.get_block_row(block_hash)
        if row is None:
            return None
        return json.loads(row["payload"])

    def fetch_tx(self, tx_hash: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT tx_hash, block_hash, block_height, from_addr, to_addr, amount"
                " FROM transactions WHERE tx_hash=?",
                (tx_hash,),
            )
            return cur.fetchone()

    def fetch_block_by_height(self, height: int) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT b.height, b.hash, b.parent_hash, b.payload "
                "FROM canonical c JOIN blocks b ON b.hash = c.block_hash "
                "WHERE c.height=?",
                (height,),
            )
            return cur.fetchone()

    def fetch_txs_by_address(self, address: str) -> list[sqlite3.Row]:
        address = address.lower()
        with self._lock:
            cur = self._conn.execute(
                "SELECT t.tx_hash, t.block_hash, t.block_height, "
                "t.from_addr, t.to_addr, t.amount "
                "FROM tx_addresses a JOIN transactions t ON t.tx_hash = a.tx_hash "
                "WHERE a.address=? ORDER BY t.block_height ASC, t.tx_hash ASC",
                (address,),
            )
            return list(cur.fetchall())

