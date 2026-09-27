"""SQLite 存储层：建表、原始区块归档、规范链视图、事务索引。

所有对外写方法都在单个数据库事务内完成，保证重组不会留下半成品。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        raise NotImplementedError
