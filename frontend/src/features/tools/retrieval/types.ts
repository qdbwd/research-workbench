export type EmbeddingProfileItem = {
  profile_id: string;
  provider: string;
  model: string;
  dimensions: number;
  query_prefix: string;
  document_prefix: string;
  normalize: boolean;
  contextual: boolean;
  status: 'building' | 'ready' | 'stale';
  knowledge_chunks: number;
  source_chunks: number;
  updated_at: string | null;
  active: boolean;
};

export type RagSettingsState = {
  hybrid_enabled: boolean;
  auto_index_enabled: boolean;
  rerank_enabled: boolean;
  contextual_embedding_enabled: boolean;
  rrf_k: number;
  rerank_top_k: number;
  active_profile_id: string | null;
  revision: number;
  updated_at: string | null;
};

export type RetrievalStatus = {
  pgvector: { installed: boolean; version: string | null };
  settings: RagSettingsState;
  effective_mode: 'bm25_only' | 'hybrid' | 'hybrid_rerank';
  effective_reason: string;
  embedding_provider: {
    provider: string;
    configured: boolean;
    gated_by_model_calls?: boolean;
    model: string;
    dimensions: number | null;
    endpoint: string;
    normalize: boolean;
    query_prefix: string;
  };
  reranker_provider: {
    provider: string;
    configured: boolean;
    gated_by_model_calls?: boolean;
    model: string;
    endpoint: string;
  };
  coverage: {
    knowledge_total_chunks: number;
    knowledge_embedded_chunks: number;
    source_total_chunks: number;
    source_embedded_chunks: number;
    total_chunks: number;
    embedded_chunks: number;
    ratio: number;
  };
  profiles: EmbeddingProfileItem[];
  index_queue: Record<'pending' | 'indexing' | 'ready' | 'failed', number>;
  collections_count: number;
};


export type AssetKind = 'knowledge' | 'source';
export type RagScope = 'auto' | 'all' | 'collections';
export type IndexStatus = 'pending' | 'indexing' | 'ready' | 'failed' | 'partial' | 'unindexed';
export type GroupTag = { id: string; name: string };
export type Collection = GroupTag & {
  description: string; revision: number; asset_count: number; knowledge_count: number; source_count: number;
  chunks_total: number; chunks_embedded: number; updated_at: string;
};
export type Asset = {
  asset_kind: AssetKind; asset_id: string; version_id: string; version: number; title: string; subtitle: string;
  collections: GroupTag[]; chunks_total: number; chunks_enabled: number; chunks_embedded: number;
  index_status: IndexStatus; last_error: string | null; updated_at: string;
};
export type AssetPage = { items: Asset[]; total: number };
export type AssetRef = Pick<Asset, 'asset_kind' | 'asset_id'>;
export type Chunk = {
  chunk_index: number; segment_id: string | null; page: number | null; start: number; end: number;
  char_count: number; text: string; contextual_header: string | null; enabled: boolean;
  vector_status: 'ready' | 'stale' | 'missing'; dimensions: number | null; updated_at: string | null;
};
export type ChunkPage = {
  asset: Asset; version_id: string; profile_id: string | null; contextual_enabled: boolean;
  total: number; enabled_count: number; disabled_count: number; items: Chunk[];
};
export const basePath = '/api/retrieval';
export const assetKey = (asset: AssetRef) => `${asset.asset_kind}:${asset.asset_id}`;
export const assetPath = (asset: AssetRef) => `${basePath}/assets/${asset.asset_kind}/${encodeURIComponent(asset.asset_id)}`;
export const originalPath = (asset: AssetRef) =>
  `${asset.asset_kind === 'knowledge' ? '/knowledge' : '/sources'}/${encodeURIComponent(asset.asset_id)}`;
export const modeLabel: Record<RetrievalStatus['effective_mode'], string> = {
  bm25_only: '仅联合 BM25',
  hybrid: 'BM25 + pgvector RRF 混合检索',
  hybrid_rerank: 'BM25 + pgvector RRF + 可选重排',
};
export const stateLabel: Record<IndexStatus, string> = {
  pending: '待索引', indexing: '索引中', ready: '已就绪', failed: '失败', partial: '部分就绪', unindexed: '未索引',
};
export const inputClass = 'min-w-0 rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-xs';
export const primaryClass = 'rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:brightness-95 disabled:opacity-50';
export const secondaryClass = 'rounded-lg border border-line bg-white px-3 py-1.5 text-xs font-medium hover:border-brand disabled:opacity-50';
