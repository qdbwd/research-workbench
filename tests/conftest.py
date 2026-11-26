"""业务测试各自使用临时 schema，避免互相清表，更不能触碰真实模型账本。"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from knowledgeops_agent.persistence.models import Base

# 评估材料按发布范围仅在本地保留；缺少整个目录时不收集其专属测试。
# 本地目录存在时照常执行，不能吞掉评估代码或依赖自身的导入错误。
collect_ignore = [] if (Path(__file__).resolve().parents[1] / "evaluation").is_dir() else [
    "test_configured_delegation_evaluation.py",
    "test_evaluation_delivery_citations.py",
    "test_evaluation_runner.py",
    "test_evidence_manifest.py",
    "test_optimization_runner.py",
]


def pytest_collection_modifyitems(items):
    """CI 分片并行钩子：仅在显式提供 PYTEST_SHARD_INDEX/TOTAL（1-based）时启用。

    按文件粒度采用蛇形轮询，将迁移类重测试和大文件均匀打散到各分片，
    同时保持同文件内用例局部性；本地默认不触发。
    """
    raw_index = os.environ.get("PYTEST_SHARD_INDEX")
    raw_total = os.environ.get("PYTEST_SHARD_TOTAL")
    if not raw_index and not raw_total:
        return
    if not raw_index or not raw_total:
        raise ValueError("PYTEST_SHARD_INDEX and PYTEST_SHARD_TOTAL must both be set")
    index, total = int(raw_index), int(raw_total)
    if total < 1 or not (1 <= index <= total):
        raise ValueError(f"invalid pytest shard {index}/{total}")
    target = index - 1
    files = list(dict.fromkeys(item.path for item in items))
    files.sort(
        key=lambda path: ("migration" not in path.name, -path.stat().st_size, path.as_posix())
    )
    selected = {
        path
        for pos, path in enumerate(files)
        if ((pos % total) if (pos // total) % 2 == 0 else (total - 1 - (pos % total))) == target
    }
    items[:] = [item for item in items if item.path in selected]


@pytest.fixture(autouse=True)
def isolated_workspace_environment(monkeypatch):
    """测试必须显式注入临时工作区，不能继承 Compose 的真实挂载目录。"""
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)


@pytest.fixture(scope="session")
def _test_database_admin():
    """每个 pytest 会话只创建一次管理员连接与扩展，避免上千次重复 DDL 探测。"""
    url = os.environ["TEST_DATABASE_URL"]
    admin = create_engine(url)
    assert admin.url.database == "knowledgeops_test"
    with admin.begin() as connection:
        connection.execute(
            text("CREATE EXTENSION IF NOT EXISTS pg_search WITH SCHEMA public CASCADE")
        )
        connection.execute(
            text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public CASCADE")
        )
    try:
        yield url, admin
    finally:
        admin.dispose()


@pytest.fixture
def business_sessions(_test_database_admin):
    url, admin = _test_database_admin
    schema = "test_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
