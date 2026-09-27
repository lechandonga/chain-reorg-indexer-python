# 数据模型

## 区块与交易结构

投放的区块 JSON：

```json
{
  "height": 0,
  "hash": "0x00",
  "parent_hash": "0x0",
  "transactions": [
    {"tx_hash": "0x7001", "from": "0xa11ce", "to": "0xb0b", "amount": 100}
  ]
}
```

- `hash` / 交易 / 地址均为 `0x` 开头的十六进制字符串，入库时统一转小写。
- 创世块固定为 `height=0` 且 `parent_hash="0x0"`；其余块 `parent_hash` 必须指向已知/将到的真实块。
- `amount` 与 `height` 为非负整数。

## 表结构

### blocks（原始归档，只增不删）

```sql
CREATE TABLE blocks (
    height      INTEGER NOT NULL,
    hash        TEXT    NOT NULL PRIMARY KEY,
    parent_hash TEXT    NOT NULL,
    payload     TEXT    NOT NULL,           -- 原始区块 JSON
    archived_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

所有通过结构校验的区块（包括父块缺失的暂存块、已被重组淘汰的分叉块）都留在这里。
重组不删 `blocks`，因此同一块在多次分叉切换间可被反复选为规范块而无需重投。

### canonical（当前规范链）

```sql
CREATE TABLE canonical (
    height     INTEGER NOT NULL PRIMARY KEY,
    block_hash TEXT    NOT NULL
);
```

每个高度最多一个块。链头 = `MAX(height)` 对应的 `block_hash`。重组时只改这张表。

### transactions（规范链交易索引）

```sql
CREATE TABLE transactions (
    tx_hash      TEXT PRIMARY KEY,
    block_hash   TEXT NOT NULL,
    block_height INTEGER NOT NULL,
    from_addr    TEXT NOT NULL,
    to_addr      TEXT NOT NULL,
    amount       INTEGER NOT NULL
);
```

只索引**当前规范链**上的交易。重组回滚某高度区间时，连带删除这些块上的全部交易行。
因此按 `tx_hash` 查询不会命中已回滚交易，也不会出现孤儿交易。

### tx_addresses（地址倒排）

```sql
CREATE TABLE tx_addresses (
    address TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    PRIMARY KEY (address, tx_hash)
);
```

一笔交易为 `from`、`to` 各写一行；随规范链交易索引一起插入/删除。

## 去重边界

| 场景 | 处理 |
| --- | --- |
| 同一区块（同 hash、同内容）重复投放 | `accepted_duplicate`，幂等，无新数据 |
| 同 hash 但 height/parent/负载不一致 | 拒绝（`bad_link` / `bad_value`），原数据保留 |
| 同高度不同 hash | 视为分叉，合法则归档；第二个创世块拒绝（`duplicate_genesis`） |
| 单个区块内交易哈希重复 | 拒绝（`duplicate_tx_in_block`） |
| 新链长尾交易与分叉点之前的共享前缀重复 | 拒绝本次重组（`duplicate_tx_on_chain`），整链不动 |
| 交易仅出现在互相竞争的分叉之间 | 允许；只有最终规范链上的那份进入交易索引 |
