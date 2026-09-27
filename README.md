# chain-reorg-indexer（本地链上数据索引服务）

用本地投放的模拟区块，把 **接收数据 → 落库 → 对外查询** 整条链路跑通的最小索引服务，
重点保证**乱序、重复投放、链重组**场景下结果确定且一致。

## 能力

- **接收与落库**：`POST /blocks` 投放区块与交易；本地 SQLite（WAL）持久化。
  - 同高度/同交易重复投放不产生重复数据（同哈希区块幂等；交易哈希全局唯一）。
  - 乱序到达：父块缺失的区块作为孤儿缓存，父块到达后自动级联上线，链头不倒退、数据不丢。
  - 非法数据不静默丢弃：按 `missing_field / invalid_format / parent_chain_error /
    block_txs_mismatch / duplicate_tx / duplicate_block` 区分原因，并记录可查。
- **链重组**：按累计工作量（chainwork）选最重链，等工作量按 tip 哈希字典序确定性平局；
  替换在单事务内原子完成，旧区块/交易索引干净回滚（查询不可见、不留孤儿交易、无半更新状态）。
- **对外查询**：按高度、按交易哈希、按地址查询；查询只 JOIN 当前规范链，
  重组或边写边查时读到的始终是某一条完整链的已提交快照。

## 代码结构（按职责拆分）

```
chain_reorg_indexer/
  models.py      # 领域类型：Block / Transaction / ChainHead / IngestResult / Rejection
  errors.py      # 拒绝原因枚举与 HTTP 状态映射
  validation.py  # 外部 JSON -> 领域模型的严格校验
  store.py       # SQLite 表结构、原子写入、孤儿级联、规范链重选/重组、查询
  service.py     # 门面：校验→落库→拒绝记录→日志
  api.py         # FastAPI HTTP 层
  feed.py        # 本地模拟链/分叉构造工具
scripts/demo.py  # 一键演示（乱序/重复/拒绝/重组）
tests/           # 乱序、重复、重组、HTTP 端到端、并发边写边查
docs/            # 数据模型、重组规则、本地运行手册
main.py          # 启动入口
```

## 快速开始

```bash
uv sync --group dev
uv run pytest                      # 跑测试（日志含高度/投放顺序/判定依据）
uv run python scripts/demo.py      # 不走网络的全链路演示
uv run python main.py              # 起 HTTP 服务 :8000
```

详见：

- [docs/data-model.md](docs/data-model.md) —— 表结构与拒绝原因
- [docs/reorg.md](docs/reorg.md) —— 规范链选择与重组规则
- [docs/local-run.md](docs/local-run.md) —— 本地运行、curl 验证步骤
