"""并发：边写边查不能读到写一半的状态。"""

from __future__ import annotations

import threading

from chain_indexer.query import QueryService

from .conftest import make_block


def test_concurrent_reads_always_see_consistent_tip(ingester):
    q = QueryService(ingester.store)
    stop = threading.Event()
    observed: list[int] = []
    errors: list[Exception] = []

    def reader():
        last = -1
        while not stop.is_set():
            try:
                status = q.chain_status()
                h = status["tip_height"]
                if h is not None:
                    # 链头在任何时刻都必须是一条完整链（每个高度都有块）
                    for height in range(h + 1):
                        assert q.block_by_height(height) is not None
                    assert h >= last  # 重组只会换成等长或更长的链
                    last = h
                    observed.append(h)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    t = threading.Thread(target=reader)
    t.start()
    try:
        # 主链 + 反复制造短分叉和更长分叉
        ingester.feed(make_block(0, "0x00", "0x0"))
        for i in range(1, 12):
            h = f"0x{i:02x}"
            parent = f"0x{i-1:02x}"
            ingester.feed(make_block(i, h, parent,
                                     {f"0x{i:04x}": ("0xa1", "0xb1", i)}))
            # 一个等长分叉
            ingester.feed(make_block(i, f"0x{i}e", f"0x{i-1:02x}"))
            # 下一块接在分叉上并延伸两格，逼出重组
            ingester.feed(make_block(i + 1, f"0x{i}f", f"0x{i}e"))
            if i + 1 <= 12:
                ingester.feed(make_block(i + 1, h, f"0x{i}e"))
    finally:
        stop.set()
        t.join()

    assert not errors, errors
    assert observed, "读线程至少应观察到一些链头"
    assert observed[-1] >= 1
