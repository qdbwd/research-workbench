"""派生片段索引的写入与重建；调用方持有事务，绝不单独提交正文之外的索引。"""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from knowledgeops_agent.persistence.models import (
    KnowledgeSearchChunk,
    KnowledgeVersion,
    SearchEntry,
    Source,
    SourceSearchChunk,
    SourceSegment,
    SourceVersion,
    TaskHistoryChunk,
    TaskHistoryRecord,
)
from knowledgeops_agent.retrieval.search import split_passages, tokenize, weighted_vector


def add_knowledge_index(session, version: KnowledgeVersion) -> None:
    """建立可重建的当前索引和父行，再写入依赖父行的 BM25 片段。"""
    metadata = " ".join([*version.tags, version.summary, version.category])
    session.add(SearchEntry(
        document_id=version.document_id,
        version_id=version.id,
        vector=weighted_vector(version.body, title=version.title, metadata=metadata),
        normalized_text="\n".join([version.title, version.summary, version.category,
                                   " ".join(version.tags), version.body]).lower(),
    ))
    session.flush()
    add_knowledge_chunks(session, version)


def add_knowledge_chunks(session, version: KnowledgeVersion) -> int:
    """将当前版本正文分段；标题/摘要只提供排序背景，读取偏移始终指向body。"""
    metadata = " ".join([*version.tags, version.summary, version.category])
    passages = split_passages(version.body)
    for index, passage in enumerate(passages):
        session.add(
            KnowledgeSearchChunk(
                document_id=version.document_id,
                version_id=version.id,
                chunk_index=index,
                start=passage.start,
                end=passage.end,
                body_tokens=tokenize(passage.text),
                title_tokens=tokenize(version.title),
                metadata_tokens=tokenize(metadata),
            )
        )
    return len(passages)


def add_history_chunks(session, scope: str, row: dict) -> int:
    """只索引已经过可见文本白名单和脱敏的原话；不持有新的读取授权。"""
    passages = split_passages(row["text"])
    for index, passage in enumerate(passages):
        session.add(
            TaskHistoryChunk(
                scope=scope,
                source_id=row["id"],
                chunk_index=index,
                start=passage.start,
                end=passage.end,
                body_tokens=tokenize(passage.text),
            )
        )
    return len(passages)


def add_source_chunks(session, version_id: str) -> int:
    """与正文版本就绪一同提交；重复构建同一片段不会重复写入。"""
    segments = session.scalars(
        select(SourceSegment).where(
            SourceSegment.source_version_id == version_id, SourceSegment.status == "ready",
        ).order_by(SourceSegment.position)
    )
    count = 0
    for segment in segments:
        rows = [
            {
                "segment_id": segment.id, "chunk_index": index,
                "start": passage.start, "end": passage.end, "body_tokens": tokenize(passage.text),
            }
            for index, passage in enumerate(split_passages(segment.text))
        ]
        if rows:
            session.execute(insert(SourceSearchChunk).values(rows).on_conflict_do_nothing())
            count += len(rows)
    return count


def rebuild_source_index(sessions) -> dict[str, int]:
    """从可用的固定片段重建，不追加版本或改写引用。"""
    versions = chunks = 0
    with sessions.begin() as session:
        statement = (
            select(SourceVersion.id).join(Source, Source.id == SourceVersion.source_id)
            .where(Source.deleted_at.is_(None), SourceVersion.status == "ready")
            .order_by(SourceVersion.id).with_for_update(of=SourceVersion)
        )
        for version_id in session.scalars(statement):
            segment_ids = select(SourceSegment.id).where(SourceSegment.source_version_id == version_id)
            session.execute(delete(SourceSearchChunk).where(SourceSearchChunk.segment_id.in_(segment_ids)))
            chunks += add_source_chunks(session, version_id)
            versions += 1
    return {"versions": versions, "chunks": chunks}


def rebuild_history_index(sessions) -> dict[str, int]:
    """只重建原话派生片段；不产生批次、不修改来源ID和可达权限。

    按记录锁定并替换，避免全表DELETE误删同时新增的历史索引。失败回滚整个
    操作，已发布批次仍指向原始payload；调用方须作为显式运维操作使用。
    """
    records = chunks = 0
    with sessions.begin() as session:
        statement = (
            select(TaskHistoryRecord)
            .order_by(
                TaskHistoryRecord.scope,
                TaskHistoryRecord.source_id,
            )
            .with_for_update()
            .execution_options(yield_per=100)
        )
        for row in session.scalars(statement):
            session.execute(
                delete(TaskHistoryChunk).where(
                    TaskHistoryChunk.scope == row.scope,
                    TaskHistoryChunk.source_id == row.source_id,
                )
            )
            chunks += add_history_chunks(session, row.scope, row.payload)
            session.flush()
            records += 1
    return {"indexed_records": records, "indexed_chunks": chunks}
