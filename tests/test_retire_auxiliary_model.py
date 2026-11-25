"""验证通用 call_model 辅助模型工具彻底下线及历史数据/迁移兼容边界。"""

import json
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from test_api_worker import client as _client
from test_api_worker import services as _services
from test_runtime import runtime_services as _runtime_services
from test_runtime import setup_run

from knowledgeops_agent.harness.subagents.registry import options as subagent_options
from knowledgeops_agent.harness.tools.catalog import catalog_view
from knowledgeops_agent.harness.tools.groups import read_groups
from knowledgeops_agent.harness.tools.policy import baseline_policy
from knowledgeops_agent.harness.tools.preview import effective_catalog
from knowledgeops_agent.persistence.models import SubagentDefinition, SubagentDefinitionRevision

client = _client
services = _services
runtime_services = _runtime_services


def test_call_model_removed_from_catalog_groups_preview_and_graph(runtime_services):
    catalog = catalog_view([])
    assert all(row["name"] != "call_model" for row in catalog["tools"])
    assert all(row["id"] != "auxiliary" for row in catalog["groups"])
    assert all(row["name"] != "call_model" for row in subagent_options()["tools"])
    assert all(row["id"] != "auxiliary" for row in read_groups())

    # 镜像与首次部署使用的种子技能目录也不能残留 call_model 指令。
    seed_root = Path(__file__).resolve().parents[1] / "skill-seeds"
    for skill_doc in seed_root.rglob("SKILL.md"):
        assert "call_model" not in skill_doc.read_text(encoding="utf-8"), str(skill_doc)

    module, context, _ = setup_run(runtime_services)
    graph = module.build_agent(context, checkpointer=InMemorySaver())
    assert "call_model" not in graph.nodes["tools"].bound.tools_by_name
    assert all(row["name"] != "call_model" for row in context.tool_catalog)


def test_api_rejects_auxiliary_field_and_preserves_baseline_policy_compatibility(client, services):
    from knowledgeops_agent.persistence.models import Run

    conversation = client.post("/api/conversations", json={}).json()
    url = f"/api/conversations/{conversation['id']}/messages"

    rejected = client.post(
        url,
        json={"content": "普通提问", "request_key": str(uuid4()), "auxiliary": True},
    )
    assert rejected.status_code == 422

    req_key = str(uuid4())
    accepted = client.post(url, json={"content": "普通提问", "request_key": req_key})
    assert accepted.status_code == 202
    assert "auxiliary" not in accepted.json()["input"]

    # 若历史记录曾显式记录 auxiliary=True，新请求（无 auxiliary 字段）不能绕过 409 幂等冲突。
    with services.sessions.begin() as session:
        row = session.get(Run, accepted.json()["id"])
        row.submission_fingerprint = None
        row.input_data = {**row.input_data, "auxiliary": True}
    assert client.post(url, json={"content": "普通提问", "request_key": req_key}).status_code == 409

    preview = effective_catalog(services.sessions)
    assert all(row["name"] != "call_model" for row in preview["tools"])

    # 历史零版本策略合同保留 builtin:call_model，确保旧冻结策略哈希校验不失效。
    assert "builtin:call_model" in baseline_policy()["enabled"]
    assert services.gateway.ledger.records() == []


def test_migration_0057_cleans_active_configs_and_preserves_revisions():
    admin = create_engine(os.environ["TEST_DATABASE_URL"])
    assert admin.url.database == "knowledgeops_test"
    schema = "retire_auxiliary_" + uuid4().hex
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "0056")

            # 模拟升级前存在的默认/自定义策略与活跃子 Agent 引用了 call_model 和 auxiliary 组。
            existing_row = connection.execute(
                text("SELECT content, revision FROM tool_policies WHERE id = 'default'")
            ).mappings().one()
            policy_content = {
                **existing_row["content"],
                "enabled": {
                    **existing_row["content"].get("enabled", {}),
                    "builtin:read_source": True,
                    "builtin:call_model": True,
                },
                "main_tool_groups": ["materials_read", "auxiliary"],
                "main_tools": ["builtin:call_model"],
                "resolved_main_tools": ["builtin:read_source", "builtin:call_model"],
                "tool_epochs": {
                    **existing_row["content"].get("tool_epochs", {}),
                    "builtin:call_model": 1,
                    "builtin:read_source": 0,
                },
                "main_epochs": {
                    **existing_row["content"].get("main_epochs", {}),
                    "builtin:call_model": 1,
                },
            }
            expected_next_revision = existing_row["revision"] + 1
            connection.execute(
                text("UPDATE tool_policies SET content = CAST(:content AS jsonb) WHERE id = 'default'"),
                {"content": json.dumps(policy_content)},
            )
            with Session(bind=connection) as orm_session:
                orm_session.add(
                    SubagentDefinition(
                        id="custom-aux",
                        name="custom-aux",
                        display_name="历史核对",
                        description="含旧工具",
                        system_prompt="提示词",
                        tools=["read_source", "call_model"],
                        tool_groups=["materials_read", "auxiliary"],
                        resolved_tools=["read_source", "call_model"],
                        skills=[],
                        enabled=True,
                        deleted=False,
                        revision=1,
                        authorization_epoch=1,
                    )
                )
                orm_session.add(
                    SubagentDefinitionRevision(
                        definition_id="custom-aux",
                        revision=1,
                        content={"tools": ["read_source", "call_model"], "tool_groups": ["auxiliary"]},
                    )
                )
                orm_session.flush()

            command.upgrade(config, "head")
            command.upgrade(config, "head")

            aux_group = connection.execute(
                text("SELECT tool_ids, deleted, revision FROM tool_groups WHERE id = 'auxiliary'")
            ).mappings().one()
            assert aux_group["deleted"] is True
            assert aux_group["tool_ids"] == []

            policy_after = connection.execute(
                text("SELECT content, revision FROM tool_policies WHERE id = 'default'")
            ).mappings().one()
            assert "builtin:call_model" not in policy_after["content"]["enabled"]
            assert "auxiliary" not in policy_after["content"]["main_tool_groups"]
            assert "builtin:call_model" not in policy_after["content"]["main_tools"]
            assert "builtin:call_model" not in policy_after["content"]["resolved_main_tools"]
            assert policy_after["content"]["tool_epochs"]["builtin:call_model"] == 2
            assert policy_after["content"]["main_epochs"]["builtin:call_model"] == 2
            assert policy_after["revision"] == expected_next_revision

            subagent_after = connection.execute(
                text("SELECT tools, tool_groups, revision FROM subagent_definitions WHERE id = 'custom-aux'")
            ).mappings().one()
            assert subagent_after["tools"] == ["read_source"]
            assert subagent_after["tool_groups"] == ["materials_read"]
            assert subagent_after["revision"] == 2

            # 历史修订保持原样不可变。
            old_rev = connection.execute(
                text(
                    "SELECT content FROM subagent_definition_revisions "
                    "WHERE definition_id = 'custom-aux' AND revision = 1"
                )
            ).scalar_one()
            assert "call_model" in old_rev["tools"]
            assert "auxiliary" in old_rev["tool_groups"]
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
