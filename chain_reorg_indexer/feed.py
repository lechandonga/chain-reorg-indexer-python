"""本地模拟区块投放：构造链与分叉，按指定顺序（乱序/重复）喂给服务。"""
from __future__ import annotations

from .models import Block, Transaction


def make_tx(tx_hash: str, block_hash: str, from_addr: str,
            to_addr: str | None = None, value: int = 1,
            index: int = 0) -> Transaction:
    return Transaction(
        tx_hash=tx_hash, block_hash=block_hash, from_addr=from_addr,
        to_addr=to_addr, value=value, index=index,
    )


def make_block(height: int, hash_: str, parent: str,
               txs: tuple[Transaction, ...] = (),
               chainwork: int | None = None) -> Block:
    return Block(
        height=height, hash=hash_, parent_hash=parent,
        chainwork=height + 1 if chainwork is None else chainwork,
        transactions=tuple(txs),
    )


def genesis(hash_: str = "0xG0") -> Block:
    return make_block(0, hash_, "", (), chainwork=1)


def chain(prefix: str, length: int, *, parent: Block,
          work_step: int = 1,
          tx_factory=None) -> list[Block]:
    """从 parent 之后延展出 length 个块。tx_factory(height, hash) -> txs。"""
    blocks: list[Block] = []
    prev_hash = parent.hash
    prev_work = parent.chainwork
    for i in range(1, length + 1):
        height = parent.height + i
        h = f"{prefix}-h{height}"
        work = prev_work + work_step
        txs = tuple(tx_factory(height, h)) if tx_factory else ()
        b = make_block(height, h, prev_hash, txs, chainwork=work)
        blocks.append(b)
        prev_hash, prev_work = h, work
    return blocks


def block_to_payload(block: Block) -> dict:
    return {
        "height": block.height,
        "hash": block.hash,
        "parent_hash": block.parent_hash,
        "chainwork": block.chainwork,
        "transactions": [
            {
                "tx_hash": t.tx_hash,
                "block_hash": t.block_hash,
                "from": t.from_addr,
                "to": t.to_addr,
                "value": t.value,
                "index": t.index,
            }
            for t in block.transactions
        ],
    }
