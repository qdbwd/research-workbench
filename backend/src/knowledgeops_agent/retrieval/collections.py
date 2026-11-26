"""逻辑分组、全局资产资格和原文分块视图。"""

from collections import defaultdict
from typing import Literal

from sqlalchemy import and_, case, delete, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert

from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.persistence import models as db
from knowledgeops_agent.retrieval.embeddings import build_contextual_text, embedding_content_hash
from knowledgeops_agent.sources.ownership import library_predicate

AssetKind = Literal["knowledge", "source"]
IndexStatus = Literal["pending", "indexing", "ready", "failed", "partial", "unindexed"]


class RagConflict(ValueError):
    """工作台编辑基于过期版本，或名称已存在。"""


def live_asset_query(kind: AssetKind, *, library_only: bool = True):
    """沿用知识当前指针和材料最新版本规则，私密材料资格单独核验。"""
    if kind == "knowledge":
        asset, version = db.KnowledgeDocument, db.KnowledgeVersion
        statement = select(asset, version).join(version, version.id == asset.current_version_id).where(
            version.archived.is_(False),
        )
    else:
        asset, version = db.Source, db.SourceVersion
        number = select(func.max(version.number)).where(
            version.source_id == asset.id,
        ).correlate(asset).scalar_subquery()
        statement = select(asset, version).join(version, version.source_id == asset.id).where(
            version.number == number, version.status == "ready",
        )
        if library_only:
            statement = statement.where(library_predicate())
    return statement.where(asset.deleted_at.is_(None), asset.trial_conversation_id.is_(None))


def live_asset(session, kind: AssetKind, identity: str, *, library_only: bool = True, lock: bool = False):
    model = db.KnowledgeDocument if kind == "knowledge" else db.Source
    if lock:
        # 先锁资产再读取当前版本。等待锁期间可能刚发布新版本。
        session.execute(select(model.id).where(model.id == identity).with_for_update())
    return session.execute(live_asset_query(kind, library_only=library_only).where(model.id == identity)).one_or_none()


def chunk_query(kind: AssetKind, version_id: str):
    if kind == "knowledge":
        chunk = db.KnowledgeSearchChunk
        return select(chunk).where(chunk.version_id == version_id).order_by(chunk.chunk_index)
    chunk, segment = db.SourceSearchChunk, db.SourceSegment
    return select(chunk, segment).join(segment, segment.id == chunk.segment_id).where(
        segment.source_version_id == version_id, segment.status == "ready",
    ).order_by(segment.position, chunk.chunk_index)


def chunk_identity(kind: AssetKind, chunk) -> dict:
    key = "document_id" if kind == "knowledge" else "segment_id"
    return {key: getattr(chunk, key), "chunk_index": chunk.chunk_index}


def chunk_text(kind: AssetKind, asset, version, row, *, contextual: bool) -> tuple[str, str, int]:
    chunk = row[0]
    if kind == "knowledge":
        raw = version.body[chunk.start:chunk.end]
        text = build_contextual_text(raw, title=version.title, summary=version.summary,
                                     tags=version.tags, category=version.category, contextual=contextual)
        return raw, text, 0
    segment = row[1]
    raw = segment.text[chunk.start:chunk.end]
    text = build_contextual_text(raw, title=asset.title, page=segment.page, contextual=contextual)
    return raw, text, segment.start_offset


def memberships(session, keys, *, collection_ids=None) -> dict:
    result = defaultdict(list)
    if not keys:
        return result
    item, collection = db.RagCollectionItem, db.RagCollection
    from sqlalchemy import tuple_

    statement = select(item, collection).join(collection, collection.id == item.collection_id).where(
        tuple_(item.asset_kind, item.asset_id).in_(keys),
    )
    if collection_ids is not None:
        statement = statement.where(collection.id.in_(collection_ids))
    for member, group in session.execute(statement.order_by(collection.name, collection.id)):
        result[(member.asset_kind, member.asset_id)].append({"id": group.id, "name": group.name})
    return result


def collection_scope(session, collection_ids: list[str]) -> tuple[list[str], list[str]]:
    item = db.RagCollectionItem
    ids = []
    for kind, model in (("knowledge", db.KnowledgeDocument), ("source", db.Source)):
        eligible = live_asset_query(kind).with_only_columns(model.id)
        ids.append(list(session.scalars(select(item.asset_id).where(
            item.collection_id.in_(collection_ids), item.asset_kind == kind, item.asset_id.in_(eligible),
        ).distinct())))
    return ids[0], ids[1]


def mounted_reference_scope(session, inputs: dict) -> tuple[list[str] | None, list[str] | None]:
    collection_ids = inputs.get("collection_ids") or []
    explicit_sources = inputs.get("source_ids") or None
    if inputs.get("rag_scope") != "collections" and not collection_ids:
        return None, explicit_sources
    documents, sources = collection_scope(session, collection_ids)
    return documents, sorted(set(sources).union(explicit_sources or []))


def collection_names(session, collection_ids: list[str]) -> list[str]:
    return list(session.scalars(select(db.RagCollection.name).where(
        db.RagCollection.id.in_(collection_ids),
    ).order_by(db.RagCollection.name)))


def _asset_rows(profile_id):
    queries = []
    for kind, asset, version, chunk, embedding in (
        ("knowledge", db.KnowledgeDocument, db.KnowledgeVersion, db.KnowledgeSearchChunk, db.KnowledgeChunkEmbedding),
        ("source", db.Source, db.SourceVersion, db.SourceSearchChunk, db.SourceChunkEmbedding),
    ):
        if kind == "knowledge":
            title, subtitle, updated = version.title, version.category, version.created_at
            chunk_side = live_asset_query(kind).outerjoin(chunk, chunk.version_id == version.id)
            same = (embedding.document_id == asset.id) & (embedding.version_id == version.id)
        else:
            title, subtitle, updated = asset.title, asset.kind, version.created_at
            segment = db.SourceSegment
            chunk_side = live_asset_query(kind).outerjoin(segment, and_(
                segment.source_version_id == version.id, segment.status == "ready",
            )).outerjoin(chunk, chunk.segment_id == segment.id)
            same = embedding.segment_id == segment.id
        queries.append(chunk_side.outerjoin(embedding, and_(
            same, embedding.chunk_index == chunk.chunk_index, embedding.profile_id == profile_id,
        )).with_only_columns(
            literal(kind).label("asset_kind"), asset.id.label("asset_id"), version.id.label("version_id"),
            version.number.label("version"), title.label("title"), subtitle.label("subtitle"),
            func.count(chunk.chunk_index).label("chunks_total"),
            func.count(chunk.chunk_index).filter(chunk.enabled.is_(True)).label("chunks_enabled"),
            func.count(embedding.chunk_index).label("chunks_embedded"), updated.label("updated_at"),
        ).group_by(asset.id, version.id, title, subtitle, updated))
    assets = union_all(*queries).subquery()
    job = db.RagIndexJob
    index_status = case(
        (job.status.in_(("pending", "indexing", "failed")), job.status),
        (assets.c.chunks_embedded == 0, "unindexed"),
        (assets.c.chunks_embedded >= assets.c.chunks_total, "ready"),
        else_="partial",
    )
    return select(
        assets.c.asset_kind, assets.c.asset_id, assets.c.version_id, assets.c.version,
        assets.c.title, assets.c.subtitle, assets.c.chunks_total, assets.c.chunks_enabled,
        assets.c.chunks_embedded, func.coalesce(job.updated_at, assets.c.updated_at).label("updated_at"),
        index_status.label("index_status"), job.last_error,
    ).outerjoin(job, and_(
        job.asset_kind == assets.c.asset_kind, job.asset_id == assets.c.asset_id,
        job.version_id == assets.c.version_id,
    )).subquery()


def _profile(session):
    settings = session.get(db.RagSettings, "default")
    profile = session.get(db.EmbeddingProfile, settings.active_profile_id) if settings and settings.active_profile_id else None
    return settings, profile


def _view_rows(session, rows) -> list[dict]:
    groups = memberships(session, [(row.asset_kind, row.asset_id) for row in rows])
    return [{
        **row._mapping,
        "category" if row.asset_kind == "knowledge" else "source_kind": row.subtitle,
        "collections": groups[(row.asset_kind, row.asset_id)],
    } for row in rows]


class RagCollections:
    def __init__(self, sessions):
        self.sessions = sessions

    def assets(self, *, collection_id=None, asset_kind=None, status=None, query="", offset=0, limit=30):
        with self.sessions() as session:
            _, profile = _profile(session)
            rows = _asset_rows(profile.profile_id if profile and profile.status == "ready" else None)
            statement = select(rows)
            if collection_id is not None:
                self._collection(session, collection_id)
                item = db.RagCollectionItem
                statement = statement.where(select(item.asset_id).where(
                    item.collection_id == collection_id, item.asset_kind == rows.c.asset_kind,
                    item.asset_id == rows.c.asset_id,
                ).exists())
            if asset_kind is not None:
                statement = statement.where(rows.c.asset_kind == asset_kind)
            if status is not None:
                statement = statement.where(rows.c.index_status == status)
            if query.strip():
                statement = statement.where(or_(rows.c.title.icontains(query.strip(), autoescape=True),
                                                rows.c.subtitle.icontains(query.strip(), autoescape=True)))
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            identity_order = (rows.c.asset_kind, rows.c.asset_id)
            # 成员对话框分页读完整集合，作业交换状态时不能把成员挪到另一页。
            order = identity_order if collection_id else (rows.c.updated_at.desc(), *identity_order)
            page = list(session.execute(statement.order_by(*order).offset(offset).limit(limit)))
            return {"items": _view_rows(session, page), "total": total}

    @staticmethod
    def _collection(session, identity, expected_revision=None):
        group = session.get(db.RagCollection, identity, with_for_update=expected_revision is not None)
        if group is None:
            raise KeyError("未找到知识集")
        if expected_revision is not None and group.revision != expected_revision:
            raise RagConflict("知识集已更新，请刷新后重试")
        return group

    @staticmethod
    def _views(session, groups):
        if not groups:
            return []
        _, profile = _profile(session)
        rows = _asset_rows(profile.profile_id if profile and profile.status == "ready" else None)
        item = db.RagCollectionItem
        totals = {
            row.collection_id: row._mapping for row in session.execute(select(
                item.collection_id, func.count().label("asset_count"),
                func.count().filter(item.asset_kind == "knowledge").label("knowledge_count"),
                func.count().filter(item.asset_kind == "source").label("source_count"),
                func.sum(rows.c.chunks_total).label("chunks_total"),
                func.sum(rows.c.chunks_embedded).label("chunks_embedded"),
            ).join(rows, and_(rows.c.asset_kind == item.asset_kind, rows.c.asset_id == item.asset_id))
              .where(item.collection_id.in_([group.id for group in groups])).group_by(item.collection_id))
        }
        return [{
            "id": group.id, "name": group.name, "description": group.description,
            "revision": group.revision, "updated_at": group.updated_at,
            **{key: totals.get(group.id, {}).get(key, 0) for key in (
                "asset_count", "knowledge_count", "source_count", "chunks_total", "chunks_embedded",
            )},
        } for group in groups]

    def list(self):
        with self.sessions() as session:
            groups = list(session.scalars(select(db.RagCollection).order_by(db.RagCollection.name)))
            return self._views(session, groups)

    def create(self, name, description):
        with self.sessions.begin() as session:
            identity = session.scalar(insert(db.RagCollection).values(
                name=name, description=description,
            ).on_conflict_do_nothing(index_elements=["name"]).returning(db.RagCollection.id))
            if identity is None:
                raise RagConflict("知识集名称已存在")
            return self._views(session, [session.get(db.RagCollection, identity)])[0]

    def update(self, identity, expected_revision, *, name=None, description=None):
        from sqlalchemy.exc import IntegrityError

        try:
            with self.sessions.begin() as session:
                group = self._collection(session, identity, expected_revision)
                if name is not None:
                    group.name = name
                if description is not None:
                    group.description = description
                group.revision += 1
                group.updated_at = db.utcnow()
                session.flush()
                return self._views(session, [group])[0]
        except IntegrityError as exc:
            raise RagConflict("知识集名称已存在") from exc

    def delete(self, identity, expected_revision):
        with self.sessions.begin() as session:
            session.delete(self._collection(session, identity, expected_revision))
        return {"id": identity}

    def replace(self, identity, expected_revision, items):
        with self.sessions.begin() as session:
            # 材料库释放和知识撤回使用同一控制锁，避免读资格后又插入失效成员。
            lock_control(session)
            group = self._collection(session, identity, expected_revision)
            keys = {(row.asset_kind, row.asset_id) for row in items}
            for kind, model in (("knowledge", db.KnowledgeDocument), ("source", db.Source)):
                ids = {asset_id for asset_kind, asset_id in keys if asset_kind == kind}
                if ids:
                    allowed = set(session.scalars(live_asset_query(kind).with_only_columns(model.id).where(
                        model.id.in_(ids),
                    )))
                    if ids != allowed:
                        raise PermissionError("仅可加入已发布知识和已保存到材料库的当前材料")
            session.execute(delete(db.RagCollectionItem).where(db.RagCollectionItem.collection_id == identity))
            if keys:
                session.execute(insert(db.RagCollectionItem), [
                    {"collection_id": identity, "asset_kind": kind, "asset_id": asset_id}
                    for kind, asset_id in sorted(keys)
                ])
            group.revision += 1
            group.updated_at = db.utcnow()
            session.flush()
            return self._views(session, [group])[0]

    def chunks(self, kind, identity, *, offset=0, limit=30, query="", enabled=None):
        with self.sessions() as session:
            target = live_asset(session, kind, identity)
            if target is None:
                raise KeyError("未找到可索引的全局资产")
            asset, version = target
            settings, profile = _profile(session)
            contextual = settings.contextual_embedding_enabled if settings else True
            rows = _asset_rows(profile.profile_id if profile and profile.status == "ready" else None)
            asset_rows = list(session.execute(select(rows).where(
                rows.c.asset_kind == kind, rows.c.asset_id == identity, rows.c.version_id == version.id,
            )))
            if not asset_rows:
                raise RagConflict("正文版本已更新，请刷新后重试")
            asset_view = _view_rows(session, asset_rows)[0]
            items, total, enabled_count = [], 0, 0
            for row in session.execute(chunk_query(kind, version.id)):
                chunk = row[0]
                enabled_count += int(chunk.enabled)
                if enabled is not None and chunk.enabled != enabled:
                    continue
                raw, enriched, base = chunk_text(kind, asset, version, row, contextual=contextual)
                if query.strip().casefold() not in raw.casefold():
                    continue
                total += 1
                if not offset < total <= offset + limit:
                    continue
                embedding = None
                if profile:
                    model = db.KnowledgeChunkEmbedding if kind == "knowledge" else db.SourceChunkEmbedding
                    embedding = session.get(model, {**chunk_identity(kind, chunk), "profile_id": profile.profile_id})
                ready = embedding and profile.status == "ready" and embedding.content_sha256 == embedding_content_hash(
                    enriched, document_prefix=profile.document_prefix, contextual=contextual, normalize=profile.normalize,
                )
                if kind == "knowledge" and embedding and embedding.version_id != version.id:
                    ready = False
                items.append({
                    "chunk_index": chunk.chunk_index, "segment_id": getattr(chunk, "segment_id", None),
                    "page": row[1].page if kind == "source" else None,
                    "start": base + chunk.start, "end": base + chunk.end, "char_count": len(raw), "text": raw,
                    "contextual_header": enriched.removesuffix(raw).rstrip() or None,
                    "enabled": chunk.enabled, "vector_status": "ready" if ready else "stale" if embedding else "missing",
                    "dimensions": embedding.dimensions if embedding else None,
                    "updated_at": embedding.updated_at if embedding else None,
                })
            return {
                "asset": asset_view, "version_id": version.id, "profile_id": profile.profile_id if profile else None,
                "contextual_enabled": contextual, "total": total, "enabled_count": enabled_count,
                "disabled_count": asset_view["chunks_total"] - enabled_count, "items": items,
            }

    def toggle(self, kind, identity, *, version_id, chunk_index, segment_id, enabled):
        with self.sessions.begin() as session:
            lock_control(session)
            target = live_asset(session, kind, identity, lock=True)
            if target is None:
                raise KeyError("未找到可索引的全局资产")
            if target[1].id != version_id:
                raise RagConflict("正文版本已更新，请刷新后重试")
            model = db.KnowledgeSearchChunk if kind == "knowledge" else db.SourceSearchChunk
            statement = chunk_query(kind, version_id)
            chunk = session.scalars(statement.where(
                model.chunk_index == chunk_index,
                segment_id is None if kind == "knowledge" else model.segment_id == segment_id,
            )).first()
            if chunk is None:
                raise KeyError("未找到该分块")
            chunk.enabled = enabled
            counts = [row.enabled for row in session.scalars(statement)]
            return {
                "version_id": version_id, "chunk_index": chunk_index, "segment_id": segment_id,
                "enabled": enabled, "enabled_count": sum(counts), "disabled_count": len(counts) - sum(counts),
            }
