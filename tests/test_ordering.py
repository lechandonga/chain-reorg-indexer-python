"""乱序到达：链头不能倒退、数据不能丢，补齐后最终状态一致。"""

from __future__ import annotations

from .conftest import make_block


def test_child_before_parent_is_pending(ingester):
    r1 = ingester.feed(make_block(1, "0x11", "0x00"), label="先投高度1")
    assert r1.kind.value == "accepted_pending"
    assert ingester.store.get_tip_height() is None  # 链头不动、不倒退

    r0 = ingester.feed(make_block(0, "0x00", "0x0"), label="后补创世")
    assert r0.accepted and r0.new_tip == "0x11"
    assert ingester.store.get_tip_height() == 1


def test_descending_order_eventually_converges(ingester):
    # 投放顺序 3,2,1,0：全部先暂存，创世到达时一次性连通
    seq_blocks = [
        make_block(3, "0x33", "0x22"),
        make_block(2, "0x22", "0x11"),
        make_block(1, "0x11", "0x00"),
        make_block(0, "0x00", "0x0"),
    ]
    for b in seq_blocks[:-1]:
        r = ingester.feed(b, label="倒序暂存")
        assert r.kind.value == "accepted_pending"
        assert ingester.store.get_tip_height() is None

    r = ingester.feed(seq_blocks[-1], label="补齐创世")
    assert r.new_tip == "0x33"
    assert ingester.store.get_tip_height() == 3
    for h, hsh in {0: "0x00", 1: "0x11", 2: "0x22", 3: "0x33"}.items():
        assert ingester.store.get_canonical_hash(h) == hsh


def test_tip_never_goes_backwards_on_stale_delivery(ingester):
    ingester.feed(make_block(0, "0x00", "0x0"))
    ingester.feed(make_block(1, "0x11", "0x00"))
    ingester.feed(make_block(2, "0x22", "0x11"))
    assert ingester.store.get_tip_height() == 2

    # 迟到的孤儿高度 1（指向一个未知父），不得影响链头
    r = ingester.feed(make_block(1, "0x1e", "0xdead"), label="迟到孤儿")
    assert r.kind.value == "accepted_pending"
    assert ingester.store.get_tip_height() == 2
    assert ingester.store.get_canonical_hash(1) == "0x11"


def test_gap_fill_after_other_branch_extended(ingester):
    # 主链 0,1；先到 3(缺父2) 暂存；补 2 后链头直接到 3
    ingester.feed(make_block(0, "0x00", "0x0"))
    ingester.feed(make_block(1, "0x11", "0x00"))
    r3 = ingester.feed(make_block(3, "0x33", "0x22"), label="跳空高度3")
    assert r3.kind.value == "accepted_pending"
    r2 = ingester.feed(make_block(2, "0x22", "0x11"), label="补高度2")
    assert r2.new_tip == "0x33"
    assert ingester.store.get_tip_height() == 3


def test_final_state_independent_of_delivery_order(ingester):
    # 同一组区块换两种投放顺序，最终规范链一致
    def fresh():
        from .conftest import SequencedIngester
        return SequencedIngester()

    blocks = [
        make_block(0, "0x00", "0x0"),
        make_block(1, "0x11", "0x00"),
        make_block(2, "0x22", "0x11"),
    ]
    other = fresh()
    for b in blocks:
        ingester.feed(b)
    for b in reversed(blocks):
        other.feed(b)
    assert ingester.store.get_canonical_hashes() == other.store.get_canonical_hashes()
