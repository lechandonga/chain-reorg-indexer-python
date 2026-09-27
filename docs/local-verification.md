# 本地验证手册

## 环境准备

```bash
uv sync --group dev
```

## 方式一：自动化测试（推荐）

```bash
# 全部用例
.venv/bin/python -m pytest -q

# 按主题
.venv/bin/python -m pytest tests/test_ordering.py -v    # 乱序
.venv/bin/python -m pytest tests/test_dedup.py -v       # 重复投放
.venv/bin/python -m pytest tests/test_reorg.py -v       # 链重组
.venv/bin/python -m pytest tests/test_query.py -v       # 查询对齐链头
.venv/bin/python -m pytest tests/test_concurrency.py -v # 边写边查
```

观察日志中的判定依据：

```bash
.venv/bin/python -m pytest tests/test_reorg.py -o log_cli=true -q
```

每条日志带：投放 `seq`、区块高度、哈希、判定类型（kind）、拒绝原因、
是否重组、判定依据（分叉高度/新链更长等）。

## 方式二：演示脚本

```bash
.venv/bin/python scripts/feed_demo.py            # 内存库，过程直观
.venv/bin/python scripts/feed_demo.py --db ./data/demo.db
```

脚本依次演示：乱序暂存与补连通、重复投放幂等、等长分叉不重组、长链顶替短链、
回滚后查询结果。

## 方式三：HTTP 手动验证

启动服务：

```bash
.venv/bin/python main.py
```

另开终端：

```bash
# 创世
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' -d '{
  "height":0,"hash":"0x00","parent_hash":"0x0",
  "transactions":[{"tx_hash":"0x7001","from":"0xa11ce","to":"0xb0b","amount":100}]}'

# 重复投放 -> kind=accepted_duplicate
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' -d '{
  "height":0,"hash":"0x00","parent_hash":"0x0",
  "transactions":[{"tx_hash":"0x7001","from":"0xa11ce","to":"0xb0b","amount":100}]}'

# 乱序：先发高度2（暂存），再补高度1
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' -d '{"height":2,"hash":"0x22","parent_hash":"0x11","transactions":[]}'
curl -s -X POST localhost:8000/blocks -H 'content-type: application/json' -d '{"height":1,"hash":"0x11","parent_hash":"0x00","transactions":[]}'

# 链头 / 交易 / 地址查询
curl -s localhost:8000/status
curl -s localhost:8000/transactions/0x7001
curl -s localhost:8000/addresses/0xa11ce/transactions

# 非法数据：422 且 reason 可区分
curl -i -X POST localhost:8000/blocks -H 'content-type: application/json' -d '{"height":0}'
```

## 验收对照

| 需求 | 对应测试 |
| --- | --- |
| 重复高度/交易不产生重复数据 | `test_dedup.py` |
| 乱序不丢数据、链头不倒退 | `test_ordering.py` |
| 拒绝原因可区分、不静默丢弃 | `test_validation.py` |
| 重组干净回滚、无孤儿/半更新 | `test_reorg.py` |
| 查询对齐当前链头 | `test_query.py` |
| 边写边查一致 | `test_concurrency.py` |
