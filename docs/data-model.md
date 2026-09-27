# 索引数据模型

存储使用本地 SQLite（默认 `indexer.db`，WAL 模式）。所有写入在同一个事务里提交，
查询只 JOIN 当前规范链，因此读请求要么看到旧链、要么看到新链，看不到写一半的状态。

## 表结构

### blocks（收到的所有区块，含分叉/孤儿）

| 字段        | 含义                                                        |
|-------------|-------------------------------------------------------------|
| height      | 区块高度                                                    |
| hash        | 区块哈希，主键；同哈希重复投放不会产生第二行                 |
| parent_hash | 父区块哈希；创世块为空串 `""`                                |
| chainwork   | 该区块所在链的累计工作量，用于"最长链（最重链）"判定         |
| connected   | 1 = 沿父指针可追溯到创世块；0 = 父块尚未到达的孤儿块         |
| ingest_seq  | 投放序号（仅用于排查，不参与链选择）                         |

- 区块到达时父块未知也**不会被拒绝或丢弃**，以 `connected=0` 缓存；
  父块日后到达时自动级联把整条后代链标记为 connected。
- 区块被重组替换后仍保留在本表（分叉历史可审计），只是从 `canonical` 移除。

### transactions（所有交易）

| 字段       | 含义                                   |
|------------|----------------------------------------|
| tx_hash    | 交易哈希，主键；重复投放幂等           |
| block_hash | 所属区块，外键 -> blocks.hash          |
| from_addr / to_addr / value / tx_index | 地址、金额、块内序号 |

- 同一 `tx_hash` 出现在两个不同区块会被拒绝（`duplicate_tx`），全库唯一。
- 交易随区块写入；区块因校验失败整体回滚时不会留下孤儿交易。

### canonical（当前规范链：高度 -> 区块）

| 字段       | 含义                  |
|------------|-----------------------|
| height     | 高度，主键，每高度唯一 |
| block_hash | 该高度当前的规范块    |

重组时在一个事务内 `DELETE` + 重建本表，这是"干净回滚"的唯一落点：
所有对外查询都 `JOIN canonical`，被替换链上的区块与交易**物理仍在库中、逻辑上不可见**。

### rejections（被拒绝的投放，保证不静默丢弃）

记录 reason / detail / block_hash / height / seq，可通过 `GET /rejections` 查询。

## 领域对象

`chain_reorg_indexer.models`：`Block`、`Transaction`、`ChainHead`、`IngestResult`、
`Rejection`、`TxView`。外部 JSON 先经 `validation.parse_block` 严格校验才进入存储。

## 拒绝原因（稳定枚举）

| reason              | 含义                                         | HTTP |
|---------------------|----------------------------------------------|------|
| missing_field       | 必填空缺（height/hash/交易字段等）            | 422  |
| invalid_format      | 类型错误、负数、布尔、空哈希、非法 JSON       | 422  |
| parent_chain_error  | 高度与父哈希矛盾、父指针成环                  | 422  |
| block_txs_mismatch  | 交易声明的 block_hash 与所在区块不一致        | 422  |
| duplicate_tx        | 交易哈希在块内重复、或已属于其他区块          | 409  |
| duplicate_block     | 同哈希区块再次投放但字段冲突                  | 409  |

> 同哈希、同内容的区块重复投放是幂等的，返回接受，不产生重复数据。
