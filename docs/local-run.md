# 本地运行与验证

## 环境

- Python >= 3.14（由 `pyproject.toml` 指定）
- 用 [uv](https://docs.astral.shop/uv/) 管理依赖：`fastapi` 运行，`pytest`/`httpx` 开发依赖。

## 跑测试

```bash
uv sync --group dev
uv run pytest                 # 全部用例，-s 已默认开启，可看到投放顺序日志
uv run pytest tests/test_reorg.py -q
```

测试日志会逐条打印：投放 `seq`、区块高度、区块哈希、
判定依据（接受并标明是否成为规范块 / 是否触发重组，或拒绝原因）。

## 起服务

```bash
uv run python main.py
# 或： INDEXER_DB=/tmp/idx.db uv run uvicorn main:app --reload
```

默认监听 `http://127.0.0.1:8000`，SQLite 文件为 `./indexer.db`
（可用环境变量 `INDEXER_DB` 覆盖）。交互文档：`http://127.0.0.1:8000/docs`。

## HTTP 接口

| 方法 & 路径                  | 说明                              |
|------------------------------|-----------------------------------|
| POST `/blocks`               | 投放一个区块（JSON）              |
| GET  `/blocks/height/{h}`    | 查规范链上某高度的区块及其交易    |
| GET  `/tx/{tx_hash}`         | 按交易哈希查（仅规范链）          |
| GET  `/address/{address}`    | 查某地址相关交易（from 或 to）    |
| GET  `/head`                 | 当前规范链头                      |
| GET  `/rejections`           | 最近被拒绝的投放及原因            |

区块 JSON 示例：

```json
{
  "height": 1,
  "hash": "0xA1",
  "parent_hash": "0xG0",
  "chainwork": 2,
  "transactions": [
    {"tx_hash": "0xt1", "block_hash": "0xA1",
     "from": "alice", "to": "bob", "value": 10, "index": 0}
  ]
}
```

创世块：`height=0`，`parent_hash` 省略或传 `""`。
`chainwork` 可省略（缺省 = height + 1）。

## 端到端手工验证（乱序 / 重复 / 重组）

```bash
# 1) 乱序：先投高度1（变孤儿），再投创世
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' \
  -d '{"height":1,"hash":"0xA1","parent_hash":"0xG0","transactions":[]}'
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' \
  -d '{"height":0,"hash":"0xG0","transactions":[]}'
curl -s localhost:8000/head        # -> 链头 0xA1

# 2) 重复投放：幂等，无重复数据
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' \
  -d '{"height":0,"hash":"0xG0","transactions":[]}'

# 3) 非法数据：原因可区分，且能在 /rejections 查到
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' \
  -d '{"height":1}'                                   # missing_field
curl -s localhost:8000/rejections | python -m json.tool

# 4) 重组：投一条工作量更大的分叉顶替 0xA1
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' \
  -d '{"height":1,"hash":"0xB1","parent_hash":"0xG0","chainwork":9,"transactions":[]}'
curl -s localhost:8000/blocks/height/1     # -> 已换成 0xB1；0xA1 查询 404
```

## 一键演示脚本（不经 HTTP，直接驱动服务）

```bash
uv run python scripts/demo.py
```

脚本会依次演示：乱序到达、重复幂等、非法数据被区分拒绝、短分叉被长链顶替、
回滚后旧链交易不可见，并打印每一步的链头与判定依据。
