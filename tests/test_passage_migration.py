"""验证有旧数据的0016→0017，不以空库建表通过代替实际回填。"""

import json
import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker


def test_empty_database_enables_extension_and_builds_bm25_indexes():
    """扩展属于整个数据库；仅用已有扩展的临时schema不能验证新安装。"""
    admin = create_engine(os.environ["TEST_DATABASE_URL"], isolation_level="AUTOCOMMIT")
    assert admin.url.database == "knowledgeops_test"
    database = "knowledgeops_bm25_test_" + uuid4().hex
    fresh = create_engine(admin.url.set(database=database))
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}" TEMPLATE template0'))
        with fresh.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM pg_extension WHERE extname='pg_search'")
                ).scalar_one()
                == 0
            )
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert (
                connection.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname='pg_search'")
                ).scalar_one()
                == "0.26.1"
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE indexname IN ('ix_history_chunks_bm25','ix_knowledge_chunks_bm25','ix_source_chunks_bm25') AND indexdef LIKE '%USING paradedb%'"
                    )
                ).scalar_one()
                == 3
            )
    finally:
        fresh.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        admin.dispose()


def test_joint_search_migration_preserves_source_evidence_and_frozen_tool_policy(tmp_path):
    from knowledgeops_agent.harness.tools.policy import ToolPolicyService
    from knowledgeops_agent.persistence.models import Evidence, SourceSegment, SourceVersion
    from knowledgeops_agent.sources.service import SourceService

    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    assert engine.url.database == "knowledgeops_test"
    schema = "source_search_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "0052")
            sessions = sessionmaker(bind=connection, expire_on_commit=False)
            quote = "紧急变更应由当班负责人审批。"
            # 0052 的材料没有试用归属列，按当时结构写入旧记录。
            connection.execute(text("""
                INSERT INTO sources (id, identity_key, title, kind, created_at)
                VALUES ('source', 'old-import', '旧手册', 'file', now())
            """))
            with sessions.begin() as session:
                session.add(SourceVersion(
                    id="version", source_id="source", number=1,
                    content_hash="0" * 64, mime_type="text/plain",
                ))
                session.flush()
                session.add(SourceSegment(
                    id="segment", source_version_id="version", position=0,
                    start_offset=0, end_offset=len(quote), text=quote,
                ))
                session.flush()
                session.add(Evidence(
                    id="original-citation", root_id="finished-run", source_segment_id="segment",
                    start_offset=0, end_offset=len(quote), quote=quote,
                ))
            command.upgrade(config, "0053")
            policy = ToolPolicyService(sessions)
            frozen = policy.update(expected_revision=policy.get()["revision"], main_tools=["builtin:read_knowledge"])
            previous = policy.history()
            command.upgrade(config, "0054")
            assert policy.history()[:-1] == previous
            updated = policy.get()
            assert updated["revision"] == frozen["revision"] + 1
            assert updated["enabled"]["builtin:search_references"] is True
            assert updated["resolved_main_tools"] == ["builtin:read_knowledge"]

            command.upgrade(config, "0055")
            sources = SourceService(sessions, tmp_path)
            [hit] = sources.search("紧急变更 负责人")
            assert (hit["segment_id"], hit["version_id"], hit["snippet"]) == ("segment", "version", quote)
            assert sources.get_evidence("original-citation")["text"] == quote
            assert connection.scalar(text("SELECT trial_conversation_id FROM sources WHERE id = 'source'")) is None
            command.downgrade(config, "0052")
            assert connection.scalar(text("SELECT quote FROM evidence WHERE id = 'original-citation'")) == quote
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        engine.dispose()


def test_nonempty_backfill_preserves_sources_and_searches_late_chunks():
    from knowledgeops_agent.knowledge.service import KnowledgeService
    from knowledgeops_agent.persistence.models import KnowledgeSearchChunk, TaskHistoryChunk
    from knowledgeops_agent.retrieval.index import rebuild_history_index

    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    assert engine.url.database == "knowledgeops_test"
    schema = "passage_migration_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            # 从真实旧结构开始。不能先升head再降级：后续记忆/预算迁移有意拒绝
            # 有损降级；也不使用未来ORM/发布服务给旧结构造数据。
            command.upgrade(config, "0016")
            body = "背景段落。" * 500 + "\n\n旧正文末尾：蓝绿部署支持回滚。"
            connection.execute(
                text("INSERT INTO knowledge_documents(id, created_at) VALUES ('legacy-doc', now())")
            )
            connection.execute(
                text("""INSERT INTO knowledge_versions
                    (id, document_id, number, title, body, summary, category, tags,
                     archived, action, root_id, created_at)
                    VALUES ('legacy-version', 'legacy-doc', 1, '发布方法', :body, '', '',
                            '[]'::jsonb, false, 'create', 'migration-root', now())"""),
                {"body": body},
            )
            connection.execute(
                text("UPDATE knowledge_documents SET current_version_id='legacy-version'")
            )
            connection.execute(
                text("""INSERT INTO search_entries(document_id, version_id, vector, normalized_text)
                     VALUES ('legacy-doc', 'legacy-version', to_tsvector('simple', :body), :body)"""),
                {"body": body},
            )
            payload = {
                "id": "r" + "a" * 32,
                "role": "human",
                "message_id": "legacy",
                "name": None,
                "text": "说明。" * 1000 + "\n\n原话：先查回执。",
                "truncated": False,
            }
            connection.execute(
                text("""INSERT INTO task_history_records(scope, source_id, payload, search_terms)
                     VALUES (:scope, :source, CAST(:payload AS jsonb), ARRAY['回执'])"""),
                {"scope": "s" * 64, "source": payload["id"], "payload": json.dumps(payload)},
            )
            before = connection.execute(
                text("SELECT payload FROM task_history_records")
            ).scalar_one()
            versions = list(
                connection.execute(text("SELECT id, body FROM knowledge_versions ORDER BY id"))
            )
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            sessions = sessionmaker(bind=connection, expire_on_commit=False)
            knowledge = KnowledgeService(sessions)
            hit = knowledge.search("蓝绿 回滚")[0]
            assert hit["start"] > 600 and "回滚" in hit["snippet"]
            assert (
                connection.execute(text("SELECT payload FROM task_history_records")).scalar_one()
                == before
            )
            assert (
                list(
                    connection.execute(text("SELECT id, body FROM knowledge_versions ORDER BY id"))
                )
                == versions
            )
            with sessions() as session:
                knowledge_chunks = list(session.scalars(select(KnowledgeSearchChunk)))
                history_chunks = list(session.scalars(select(TaskHistoryChunk)))
                assert len(knowledge_chunks) > 1 and len(history_chunks) > 1
            original_index = list(
                connection.execute(
                    text(
                        'SELECT scope, source_id, chunk_index, start, "end", body_tokens FROM task_history_chunks ORDER BY chunk_index'
                    )
                )
            )
            result = rebuild_history_index(sessions)
            assert result["indexed_records"] == 1
            assert (
                list(
                    connection.execute(
                        text(
                            'SELECT scope, source_id, chunk_index, start, "end", body_tokens FROM task_history_chunks ORDER BY chunk_index'
                        )
                    )
                )
                == original_index
            )
            assert json.loads(json.dumps(before)) == payload
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
