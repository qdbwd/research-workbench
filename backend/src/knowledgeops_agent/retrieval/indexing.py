"""事务外盒与单资产向量写回。"""

import logging
from datetime import timedelta

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.app.runs.queue import RAG_INDEX_TASK
from knowledgeops_agent.persistence import models as db
from knowledgeops_agent.retrieval.collections import (
    chunk_identity,
    chunk_query,
    chunk_text,
    live_asset,
)
from knowledgeops_agent.retrieval.embeddings import EmbeddingError, embedding_content_hash

logger = logging.getLogger(__name__)


def enqueue_asset_index(session, *, asset_kind, asset_id, version_id, force_enqueue=False):
    settings = session.get(db.RagSettings, "default")
    if not force_enqueue and not (settings and settings.hybrid_enabled and settings.auto_index_enabled):
        # 旧版正在计算时，关闭自动入队后的发布仍使旧任务失效。
        session.execute(delete(db.RagIndexJob).where(
            db.RagIndexJob.asset_kind == asset_kind, db.RagIndexJob.asset_id == asset_id,
            db.RagIndexJob.version_id != version_id,
        ))
        return None
    now = db.utcnow()
    values = dict(
        version_id=version_id, profile_id=settings.active_profile_id if settings else None,
        status="pending", attempts=0, next_attempt_at=now, lease_until=None, last_error=None,
        chunks_total=0, chunks_embedded=0, started_at=None, finished_at=None, updated_at=now,
    )
    statement = insert(db.RagIndexJob).values(asset_kind=asset_kind, asset_id=asset_id, **values)
    return session.scalar(statement.on_conflict_do_update(
        constraint="uq_rag_index_jobs_asset", set_={**values, "generation": db.RagIndexJob.generation + 1},
    ).returning(db.RagIndexJob).execution_options(populate_existing=True))


def maybe_enqueue_asset_index(session, **asset):
    return enqueue_asset_index(session, **asset)


def remove_rag_asset(session, kind, identity, *, remove_members=True, remove_job=True):
    if remove_job:
        session.execute(delete(db.RagIndexJob).where(
            db.RagIndexJob.asset_kind == kind, db.RagIndexJob.asset_id == identity,
        ))
    if remove_members:
        group = db.RagCollection
        member = db.RagCollectionItem
        affected = select(member.collection_id).where(member.asset_kind == kind, member.asset_id == identity)
        session.execute(update(group).where(group.id.in_(affected)).values(
            revision=group.revision + 1, updated_at=db.utcnow(),
        ))
        session.execute(delete(member).where(member.asset_kind == kind, member.asset_id == identity))


def job_view(job):
    return {name: getattr(job, name) for name in (
        "id", "asset_kind", "asset_id", "version_id", "profile_id", "status", "generation",
        "attempts", "max_attempts", "next_attempt_at", "lease_until", "chunks_total", "chunks_embedded",
        "last_error", "started_at", "finished_at", "updated_at",
    )}


def retry_failed_jobs(sessions):
    with sessions() as session:
        jobs = list(session.execute(select(
            db.RagIndexJob.id, db.RagIndexJob.asset_kind, db.RagIndexJob.asset_id, db.RagIndexJob.generation,
        ).where(db.RagIndexJob.status == "failed").order_by(db.RagIndexJob.id)))
    count = 0
    for identity, kind, asset_id, generation in jobs:
        with sessions.begin() as session:
            asset = live_asset(session, kind, asset_id, library_only=False, lock=True)
            job = session.get(db.RagIndexJob, identity, with_for_update=True)
            if not asset or not job or job.status != "failed" or job.generation != generation:
                continue
            enqueue_asset_index(session, asset_kind=kind, asset_id=asset_id, version_id=asset[1].id,
                                force_enqueue=True)
            count += 1
    return count


class RagIndexPublisher:
    def __init__(self, sessions, queue):
        self.sessions, self.queue = sessions, queue

    def publish_due(self, limit=10):
        sent, now = 0, db.utcnow()
        job = db.RagIndexJob
        with self.sessions.begin() as session:
            rows = session.scalars(select(job).where(
                job.status == "pending", job.next_attempt_at <= now,
                or_(job.lease_until.is_(None), job.lease_until <= now),
            ).order_by(job.next_attempt_at, job.id).limit(limit).with_for_update(skip_locked=True))
            for row in rows:
                try:
                    self.queue.send_task(RAG_INDEX_TASK, args=[row.id, row.generation],
                                         task_id=f"rag:{row.id}:{row.generation}")
                except Exception as exc:
                    # 用上一次投递延迟计算退避，消费次数只由真正领取的 Worker 递增。
                    previous = max(0, (row.next_attempt_at - row.updated_at).total_seconds())
                    row.next_attempt_at = now + timedelta(seconds=min(60, max(5, previous * 2)))
                    row.updated_at = now
                    row.last_error = type(exc).__name__[:300]
                else:
                    row.lease_until = now + timedelta(seconds=30)
                    sent += 1
        return sent


def record_rebuilt_asset(session, *, kind, identity, version, profile_id, chunks, started):
    now = db.utcnow()
    values = dict(
        version_id=version, profile_id=profile_id, status="ready", attempts=0,
        chunks_total=chunks, chunks_embedded=chunks, last_error=None, lease_until=None,
        started_at=started, finished_at=now, updated_at=now, next_attempt_at=now,
    )
    statement = insert(db.RagIndexJob).values(asset_kind=kind, asset_id=identity, **values)
    session.execute(statement.on_conflict_do_update(
        constraint="uq_rag_index_jobs_asset",
        set_={**values, "generation": db.RagIndexJob.generation + 1},
        where=(db.RagIndexJob.version_id == version) & (db.RagIndexJob.updated_at <= started),
    ))


def recover_interrupted_rag_jobs(sessions):
    with sessions.begin() as session:
        now = db.utcnow()
        result = session.execute(update(db.RagIndexJob).where(db.RagIndexJob.status == "indexing").values(
            status="failed", lease_until=None, finished_at=now, updated_at=now,
            last_error="工作进程重启中断，请手动点击重试或补建向量索引",
        ))
        return result.rowcount


def _locked_job(session, identity, generation, status, version_id=None):
    job = session.get(db.RagIndexJob, identity, with_for_update=True)
    if job is None or job.generation != generation or job.status != status:
        return None
    if version_id is not None and job.version_id != version_id:
        return None
    return job


def _finished(job, status, error=None):
    job.status, job.last_error = status, error
    job.finished_at = job.updated_at = db.utcnow()
    job.lease_until = None


class _InvalidEmbedding(EmbeddingError):
    """重试不能修复维度或向量格式问题。"""


def _embed(provider, texts, dimensions=None):
    vectors = []
    for start in range(0, len(texts), 32):
        batch = texts[start:start + 32]
        values = provider.embed_documents(batch)
        if len(values) != len(batch):
            raise _InvalidEmbedding("嵌入服务返回的向量数量不匹配")
        for value in values:
            if not value or (dimensions is not None and len(value) != dimensions):
                raise _InvalidEmbedding("嵌入向量维度不匹配")
            try:
                db.format_pgvector(value)
            except ValueError as exc:
                raise _InvalidEmbedding("嵌入服务返回了无效向量") from exc
        vectors.extend(values)
    return vectors


def _transient_error(exc):
    if isinstance(exc, _InvalidEmbedding):
        return False
    cause = exc.__cause__ or exc
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code in {408, 425, 429} or cause.response.status_code >= 500
    # 供应商的配置、格式和维度异常不会因自动重试而恢复。原始错误只用于分类，不保存。
    return not any(word in str(exc).lower() for word in (
        "配置", "维度", "格式", "数量", "非法", "空向量", "未返回", "dimension", "configuration",
    ))


def index_asset(sessions, provider, identity, generation):
    with sessions.begin() as session:
        job = _locked_job(session, identity, generation, "pending")
        now = db.utcnow()
        if job is None or job.next_attempt_at > now:
            return None
        if not provider.enabled:
            _finished(job, "failed", "嵌入服务未启用或受 MODEL_CALLS_ENABLED 保护，请启用后手动重试")
            return job_view(job)
        kind, asset_id, version_id = job.asset_kind, job.asset_id, job.version_id
        job.status, job.attempts = "indexing", job.attempts + 1
        job.started_at = job.updated_at = now
        job.lease_until = now + timedelta(seconds=120)
        job.last_error = None

    model = db.KnowledgeChunkEmbedding if kind == "knowledge" else db.SourceChunkEmbedding
    spec = provider.spec
    try:
        with sessions() as session:
            target = live_asset(session, kind, asset_id, library_only=False)
            if target and target[1].id == version_id:
                asset, version = target
                settings = session.get(db.RagSettings, "default")
                contextual = settings.contextual_embedding_enabled if settings else True
                entries = []
                for row in session.execute(chunk_query(kind, version_id)):
                    _, text, _ = chunk_text(kind, asset, version, row, contextual=contextual)
                    digest = embedding_content_hash(text, document_prefix=spec.document_prefix,
                                                    contextual=contextual, normalize=spec.normalize)
                    entries.append((chunk_identity(kind, row[0]), text, digest))
            else:
                entries = None

        if entries is None:
            with sessions.begin() as session:
                job = _locked_job(session, identity, generation, "indexing", version_id)
                if job:
                    session.delete(job)
            return None

        dimensions = spec.dimensions
        probe = None
        if dimensions is None:
            probe = _embed(provider, [entries[0][1] if entries else "probe"])[0]
            dimensions = len(probe)
        profile_id = spec.profile_id(dimensions)
        with sessions() as session:
            pending = []
            for keys, text, digest in entries:
                existing = session.get(model, {**keys, "profile_id": profile_id})
                if existing is None or existing.content_sha256 != digest or (
                    kind == "knowledge" and existing.version_id != version_id
                ):
                    pending.append((keys, text, digest))

        vectors = []
        if probe is not None and pending and pending[0][0] == entries[0][0]:
            vectors = [probe]
        vectors += _embed(provider, [entry[1] for entry in pending[len(vectors):]], dimensions)

        with sessions.begin() as session:
            lock_control(session)
            # 发布与单篇索引都先锁资产再锁任务，避免新版本与旧写回形成锁顺序环。
            target = live_asset(session, kind, asset_id, library_only=False, lock=True)
            job = _locked_job(session, identity, generation, "indexing", version_id)
            if job is None:
                return None
            if not target or target[1].id != version_id:
                session.delete(job)
                return None
            if [chunk_identity(kind, row[0]) for row in session.execute(chunk_query(kind, version_id))] != [
                keys for keys, _, _ in entries
            ]:
                raise EmbeddingError("本次生成期间分块索引已变化")
            now = db.utcnow()
            metadata = dict(query_prefix=spec.query_prefix, document_prefix=spec.document_prefix,
                            normalize=spec.normalize, contextual=contextual, status="ready", updated_at=now)
            profile = insert(db.EmbeddingProfile).values(
                profile_id=profile_id, provider=spec.provider, model=spec.model, dimensions=dimensions, **metadata,
            )
            # 档案删除和同步补建都先锁档案，再锁设置；异步写回沿用相同顺序。
            session.execute(profile.on_conflict_do_update(index_elements=["profile_id"], set_=metadata))
            settings = session.get(db.RagSettings, "default", with_for_update=True)
            if settings is None:
                session.execute(insert(db.RagSettings).values(id="default", updated_at=now).on_conflict_do_nothing())
                settings = session.get(db.RagSettings, "default", with_for_update=True)
            if settings.contextual_embedding_enabled != contextual:
                raise EmbeddingError("嵌入选项在处理期间变化，请重试")
            for (keys, _, digest), vector in zip(pending, vectors, strict=True):
                values = dict(embedding=vector, dimensions=dimensions, content_sha256=digest, updated_at=now)
                if kind == "knowledge":
                    values["version_id"] = version_id
                session.execute(insert(model).values(**keys, profile_id=profile_id, **values)
                                .on_conflict_do_update(index_elements=[*keys, "profile_id"], set_=values))
            if settings.active_profile_id is None:
                settings.active_profile_id = profile_id
                settings.revision += 1
                settings.updated_at = now
            _finished(job, "ready")
            job.profile_id = profile_id
            job.chunks_total = job.chunks_embedded = len(entries)
            return job_view(job)
    except (EmbeddingError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
        with sessions.begin() as session:
            job = _locked_job(session, identity, generation, "indexing", version_id)
            if job is None:
                return None
            transient = _transient_error(exc)
            logger.warning("RAG indexing failed (%s)", type(exc).__name__)
            error = "嵌入请求暂时失败" if transient else "嵌入服务配置、响应格式或维度不匹配"
            _finished(job, "failed", error)
            if transient and job.attempts < job.max_attempts:
                job.status, job.finished_at = "pending", None
                job.next_attempt_at = job.updated_at + timedelta(seconds=min(60, 5 * 2 ** (job.attempts - 1)))
            return job_view(job)
