"""可插拔嵌入（Embedding）与可选重排（Reranker）提供方。

支持 disabled（默认，纯 BM25）、本机/内网 HuggingFace TEI 以及 OpenAI 兼容 HTTP 端点。
收费云端端点受 MODEL_CALLS_ENABLED 保护；所有异常由调用方捕获并平滑降级，不阻塞主链路。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from knowledgeops_agent.core.config import Settings


class EmbeddingError(RuntimeError):
    """嵌入生成或配置校验失败。"""


class RerankerError(RuntimeError):
    """可选重排调用失败。"""


@dataclass(frozen=True)
class EmbeddingProfileSpec:
    """不可变嵌入配置摘要，用于隔离不同模型/维度的向量空间。"""

    provider: str
    model: str
    dimensions: int | None
    query_prefix: str = ""
    document_prefix: str = ""
    normalize: bool = True

    def profile_id(self, resolved_dimensions: int | None = None) -> str:
        dims = resolved_dimensions or self.dimensions or 0
        model_slug = (self.model.strip() or "default").replace(" ", "_")
        base = f"{self.provider}:{model_slug}:{dims}"
        return base[:160]


class EmbeddingProvider(Protocol):
    """统一嵌入接口：查询与文档分开，支持非对称指令前缀与批量生成。"""

    @property
    def enabled(self) -> bool: ...

    @property
    def spec(self) -> EmbeddingProfileSpec: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...

    def describe(self) -> dict: ...


class RerankerProvider(Protocol):
    """统一可选重排接口：输入查询与候选文本，返回按相关性降序的 (index, score)。"""

    @property
    def enabled(self) -> bool: ...

    def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None) -> list[tuple[int, float]]: ...

    def describe(self) -> dict: ...


def build_contextual_text(
    chunk_text: str,
    *,
    title: str = "",
    summary: str = "",
    tags: Sequence[str] = (),
    category: str = "",
    page: int | None = None,
    contextual: bool = True,
) -> str:
    """上下文增强嵌入文本：仅用于算向量，不改写原文也不改变 start/end 偏移。"""
    if not contextual:
        return chunk_text
    header_parts: list[str] = []
    if title.strip():
        header_parts.append(f"标题：{title.strip()}")
    if category.strip():
        header_parts.append(f"分类：{category.strip()}")
    clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    if clean_tags:
        header_parts.append(f"标签：{'、'.join(clean_tags)}")
    if summary.strip():
        header_parts.append(f"摘要：{summary.strip()}")
    if page is not None:
        header_parts.append(f"页码：第 {page} 页")
    if not header_parts:
        return chunk_text
    return "\n".join(header_parts) + "\n\n" + chunk_text


def embedding_content_hash(
    text: str,
    *,
    document_prefix: str = "",
    contextual: bool = True,
    normalize: bool = True,
) -> str:
    """计算片段嵌入输入指纹，供增量重建跳过未变更片段。"""
    payload = f"ctx={int(contextual)};norm={int(normalize)}\n{document_prefix}{text}".encode()
    return hashlib.sha256(payload).hexdigest()


def _validate_and_normalize(
    vectors: Sequence[Sequence[float]],
    *,
    expected_dimensions: int | None = None,
    normalize: bool = True,
) -> list[list[float]]:
    result: list[list[float]] = []
    inferred_dim = expected_dimensions
    for raw in vectors:
        vec = [float(x) for x in raw]
        if not vec:
            raise EmbeddingError("嵌入服务返回了空向量")
        if any(not math.isfinite(x) for x in vec):
            raise EmbeddingError("嵌入服务返回了非法浮点值（NaN/Inf）")
        if inferred_dim is None:
            inferred_dim = len(vec)
        elif len(vec) != inferred_dim:
            raise EmbeddingError(f"嵌入向量维度不一致：期望 {inferred_dim}，实际 {len(vec)}")
        if normalize:
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0:
                vec = [x / norm for x in vec]
        result.append(vec)
    return result


def _safe_endpoint_summary(url: str) -> str:
    """只暴露协议与主机端口供 UI 展示，严格剥离内嵌凭据、路径与查询参数。"""
    if not url.strip():
        return ""
    parsed = urlsplit(url.strip())
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return ""
    return f"{parsed.scheme}://{host}:{port}" if port is not None else f"{parsed.scheme}://{host}"


class DisabledEmbeddingProvider:
    """默认关闭态：零外部依赖，所有请求直接拒绝。"""

    def __init__(self):
        self._spec = EmbeddingProfileSpec(provider="disabled", model="", dimensions=None)

    @property
    def enabled(self) -> bool:
        return False

    @property
    def spec(self) -> EmbeddingProfileSpec:
        return self._spec

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("嵌入服务未配置（EMBEDDING_PROVIDER=disabled）")

    def embed_query(self, query: str) -> list[float]:
        raise EmbeddingError("嵌入服务未配置（EMBEDDING_PROVIDER=disabled）")

    def describe(self) -> dict:
        return {
            "provider": "disabled",
            "configured": False,
            "model": "",
            "dimensions": None,
            "endpoint": "",
            "normalize": True,
            "query_prefix": "",
        }


class TEIEmbeddingProvider:
    """本机/内网 HuggingFace Text Embeddings Inference (TEI) 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimensions: int | None = None,
        query_prefix: str = "",
        document_prefix: str = "",
        normalize: bool = True,
        batch_size: int = 16,
        timeout_seconds: float = 10.0,
        api_key: str = "",
    ):
        self._base_url = base_url.rstrip("/")
        self._batch_size = max(1, int(batch_size))
        self._timeout = float(timeout_seconds)
        self._api_key = api_key.strip()
        self._spec = EmbeddingProfileSpec(
            provider="tei",
            model=model.strip() or "tei-default",
            dimensions=dimensions,
            query_prefix=query_prefix,
            document_prefix=document_prefix,
            normalize=normalize,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    @property
    def spec(self) -> EmbeddingProfileSpec:
        return self._spec

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    def _post_embed(self, inputs: list[str]) -> list[list[float]]:
        if not self._base_url:
            raise EmbeddingError("未配置 EMBEDDING_BASE_URL")
        if not inputs:
            return []
        url = f"{self._base_url}/embed"
        vectors: list[list[float]] = []
        try:
            with httpx.Client(timeout=self._timeout) as client:
                for start in range(0, len(inputs), self._batch_size):
                    batch = inputs[start : start + self._batch_size]
                    response = client.post(
                        url,
                        json={"inputs": batch, "normalize": self._spec.normalize, "truncate": True},
                        headers=self._headers(),
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if isinstance(payload, list) and payload and isinstance(payload[0], (int, float)):
                        batch_vectors = [payload]
                    elif isinstance(payload, list):
                        batch_vectors = payload
                    else:
                        raise EmbeddingError("TEI /embed 返回格式异常")
                    if len(batch_vectors) != len(batch):
                        raise EmbeddingError("TEI /embed 返回数量与请求批次不匹配")
                    vectors.extend(batch_vectors)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"TEI 嵌入请求失败: {type(exc).__name__}") from exc
        return _validate_and_normalize(
            vectors,
            expected_dimensions=self._spec.dimensions,
            normalize=self._spec.normalize,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        prefixed = [f"{self._spec.document_prefix}{text}" for text in texts]
        return self._post_embed(prefixed)

    def embed_query(self, query: str) -> list[float]:
        vectors = self._post_embed([f"{self._spec.query_prefix}{query}"])
        if not vectors:
            raise EmbeddingError("TEI 未返回查询向量")
        return vectors[0]

    def describe(self) -> dict:
        return {
            "provider": "tei",
            "configured": self.enabled,
            "model": self._spec.model,
            "dimensions": self._spec.dimensions,
            "endpoint": _safe_endpoint_summary(self._base_url),
            "normalize": self._spec.normalize,
            "query_prefix": self._spec.query_prefix,
        }


class OpenAICompatibleEmbeddingProvider:
    """OpenAI 兼容 /embeddings 客户端，严格受 MODEL_CALLS_ENABLED 保护。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        model_calls_enabled: bool,
        dimensions: int | None = None,
        query_prefix: str = "",
        document_prefix: str = "",
        normalize: bool = True,
        batch_size: int = 16,
        timeout_seconds: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._model_calls_enabled = bool(model_calls_enabled)
        self._batch_size = max(1, int(batch_size))
        self._timeout = float(timeout_seconds)
        self._spec = EmbeddingProfileSpec(
            provider="openai_compatible",
            model=model.strip(),
            dimensions=dimensions,
            query_prefix=query_prefix,
            document_prefix=document_prefix,
            normalize=normalize,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self._spec.model and self._model_calls_enabled)

    @property
    def spec(self) -> EmbeddingProfileSpec:
        return self._spec

    def _post_embeddings(self, inputs: list[str]) -> list[list[float]]:
        if not self._model_calls_enabled:
            raise EmbeddingError("外部模型调用已关闭（MODEL_CALLS_ENABLED=false），拒绝调用云端嵌入接口")
        if not self._base_url or not self._spec.model:
            raise EmbeddingError("未完整配置 EMBEDDING_BASE_URL 与 EMBEDDING_MODEL")
        if not inputs:
            return []
        url = self._base_url if self._base_url.endswith("/embeddings") else f"{self._base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        vectors: list[list[float]] = []
        try:
            with httpx.Client(timeout=self._timeout) as client:
                for start in range(0, len(inputs), self._batch_size):
                    batch = inputs[start : start + self._batch_size]
                    body: dict = {"model": self._spec.model, "input": batch}
                    if self._spec.dimensions is not None:
                        body["dimensions"] = self._spec.dimensions
                    response = client.post(url, json=body, headers=headers)
                    response.raise_for_status()
                    data = (response.json() or {}).get("data")
                    if not isinstance(data, list) or len(data) != len(batch):
                        raise EmbeddingError("OpenAI 兼容嵌入返回数量异常")
                    ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
                    vectors.extend(item["embedding"] for item in ordered)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"云端嵌入请求失败: {type(exc).__name__}") from exc
        return _validate_and_normalize(
            vectors,
            expected_dimensions=self._spec.dimensions,
            normalize=self._spec.normalize,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._post_embeddings([f"{self._spec.document_prefix}{text}" for text in texts])

    def embed_query(self, query: str) -> list[float]:
        vectors = self._post_embeddings([f"{self._spec.query_prefix}{query}"])
        if not vectors:
            raise EmbeddingError("未返回查询向量")
        return vectors[0]

    def describe(self) -> dict:
        return {
            "provider": "openai_compatible",
            "configured": bool(self._base_url and self._spec.model),
            "gated_by_model_calls": not self._model_calls_enabled,
            "model": self._spec.model,
            "dimensions": self._spec.dimensions,
            "endpoint": _safe_endpoint_summary(self._base_url),
            "normalize": self._spec.normalize,
            "query_prefix": self._spec.query_prefix,
        }


class DisabledRerankerProvider:
    """默认关闭态重排器。"""

    @property
    def enabled(self) -> bool:
        return False

    def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None) -> list[tuple[int, float]]:
        raise RerankerError("重排服务未配置（RERANKER_PROVIDER=disabled）")

    def describe(self) -> dict:
        return {"provider": "disabled", "configured": False, "model": "", "endpoint": ""}


class TEIRerankerProvider:
    """HuggingFace TEI /rerank 可选重排客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "",
        timeout_seconds: float = 10.0,
        api_key: str = "",
        max_batch_size: int = 16,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model.strip() or "tei-reranker"
        self._timeout = float(timeout_seconds)
        self._api_key = api_key.strip()
        self._max_batch_size = max(1, int(max_batch_size))

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None) -> list[tuple[int, float]]:
        if not self._base_url:
            raise RerankerError("未配置 RERANKER_BASE_URL")
        if not documents:
            return []
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        docs = list(documents)
        ranked: list[tuple[int, float]] = []
        with httpx.Client(timeout=self._timeout) as client:
            # 按客户端批上限分段调用 /rerank 再合并全局排序，避免候选数大于 TEI max-client-batch-size 时触发 422。
            for offset in range(0, len(docs), self._max_batch_size):
                batch = docs[offset : offset + self._max_batch_size]
                try:
                    response = client.post(
                        f"{self._base_url}/rerank",
                        json={"query": query, "texts": batch, "truncate": True},
                        headers=headers,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    raise RerankerError(f"TEI 重排请求失败: {type(exc).__name__}") from exc
                if not isinstance(payload, list):
                    raise RerankerError("TEI /rerank 返回格式异常")
                try:
                    for item in payload:
                        if not isinstance(item, dict):
                            continue
                        local_idx = int(item.get("index", -1))
                        score = float(item["score"])
                        if 0 <= local_idx < len(batch) and math.isfinite(score):
                            ranked.append((offset + local_idx, score))
                except (KeyError, TypeError, ValueError) as exc:
                    raise RerankerError(f"TEI /rerank 响应项格式异常: {type(exc).__name__}") from exc
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        return ranked[:top_n] if top_n is not None else ranked

    def describe(self) -> dict:
        return {
            "provider": "tei",
            "configured": self.enabled,
            "model": self._model,
            "endpoint": _safe_endpoint_summary(self._base_url),
        }


class OpenAICompatibleRerankerProvider:
    """OpenAI/Jina/Cohere 兼容 /rerank 客户端，受 MODEL_CALLS_ENABLED 保护。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        model_calls_enabled: bool,
        timeout_seconds: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._model_calls_enabled = bool(model_calls_enabled)
        self._timeout = float(timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self._model and self._model_calls_enabled)

    def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None) -> list[tuple[int, float]]:
        if not self._model_calls_enabled:
            raise RerankerError("外部模型调用已关闭（MODEL_CALLS_ENABLED=false），拒绝调用云端重排接口")
        if not self._base_url or not self._model:
            raise RerankerError("未完整配置 RERANKER_BASE_URL 与 RERANKER_MODEL")
        if not documents:
            return []
        url = self._base_url if self._base_url.endswith("/rerank") else f"{self._base_url}/rerank"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        body: dict = {"model": self._model, "query": query, "documents": list(documents)}
        if top_n is not None:
            body["top_n"] = int(top_n)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, json=body, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise RerankerError(f"云端重排请求失败: {type(exc).__name__}") from exc
        items = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise RerankerError("云端重排返回格式异常")
        ranked: list[tuple[int, float]] = []
        try:
            for item in items:
                if not isinstance(item, dict):
                    continue
                idx = int(item.get("index", -1))
                score = float(item.get("relevance_score", item.get("score", 0.0)))
                if 0 <= idx < len(documents) and math.isfinite(score):
                    ranked.append((idx, score))
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankerError(f"云端重排响应项格式异常: {type(exc).__name__}") from exc
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        return ranked[:top_n] if top_n is not None else ranked

    def describe(self) -> dict:
        return {
            "provider": "openai_compatible",
            "configured": bool(self._base_url and self._model),
            "gated_by_model_calls": not self._model_calls_enabled,
            "model": self._model,
            "endpoint": _safe_endpoint_summary(self._base_url),
        }


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "tei":
        return TEIEmbeddingProvider(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            query_prefix=settings.embedding_query_prefix,
            document_prefix=settings.embedding_document_prefix,
            normalize=settings.embedding_normalize,
            batch_size=settings.embedding_batch_size,
            timeout_seconds=settings.embedding_timeout_seconds,
            api_key=settings.embedding_api_key.get_secret_value(),
        )
    if settings.embedding_provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key.get_secret_value(),
            model=settings.embedding_model,
            model_calls_enabled=settings.model_calls_enabled,
            dimensions=settings.embedding_dimensions,
            query_prefix=settings.embedding_query_prefix,
            document_prefix=settings.embedding_document_prefix,
            normalize=settings.embedding_normalize,
            batch_size=settings.embedding_batch_size,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
    return DisabledEmbeddingProvider()


def create_reranker_provider(settings: Settings) -> RerankerProvider:
    if settings.reranker_provider == "tei":
        return TEIRerankerProvider(
            base_url=settings.reranker_base_url,
            model=settings.reranker_model,
            timeout_seconds=settings.reranker_timeout_seconds,
            api_key=settings.reranker_api_key.get_secret_value(),
        )
    if settings.reranker_provider == "openai_compatible":
        return OpenAICompatibleRerankerProvider(
            base_url=settings.reranker_base_url,
            api_key=settings.reranker_api_key.get_secret_value(),
            model=settings.reranker_model,
            model_calls_enabled=settings.model_calls_enabled,
            timeout_seconds=settings.reranker_timeout_seconds,
        )
    return DisabledRerankerProvider()
