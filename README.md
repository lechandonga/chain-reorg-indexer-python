# 本地链上数据索引服务（支持链重组）

一个本地运行的链上数据索引：接收本地投放的模拟区块与交易 → 落库 → 对外查询。
重点保证在 **乱序到达、重复投放、链重组（reorg）** 三类情况下，结果确定且一致：

- 同一高度 / 同一笔交易重复投放不会产生重复数据；
- 乱序到达不会让链头倒退，也不会丢数据（先暂存，补齐后连通）；
- 短分叉被更长链顶替时，旧块和交易索引干净回滚，不留孤儿交易或半更新状态；
- 查询始终对齐当前链头，读不到已回滚数据，也读不到写一半的状态；
- 格式错误、字段缺失等拒绝原因可区分（响应中带 `reason`）。

## 快速开始

```bash
uv sync --group dev

# 跑全部测试
.venv/bin/python -m pytest -q

# 本地端到端演示（乱序/重复/等长分叉/长链重组/查询）
.venv/bin/python scripts/feed_demo.py

# 启动 HTTP 服务
.venv/bin/python main.py
# 或
.venv/bin/uvicorn main:app --reload
# Swagger 文档: http://127.0.0.1:8000/docs
```

使用持久化数据库：

```bash
INDEXER_DB=./data/indexer.db .venv/bin/python main.py
.venv/bin/python scripts/feed_demo.py --db ./data/demo.db
```

## 数据模型（SQLite）

| 表 | 作用 | 关键字段 |
| --- | --- | --- |
| `blocks` | **所有到达区块**的原始归档（含未连通暂存块），只增不删 | `height`, `hash`(PK), `parent_hash`, `payload(JSON)` |
| `canonical` | **当前规范链**每个高度对应的块哈希（链头权威视图） | `height`(PK), `block_hash` |
| `transactions` | 当前规范链上的**交易索引** | `tx_hash`(PK), `block_hash`, `block_height`, `from_addr`, `to_addr`, `amount` |
| `tx_addresses` | 地址 ↔ 交易的**倒排表**，支持按地址查 | `(address, tx_hash)` PK |

设计要点：

- **原始数据与链视图分离**：`blocks` 永久归档，重组只改 `canonical` / `transactions` / `tx_addresses`。
  这样分叉块在以后再次成为规范链时无需重新投放。
- 只有规范链上的交易才进入 `transactions` 与 `tx_addresses`，因此查询天然不会返回回滚数据。
- 入库前统一做结构校验（见[拒绝原因](#拒绝原因reason)），非法数据不会被悄悄丢弃，而是返回 422 与原因。

更详细的规则见 [`docs/data-model.md`](docs/data-model.md)。

## 链重组规则

- 一条链必须从创世块（`height=0, parent_hash=0x0`）开始、父子哈希逐块相连、高度连续。
- 父块尚未到达的区块标记为**暂存**（`accepted_pending`），不参与链头竞争；父块补齐后自动连通。
- 链头选择：在所有连通到创世的链中取**最长**者；**等长时不重组**（保留现有链头），
  无现有链头时按 tip 哈希字典序取最小，保证结果确定。
- 发生重组时在**单个数据库事务**内完成：
  1. 计算分叉点（新旧链最后一个共同块）；
  2. 校验新尾交易唯一、且不与分叉点之前的共享前缀冲突；
  3. 删除旧尾在 `canonical` / `transactions` / `tx_addresses` 的全部索引；
  4. 写入新尾的规范映射与交易索引。
  
  任何一步失败则整体回滚，链头与索引停留在重组前，不产生半成品。

详见 [`docs/reorg-rules.md`](docs/reorg-rules.md)。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/blocks` | 投放单个区块 |
| `POST` | `/blocks/batch` | 批量投放，body：`{"blocks": [...]}`，返回逐条结果 |
| `GET` | `/status` | 当前链头 `{tip_height, tip_hash}` |
| `GET` | `/blocks/height/{height}` | 按高度查规范链区块 |
| `GET` | `/blocks/hash/{hash}` | 按哈希查（仅当在规范链上） |
| `GET` | `/transactions/{tx_hash}` | 按交易哈希查 |
| `GET` | `/addresses/{address}/transactions` | 按地址查其参与的交易 |

投放区块格式：

```json
{
  "height": 2,
  "hash": "0x2b",
  "parent_hash": "0x11",
  "transactions": [
    {"tx_hash": "0x7b01", "from": "0xb0b", "to": "0xca201", "amount": 7}
  ]
}
```

投放结果：

```json
{
  "accepted": true,
  "kind": "accepted_new",
  "height": 2,
  "block_hash": "0x2b",
  "reason": null,
  "detail": "分叉高度=1，旧链回滚高度>=2，新链更长(4>3)",
  "reorg": true,
  "new_tip": "0x3b"
}
```

`kind` 取值：`accepted_new` / `accepted_duplicate`（幂等重复）/ `accepted_pending`（父块未知暂存）/ `rejected`。

状态码：`201` 单投接受，`200` 批量通过/查询，`422` 拒绝（带 `reason`），`404` 查询对象不在当前规范链。

### 拒绝原因（reason）

| reason | 含义 |
| --- | --- |
| `malformed_json` | 请求体不是合法 JSON |
| `not_an_object` | 负载不是 JSON 对象 |
| `missing_field` | 缺少必需字段 |
| `bad_type` | 字段类型错误 |
| `bad_value` | 字段取值非法（如非十六进制哈希、负数） |
| `bad_link` | 区块链接非法（创世父哈希、自指、同哈希内容不一致） |
| `duplicate_genesis` | 试图投放第二个创世块 |
| `duplicate_tx_in_block` | 单个区块内交易哈希重复 |
| `duplicate_tx_on_chain` | 新链与共享前缀出现重复交易，重组被整体拒绝 |

## 代码结构（按职责拆分）

```
chain_indexer/
  models.py      领域模型、拒绝原因枚举、投放结果
  validation.py  入参校验（区分拒绝原因）
  store.py       SQLite 存储、原子事务、读写加锁
  chain.py       纯链逻辑：连通回溯、最长链、分叉点
  service.py     投放编排：校验→去重→归档→选链→原子重组
  query.py       只读查询（只看规范链）
  api.py         FastAPI 路由
scripts/
  feed_demo.py   本地模拟区块投放演示
tests/
  test_validation.py / test_dedup.py / test_ordering.py
  test_reorg.py / test_query.py / test_concurrency.py
docs/
  data-model.md / reorg-rules.md / local-verification.md
```

## 一致性保证怎么落地

- 存储层用单把可重入锁 + `BEGIN IMMEDIATE`：写全程持锁，读在同锁下开事务快照，
  边写边查只会看到重组前或重组后的完整链。
- 重组在一个事务里删旧插新，失败整体回滚（`tests/test_reorg.py` 验证无半更新）。
- 测试日志按 `seq`（投放顺序）记录区块高度、哈希、判定类型与依据，便于复盘。
