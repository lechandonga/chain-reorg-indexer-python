"""链头选择：连通性、最长链、分叉点计算。纯图算法，不触碰数据库细节。

概念：
- 一条链必须从创世块(height=0, parent=0x0)开始逐块父子相连。
- 父块还没到的块称为「暂存块」，不能参与链头竞争，链头绝不会因此倒退。
- 链头规则：连接到创世的链中取最长者；等长时不发生重组（保留现有链头），
  没有现有链头时按 tip 哈希字典序取最小，保证结果确定。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

GENESIS_PARENT = "0x0"


@dataclass(frozen=True)
class Node:
    height: int
    hash: str
    parent_hash: str


@dataclass(frozen=True)
class ReorgPlan:
    """换链方案：从 fork_height 起删除旧尾，再按 new_suffix 接入新尾。"""

    is_reorg: bool
    fork_height: int | None
    old_path: tuple[str, ...]
    new_path: tuple[str, ...]

    @property
    def new_suffix(self) -> tuple[str, ...]:
        """新链上从分叉点之后开始的块哈希（高度递增）。"""
        if self.fork_height is None:
            return self.new_path
        return self.new_path[self.fork_height + 1 :]

    @property
    def rollback_height(self) -> int | None:
        """需要清空旧规范链的起始高度；无重组时为 None。"""
        if not self.is_reorg:
            return None
        return 0 if self.fork_height is None else self.fork_height + 1


class ChainGraph:
    """只读链图。node_getter 负责按哈希加载节点（由存储层提供）。"""

    def __init__(self, node_getter: Callable[[str], Node | None]) -> None:
        self._get = node_getter

    def path_from_tip(self, tip_hash: str) -> tuple[Node, ...] | None:
        """从某个块一路回溯到创世；断链（父未知）返回 None。

        同时校验高度连续性与哈希自洽，任何不一致也返回 None。
        """
        reversed_path: list[Node] = []
        cursor: Node | None = self._get(tip_hash)
        seen: set[str] = set()
        while cursor is not None:
            if cursor.hash in seen:
                return None  # 环，非法
            seen.add(cursor.hash)
            reversed_path.append(cursor)
            if cursor.height == 0:
                if cursor.parent_hash != GENESIS_PARENT:
                    return None
                path = tuple(reversed(reversed_path))
                return path if _is_contiguous(path) else None
            if cursor.parent_hash == GENESIS_PARENT:
                return None
            cursor = self._get(cursor.parent_hash)
        return None  # 回溯到未知父块

    def best_path(
        self, candidate_tips: Iterable[str], current_tip: str | None
    ) -> tuple[str, ...] | None:
        """在候选 tip 中选出最终规范链（从创世到 tip 的哈希序列）。"""
        best: tuple[str, ...] | None = None
        for tip in candidate_tips:
            path = self.path_from_tip(tip)
            if path is None:
                continue
            hashes = tuple(n.hash for n in path)
            best = _pick_longer_or_tie(best, hashes, current_tip)
        return best

    def reorg_plan(
        self, current: Sequence[str] | None, target: Sequence[str]
    ) -> ReorgPlan:
        """对比当前规范链与目标链，给出换链方案。"""
        current = tuple(current or ())
        target = tuple(target)
        if current == target:
            return ReorgPlan(False, None, current, target)

        fork_height: int | None = None
        for i in range(min(len(current), len(target))):
            if current[i] != target[i]:
                break
            fork_height = i
        return ReorgPlan(True, fork_height, current, target)


def _is_contiguous(path: tuple[Node, ...]) -> bool:
    prev_hash = GENESIS_PARENT
    for expected, node in enumerate(path):
        if node.height != expected:
            return False
        if node.parent_hash != prev_hash:
            return False
        prev_hash = node.hash
    return True


def _pick_longer_or_tie(
    best: tuple[str, ...] | None,
    candidate: tuple[str, ...],
    current_tip: str | None,
) -> tuple[str, ...]:
    if best is None:
        return candidate
    if len(candidate) > len(best):
        return candidate
    if len(candidate) < len(best):
        return best
    # 等长：当前链头所在链优先（不做无谓重组），否则取 tip 字典序最小
    if current_tip is not None and best[-1] == current_tip:
        return best
    if current_tip is not None and candidate[-1] == current_tip:
        return candidate
    return candidate if candidate[-1] < best[-1] else best
