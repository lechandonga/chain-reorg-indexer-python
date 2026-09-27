"""格式校验：不同原因的拒绝必须可区分，合法数据正常解析。"""

from __future__ import annotations

import pytest

from chain_indexer.models import IngestError, RejectReason
from chain_indexer.validation import parse_block

from .conftest import make_block


def test_valid_block_normalizes_hex():
    block = parse_block(make_block(0, "0xAA", "0x0", {
        "0x71": ("0xA1", "0xB1", 100)}))
    assert block.hash == "0xaa"
    assert block.transactions[0].from_addr == "0xa1"
    assert block.transactions[0].amount == 100


@pytest.mark.parametrize(
    "payload,expected",
    [
        ([], RejectReason.NOT_AN_OBJECT),
        ({"height": 0, "hash": "0xaa"}, RejectReason.MISSING_FIELD),
        ({"height": "1", "hash": "0xaa", "parent_hash": "0x0"},
         RejectReason.BAD_TYPE),
        ({"height": 0, "hash": "zz", "parent_hash": "0x0"},
         RejectReason.BAD_VALUE),
        ({"height": -1, "hash": "0xaa", "parent_hash": "0x0"},
         RejectReason.BAD_VALUE),
        ({"height": 0, "hash": "0xaa", "parent_hash": "0xbb"},
         RejectReason.BAD_LINK),
        ({"height": 1, "hash": "0xaa", "parent_hash": "0x0"},
         RejectReason.BAD_LINK),
        ({"height": 0, "hash": "0xaa", "parent_hash": "0xaa"},
         RejectReason.BAD_LINK),
    ],
)
def test_reject_reasons_are_distinct(payload, expected):
    with pytest.raises(IngestError) as exc:
        parse_block(payload)
    assert exc.value.reason is expected


def test_duplicate_tx_in_single_payload_rejected():
    bad = make_block(0, "0xaa", "0x0")
    bad["transactions"] = [
        {"tx_hash": "0x77", "from": "0xa1", "to": "0xb1", "amount": 1},
        {"tx_hash": "0x77", "from": "0xa2", "to": "0xb2", "amount": 2},
    ]
    with pytest.raises(IngestError) as exc:
        parse_block(bad)
    assert exc.value.reason is RejectReason.DUPLICATE_TX_IN_BLOCK


def test_transactions_must_be_list():
    bad = make_block(0, "0xaa", "0x0")
    bad["transactions"] = "nope"
    with pytest.raises(IngestError) as exc:
        parse_block(bad)
    assert exc.value.reason is RejectReason.BAD_TYPE


def test_negative_amount_rejected():
    with pytest.raises(IngestError) as exc:
        parse_block(make_block(0, "0xaa", "0x0",
                               {"0x77": ("0xa1", "0xb1", -5)}))
    assert exc.value.reason is RejectReason.BAD_VALUE
