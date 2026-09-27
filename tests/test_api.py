"""HTTP 端到端：经 FastAPI 把"接收-落库-查询"整条链路跑一遍。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chain_reorg_indexer.api import create_app
from chain_reorg_indexer.feed import block_to_payload, chain, genesis
from chain_reorg_indexer.service import IndexerService
from chain_reorg_indexer.store import IndexStore


@pytest.fixture
def client():
    store = IndexStore(":memory:")
    svc = IndexerService(store)
    return TestClient(create_app(svc))


def _post(client, payload, seq=None):
    return client.post("/blocks", json=payload)


def test_full_pipeline_over_http(client):
    g = genesis()
    a1, a2 = chain("A", 2, parent=g)

    # 乱序投放
    r = _post(client, block_to_payload(a2))
    assert r.status_code == 200 and r.json()["accepted"]
    r = _post(client, block_to_payload(g))
    assert r.status_code == 200
    r = _post(client, block_to_payload(a1))
    assert r.status_code == 200 and r.json()["head"]["hash"] == "A-h2"

    # 按高度 / 链头查询
    assert client.get("/head").json()["height"] == 2
    block = client.get("/blocks/height/2").json()
    assert block["hash"] == "A-h2"

    # 高度不存在 -> 404
    assert client.get("/blocks/height/9").status_code == 404


def test_reorg_over_http_rolls_back_queries(client):
    g = genesis()
    a1, a2 = chain("A", 2, parent=g)
    b1, b2, b3 = chain("B", 3, parent=g, work_step=2)
    for b in (g, a1, a2):
        _post(client, block_to_payload(b))
    assert client.get("/blocks/height/2").json()["hash"] == "A-h2"
    rs = [_post(client, block_to_payload(b)) for b in (b1, b2, b3)]
    # B-h2 起工作量反超 -> 重组；B-h3 只是延长
    assert [r.json()["reorg"] for r in rs] == [False, True, False]

    # 旧高度 2 的规范块已换成 B
    assert client.get("/blocks/height/2").json()["hash"] == "B-h2"
    assert client.get("/head").json()["hash"] == "B-h3"


def test_rejection_reasons_distinguished_over_http(client):
    r = _post(client, {"height": 0})  # 缺 hash
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "missing_field"

    r = _post(client, {"height": 0, "hash": "  "})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "invalid_format"

    r = _post(client, {"height": 1, "hash": "x", "parent_hash": ""})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "parent_chain_error"

    items = client.get("/rejections").json()["items"]
    reasons = {i["reason"] for i in items}
    assert {"missing_field", "invalid_format", "parent_chain_error"} <= reasons


def test_address_query_only_canonical(client):
    from chain_reorg_indexer.feed import make_block, make_tx
    g = genesis()
    a1 = make_block(1, "A-h1", g.hash,
                    (make_tx("t-a", "A-h1", "alice", "bob", 10),))
    b1 = make_block(1, "B-h1", g.hash,
                    (make_tx("t-b", "B-h1", "alice", "carol", 20),),
                    chainwork=3)
    _post(client, block_to_payload(g))
    _post(client, block_to_payload(a1))
    resp = client.get("/address/alice").json()
    assert [t["tx_hash"] for t in resp["transactions"]] == ["t-a"]
    _post(client, block_to_payload(b1))  # 工作量更大，顶替
    resp = client.get("/address/alice").json()
    assert [t["tx_hash"] for t in resp["transactions"]] == ["t-b"]

    assert client.get("/tx/t-a").status_code == 404
    assert client.get("/tx/t-b").status_code == 200
