"""SQLite 存储：区块/交易/规范链，以及重组原子操作。

关键约定：
- 所有区块（包括尚未连上创世的"孤儿"分叉块）都落 blocks 表，不丢数据；
- connected=1 表示沿 parent_hash 可追溯到高度 0 的创世块；
- canonical 表是"当前规范链"，每个高度恰好一个块。查询一律 JOIN canonical，
  因此被重组替换掉的块及其交易绝不可能出现在查询结果里；
- ingest 的写入、连通性级联、规范链重选放在同一个事务里提交，
  外部要么看到旧规范链、要么看到新规范链，读不到写一半的状态。
"""
from __future__ import annotations

import sqlite3
import threading

from .errors import DuplicateBlockError, DuplicateTxError, ParentChainError
from .models import Block, ChainHead, Rejection, Transaction, TxView

GENESIS_PARENT = ""


class IndexStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    # ---------- schema ----------
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    height      INTEGER NOT NULL,
                    hash        TEXT PRIMARY KEY,
                    parent_hash TEXT NOT NULL,
                    chainwork   INTEGER NOT NULL,
                    connected   INTEGER NOT NULL,
                    ingest_seq  INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    tx_hash    TEXT PRIMARY KEY,
                    block_hash TEXT NOT NULL,
                    from_addr  TEXT NOT NULL,
                    to_addr    TEXT,
                    value      INTEGER NOT NULL,
                    tx_index   INTEGER NOT NULL,
                    FOREIGN KEY(block_hash) REFERENCES blocks(hash)
                );
                CREATE INDEX IF NOT EXISTS idx_tx_from ON transactions(from_addr);
                CREATE INDEX IF NOT EXISTS idx_tx_to   ON transactions(to_addr);
                CREATE INDEX IF NOT EXISTS idx_tx_block ON transactions(block_hash);
                CREATE TABLE IF NOT EXISTS canonical (
                    height     INTEGER PRIMARY KEY,
                    block_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rejections (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    reason     TEXT NOT NULL,
                    detail     TEXT NOT NULL,
                    block_hash TEXT,
                    height     INTEGER,
                    seq        INTEGER
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 读取辅助 ----------
    def head(self) -> ChainHead | None:
        row = self._conn.execute(
            "SELECT b.height, b.hash, b.chainwork FROM canonical c "
            "JOIN blocks b ON b.hash = c.block_hash "
            "ORDER BY c.height DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return ChainHead(row["height"], row["hash"], row["chainwork"])

    def _txs_of(self, block_hash: str) -> tuple[Transaction, ...]:
        rows = self._conn.execute(
            "SELECT * FROM transactions WHERE block_hash = ? ORDER BY tx_index",
            (block_hash,),
        ).fetchall()
        return tuple(
            Transaction(
                tx_hash=r["tx_hash"], block_hash=r["block_hash"],
                from_addr=r["from_addr"], to_addr=r["to_addr"],
                value=r["value"], index=r["tx_index"],
            )
            for r in rows
        )

    # ---------- 写入 / 重组 ----------
    def ingest(self, block: Block, *, seq: int) -> tuple[ChainHead, bool]:
        """写入区块并在必要时重组。返回事务后的链头与是否发生重组。

        未知父块：缓存（connected=0），不拒绝；父块日后到达时自动级联上线。
        已知父块但高度不衔接：拒绝（parent_chain_error）。
        """
        with self._lock:
            conn = self._conn
            try:
                conn.execute("BEGIN IMMEDIATE")

                existed = conn.execute(
                    "SELECT height, parent_hash, chainwork FROM blocks WHERE hash = ?",
                    (block.hash,),
                ).fetchone()
                if existed is not None:
                    # 同 hash 重复投放：数据一致即幂等；数据冲突则拒绝
                    if (existed["height"], existed["parent_hash"],
                            existed["chainwork"]) != (
                            block.height, block.parent_hash, block.chainwork):
                        raise DuplicateBlockError(
                            f"区块 {block.hash} 已存在但字段与本次投放不一致",
                            block_hash=block.hash, height=block.height,
                        )
                    head_after = self.head()
                    conn.commit()
                    return head_after, False

                if block.parent_hash != GENESIS_PARENT:
                    parent = conn.execute(
                        "SELECT height FROM blocks WHERE hash = ?",
                        (block.parent_hash,),
                    ).fetchone()
                    if parent is not None and parent["height"] != block.height - 1:
                        raise ParentChainError(
                            f"区块 {block.hash} 高度 {block.height} 与父块 "
                            f"{block.parent_hash} 高度 {parent['height']} 不衔接",
                            block_hash=block.hash, height=block.height,
                        )

                parent_connected = (
                    block.parent_hash == GENESIS_PARENT
                    and block.height == 0
                )
                if block.parent_hash != GENESIS_PARENT:
                    row = conn.execute(
                        "SELECT connected FROM blocks WHERE hash = ?",
                        (block.parent_hash,),
                    ).fetchone()
                    if row is not None:
                        parent_connected = bool(row["connected"])

                conn.execute(
                    "INSERT INTO blocks(height, hash, parent_hash, chainwork, "
                    "connected, ingest_seq) VALUES (?,?,?,?,?,?)",
                    (block.height, block.hash, block.parent_hash,
                     block.chainwork, int(parent_connected), seq),
                )
                for tx in block.transactions:
                    clash = conn.execute(
                        "SELECT block_hash FROM transactions WHERE tx_hash = ?",
                        (tx.tx_hash,),
                    ).fetchone()
                    if clash is not None and clash["block_hash"] != block.hash:
                        raise DuplicateTxError(
                            f"交易 {tx.tx_hash} 已属于区块 {clash['block_hash']}，"
                            f"不能再次属于区块 {block.hash}",
                            block_hash=block.hash, height=block.height,
                        )
                    if clash is None:
                        conn.execute(
                            "INSERT INTO transactions(tx_hash, block_hash, from_addr,"
                            " to_addr, value, tx_index) VALUES (?,?,?,?,?,?)",
                            (tx.tx_hash, tx.block_hash, tx.from_addr, tx.to_addr,
                             tx.value, tx.index),
                        )

                # 连通性级联：新块可能让其后代从孤儿变成已连通
                if parent_connected:
                    self._cascade_connect(block.hash)

                reorged = self._rebuild_canonical_if_needed(conn)
                new_head = self.head()
                conn.commit()
                return new_head, reorged
            except Exception:
                conn.rollback()
                raise

    def _cascade_connect(self, newly_connected_hash: str) -> None:
        frontier = [newly_connected_hash]
        while frontier:
            parent = frontier.pop()
            children = self._conn.execute(
                "UPDATE blocks SET connected = 1 WHERE connected = 0 "
                "AND parent_hash = ? RETURNING hash",
                (parent,),
            ).fetchall()
            frontier.extend(r["hash"] for r in children)

    def _best_tip(self) -> str | None:
        """规范链头：connected 块中 chainwork 最大；并列时 hash 字典序更小者胜。

        并列规则保证结果确定、与投放顺序无关。
        """
        row = self._conn.execute(
            "SELECT hash FROM blocks WHERE connected = 1 "
            "ORDER BY chainwork DESC, hash ASC LIMIT 1"
        ).fetchone()
        return None if row is None else row["hash"]

    def _rebuild_canonical_if_needed(self, conn: sqlite3.Connection) -> bool:
        tip = self._best_tip()
        if tip is None:
            return False
        current = self.head()
        if current is not None and current.hash == tip:
            return False

        # 从 tip 沿父指针回溯到创世，再整体替换 canonical（同一事务内原子完成）
        chain: list[tuple[int, str]] = []
        cursor = tip
        seen: set[str] = set()
        while cursor:
            if cursor in seen:
                raise ParentChainError(f"父指针出现环，涉及区块 {cursor}")
            seen.add(cursor)
            row = conn.execute(
                "SELECT height, parent_hash FROM blocks WHERE hash = ?", (cursor,)
            ).fetchone()
            if row is None:
                break
            chain.append((row["height"], cursor))
            cursor = row["parent_hash"]

        # 只有当旧链头不在新链上时，才是真正的"分叉顶替"式重组；
        # 旧链为空（首次成链）或旧链头仍是新链祖先（正常向前延长）不算。
        old_head_hash = current.hash if current is not None else None
        reorged = old_head_hash is not None and old_head_hash not in seen

        conn.execute("DELETE FROM canonical")
        conn.executemany(
            "INSERT INTO canonical(height, block_hash) VALUES (?,?)", chain
        )
        return reorged

    # ---------- 拒绝记录 ----------
    def log_rejection(self, rejection: Rejection) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO rejections(reason, detail, block_hash, height, seq)"
                " VALUES (?,?,?,?,?)",
                (rejection.reason, rejection.detail, rejection.block_hash,
                 rejection.height, rejection.seq),
            )
            self._conn.commit()

    def rejections(self, limit: int = 100) -> list[Rejection]:
        rows = self._conn.execute(
            "SELECT reason, detail, block_hash, height, seq FROM rejections "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Rejection(
                reason=r["reason"], detail=r["detail"],
                block_hash=r["block_hash"], height=r["height"], seq=r["seq"],
            )
            for r in rows
        ]

    # ---------- 对外查询（只可能命中当前规范链） ----------
    def get_block_by_height(self, height: int) -> Block | None:
        row = self._conn.execute(
            "SELECT b.hash, b.parent_hash, b.chainwork FROM canonical c "
            "JOIN blocks b ON b.hash = c.block_hash WHERE c.height = ?",
            (height,),
        ).fetchone()
        if row is None:
            return None
        return Block(
            height=height, hash=row["hash"], parent_hash=row["parent_hash"],
            chainwork=row["chainwork"], transactions=self._txs_of(row["hash"]),
        )

    def get_tx(self, tx_hash: str) -> TxView | None:
        row = self._conn.execute(
            "SELECT t.*, c.height FROM transactions t "
            "JOIN canonical c ON c.block_hash = t.block_hash "
            "WHERE t.tx_hash = ?",
            (tx_hash,),
        ).fetchone()
        if row is None:
            return None
        tx = Transaction(
            tx_hash=row["tx_hash"], block_hash=row["block_hash"],
            from_addr=row["from_addr"], to_addr=row["to_addr"],
            value=row["value"], index=row["tx_index"],
        )
        return TxView(tx=tx, height=row["height"])

    def get_txs_by_address(self, address: str) -> list[TxView]:
        rows = self._conn.execute(
            "SELECT t.*, c.height FROM transactions t "
            "JOIN canonical c ON c.block_hash = t.block_hash "
            "WHERE t.from_addr = ? OR t.to_addr = ? "
            "ORDER BY c.height DESC, t.tx_index ASC",
            (address, address),
        ).fetchall()
        return [
            TxView(
                tx=Transaction(
                    tx_hash=r["tx_hash"], block_hash=r["block_hash"],
                    from_addr=r["from_addr"], to_addr=r["to_addr"],
                    value=r["value"], index=r["tx_index"],
                ),
                height=r["height"],
            )
            for r in rows
        ]
