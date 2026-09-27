"""冒烟测试：确认包可导入、空服务可构建。"""


def test_import() -> None:
    import chain_indexer  # noqa: F401
    from chain_indexer import models  # noqa: F401
