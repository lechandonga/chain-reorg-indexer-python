"""本地链上数据索引服务。

职责拆分：
- models:     领域模型与拒绝原因
- validation: 入参校验
- store:      SQLite 存储（原子事务）
- chain:      链头选择 / 分叉 / 最长链判定
- service:    投放编排（校验、去重、落库、重组）
- query:      对外查询（只读规范链）
- api:        FastAPI HTTP 接口
"""

__version__ = "0.1.0"
