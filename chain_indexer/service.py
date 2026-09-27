"""投放编排：校验 -> 去重 -> 归档 -> 选最长链 -> 原子换链。

关键确定性约定：
- 完全相同的区块重复投放：幂等接受，不产生重复数据（accepted_duplicate）。
- 同哈希但内容不一致：拒绝（bad_link / duplicate_genesis）。
- 父块未知：区块先归档暂存（accepted_pending），链头不动、绝不倒退。
- 只有连接到创世的链才能竞争链头；取最长链，等长不重组。
- 换链在单个数据库事务内完成：校验新尾交易唯一性 -> 删旧尾索引 ->
  插新尾索引，任一步失败整体回滚，不留孤儿交易。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .chain import GENESIS_PARENT, ChainGraph, Node, ReorgPlan
from .models import AcceptKind, Block, IngestError, IngestResult, RejectReason, Transaction
from .store import Store
from .validation import parse_block

logger = logging.getLogger("chain_indexer.ingest")


class IngestService:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._graph = ChainGraph(self._load_node)

    # ---- 对外入口 -------------------------------------------------------

    def ingest(self, payload: Any, *, seq: int | str = "-") -> IngestResult:
        """投放一个区块（dict 或原始 JSON 字符串），返回结构化判定结果。"""
        try:
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise IngestError(
                        RejectReason.MALFORMED_JSON, f"JSON 解析失败: {exc}"
                    ) from exc
            block = parse_block(payload)
        except IngestError as err:
            logger.warning(
                "seq=%s 拒绝区块 原因=%s 明细=%s", seq, err.reason.value, err.detail
            )
            height = payload.get("height") if isinstance(payload, dict) else None
            block_hash = payload.get("hash") if isinstance(payload, dict) else None
            return IngestResult(
                accepted=False,
                kind=AcceptKind.REJECTED,
                height=height if isinstance(height, int) else None,
                block_hash=block_hash if isinstance(block_hash, str) else None,
                reason=err.reason,
                detail=err.detail,
            )
        try:
            return self.ingest_block(block, seq=seq)
        except IngestError as err:
            # 换链阶段的冲突（如新尾交易与共享前缀重复）：事务已整体回滚
            logger.warning(
                "seq=%s 拒绝 height=%s hash=%s 原因=%s 明细=%s",
                seq, block.height, block.hash, err.reason.value, err.detail
            )
            return IngestResult(
                accepted=False,
                kind=AcceptKind.REJECTED,
                height=block.height,
                block_hash=block.hash,
                reason=err.reason,
                detail=err.detail,
                new_tip=self._tip_hash(),
            )

    def ingest_block(self, block: Block, *, seq: int | str = "-") -> IngestResult:
        """投放一个已解析的区块。"""
        s = self._store
        existing = s.get_block_row(block.hash)
        if existing is not None:
            return self._handle_existing(block, existing, seq)

        # 创世冲突：已有创世且不是同一个块
        genesis_hash = s.get_canonical_hash(0) or self._find_archived_genesis()
        if block.height == 0 and genesis_hash is not None and genesis_hash != block.hash:
            return self._reject(
                block,
                RejectReason.DUPLICATE_GENESIS,
                f"已存在创世块 {genesis_hash}，拒绝另一个创世块 {block.hash}",
                seq,
            )

        connected = self._is_connected(block)
        if block.height > 0 and not connected:
            # 先归档暂存：父块未知时不影响链头，之后补到也能参与竞争
            self._archive_only(block)
            logger.info(
                "seq=%s 暂存 height=%s hash=%s parent=%s 判定=父块未知，链头保持不变",
                seq, block.height, block.hash, block.parent_hash,
            )
            return IngestResult(
                accepted=True,
                kind=AcceptKind.ACCEPTED_PENDING,
                height=block.height,
                block_hash=block.hash,
                detail="父块尚未到达，已暂存",
                new_tip=self._tip_hash(),
            )

        return self._archive_and_reorg(block, seq)

    # ---- 内部步骤 -------------------------------------------------------

    def _handle_existing(
        self, block: Block, existing: Any, seq: int | str
    ) -> IngestResult:
        if (
            existing["height"] != block.height
            or existing["parent_hash"] != block.parent_hash
        ):
            return self._reject(
                block,
                RejectReason.BAD_LINK,
                f"哈希 {block.hash} 已存在但 height/parent 与本次投放不一致",
                seq,
            )
        prev_payload = json.loads(existing["payload"])
        if prev_payload != block.raw:
            return self._reject(
                block,
                RejectReason.BAD_VALUE,
                f"哈希 {block.hash} 已存在但区块内容与首次投放不一致",
                seq,
            )
        logger.info(
            "seq=%s 重复投放 height=%s hash=%s 判定=幂等接受，不产生重复数据",
            seq, block.height, block.hash,
        )
        return IngestResult(
            accepted=True,
            kind=AcceptKind.ACCEPTED_DUPLICATE,
            height=block.height,
            block_hash=block.hash,
            detail="区块已存在，幂等忽略",
            new_tip=self._tip_hash(),
        )

    def _archive_and_reorg(self, block: Block, seq: int | str) -> IngestResult:
        s = self._store
        s.begin_write()
        try:
            s.archive_block(block)

            current = s.get_canonical_hashes()
            current_path = tuple(current[h] for h in sorted(current))
            current_tip = current_path[-1] if current_path else None

            # 新区块可能让一串早先暂存的后代首次连通，叶子后代都是候选 tip
            candidate_tips = self._descendant_tips(block.hash)
            if current_tip is not None:
                candidate_tips.add(current_tip)
            target_path = self._graph.best_path(candidate_tips, current_tip)
            if target_path is None:
                # 新区块自身不连通（理论上已在前置判断过滤），仅保留归档
                s.commit_write()
                return IngestResult(
                    accepted=True,
                    kind=AcceptKind.ACCEPTED_PENDING,
                    height=block.height,
                    block_hash=block.hash,
                    new_tip=current_tip,
                )

            plan = self._graph.reorg_plan(current_path, target_path)

            if not plan.is_reorg:
                s.commit_write()
                logger.info(
                    "seq=%s 归档 height=%s hash=%s 判定=未改变当前规范链",
                    seq, block.height, block.hash,
                )
                return IngestResult(
                    accepted=True,
                    kind=AcceptKind.ACCEPTED_NEW,
                    height=block.height,
                    block_hash=block.hash,
                    new_tip=current_tip,
                )

            self._check_new_tail_txs(plan)
            self._apply_plan(plan)
            new_tip = target_path[-1]
            s.commit_write()
        except IngestError:
            s.rollback_write()
            raise
        except Exception:
            s.rollback_write()
            raise

        if plan.fork_height is None:
            basis = "初次建链"
        elif plan.rollback_height == 0:
            basis = f"分叉点在创世前，回滚整条旧链"
        else:
            basis = (
                f"分叉高度={plan.fork_height}，旧链回滚高度>={plan.rollback_height}，"
                f"新链更长({len(target_path)}>{len(plan.old_path)})"
            )
        logger.info(
            "seq=%s 接受 height=%s hash=%s 判定=%s 依据=%s 新链头=%s",
            seq, block.height, block.hash,
            "重组" if plan.rollback_height else "延伸",
            basis, new_tip,
        )
        return IngestResult(
            accepted=True,
            kind=AcceptKind.ACCEPTED_NEW,
            height=block.height,
            block_hash=block.hash,
            reorg=plan.rollback_height is not None,
            new_tip=new_tip,
            detail=basis,
        )

    def _check_new_tail_txs(self, plan: ReorgPlan) -> None:
        """校验新尾内交易唯一，且不与共享前缀中已上链交易冲突。

        旧尾的交易此时仍在索引中、但马上要被回滚，因此不能拿全表判断，
        只能与分叉点之前的共享前缀比较。
        """
        s = self._store
        if plan.fork_height is None:
            prefix = ()
        else:
            prefix = plan.old_path[: plan.fork_height + 1]
        prefix_txs = s.canonical_tx_hashes(prefix)

        seen: set[str] = set()
        for block_hash in plan.new_suffix:
            block = self._load_block(block_hash)
            assert block is not None
            for tx in block.transactions:
                if tx.tx_hash in seen:
                    raise IngestError(
                        RejectReason.DUPLICATE_TX_ON_CHAIN,
                        f"新链内交易哈希重复: {tx.tx_hash} 出现于 {tx.block_hash}",
                    )
                seen.add(tx.tx_hash)
                if tx.tx_hash in prefix_txs:
                    raise IngestError(
                        RejectReason.DUPLICATE_TX_ON_CHAIN,
                        f"交易 {tx.tx_hash} 已存在于分叉点之前的规范链上",
                    )

    def _apply_plan(self, plan: ReorgPlan) -> None:
        s = self._store
        if plan.rollback_height is not None:
            s.clear_canonical_from(plan.rollback_height)
        for height, block_hash in enumerate(plan.new_suffix):
            actual_height = (
                0 if plan.fork_height is None else plan.fork_height + 1
            ) + height
            block = self._load_block(block_hash)
            assert block is not None
            s.set_canonical_at(actual_height, block_hash)
            s.insert_tx_indexes(block.transactions)

    # ---- 辅助 -----------------------------------------------------------

    def _descendant_tips(self, root_hash: str) -> set[str]:
        """沿归档子树向下收集所有叶子块哈希（无孩子的块）。"""
        tips: set[str] = set()
        frontier = [root_hash]
        visited: set[str] = set()
        while frontier:
            cur = frontier.pop()
            if cur in visited:
                continue
            visited.add(cur)
            children = self._store.get_children(cur)
            if not children:
                tips.add(cur)
                continue
            frontier.extend(c["hash"] for c in children)
        return tips

    def _archive_only(self, block: Block) -> None:
        s = self._store
        s.begin_write()
        try:
            s.archive_block(block)
            s.commit_write()
        except Exception:
            s.rollback_write()
            raise

    def _is_connected(self, block: Block) -> bool:
        if block.height == 0:
            return block.parent_hash == GENESIS_PARENT
        parent = self._store.get_block_row(block.parent_hash)
        if parent is None:
            return False
        if parent["height"] != block.height - 1:
            return False
        return True

    def _find_archived_genesis(self) -> str | None:
        rows = self._store.get_children(GENESIS_PARENT)
        for row in rows:
            if row["height"] == 0:
                return row["hash"]
        return None

    def _load_node(self, block_hash: str) -> Node | None:
        row = self._store.get_block_row(block_hash)
        if row is None:
            return None
        return Node(row["height"], row["hash"], row["parent_hash"])

    def _load_block(self, block_hash: str) -> Block | None:
        payload = self._store.get_block_payload(block_hash)
        if payload is None:
            return None
        return parse_block(payload)

    def _tip_hash(self) -> str | None:
        h = self._store.get_tip_height()
        return self._store.get_canonical_hash(h) if h is not None else None

    def _reject(
        self,
        block: Block,
        reason: RejectReason,
        detail: str,
        seq: int | str,
    ) -> IngestResult:
        logger.warning(
            "seq=%s 拒绝 height=%s hash=%s 原因=%s 明细=%s",
            seq, block.height, block.hash, reason.value, detail,
        )
        return IngestResult(
            accepted=False,
            kind=AcceptKind.REJECTED,
            height=block.height,
            block_hash=block.hash,
            reason=reason,
            detail=detail,
            new_tip=self._tip_hash(),
        )
