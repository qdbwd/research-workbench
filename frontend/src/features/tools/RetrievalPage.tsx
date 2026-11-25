import { useEffect, useState } from 'react';
import { Database, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox, Loading, PageTitle } from '../../shared/ui/components';
import { AgentSettingsTabs } from './AgentSettingsTabs';

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
};

type RebuildSummary = {
  profile_id: string;
  dimensions: number;
  contextual: boolean;
  knowledge_embedded: number;
  knowledge_skipped: number;
  source_embedded: number;
  source_skipped: number;
  elapsed_ms: number;
};

const modeLabel: Record<RetrievalStatus['effective_mode'], string> = {
  bm25_only: '仅联合 BM25',
  hybrid: 'BM25 + pgvector RRF 混合检索',
  hybrid_rerank: 'BM25 + pgvector RRF + 可选重排',
};

const profileStatusLabel: Record<EmbeddingProfileItem['status'], string> = {
  ready: '已就绪',
  building: '构建中',
  stale: '已过时',
};

const providerLabel: Record<string, string> = {
  disabled: '未启用（disabled）',
  tei: '本机 / 内网 HuggingFace TEI',
  openai_compatible: 'OpenAI 兼容 HTTP 端点',
};

export function RetrievalPage() {
  const [tick, setTick] = useState(0);
  const { data, loading, error } = useResource<RetrievalStatus>('/api/retrieval/status', tick);
  const [draft, setDraft] = useState<RagSettingsState | null>(null);
  const [savedRevision, setSavedRevision] = useState<number>(-1);
  const [busy, setBusy] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [lastRebuild, setLastRebuild] = useState<RebuildSummary | null>(null);

  useEffect(() => {
    if (!data) return;
    if (draft === null || data.settings.revision !== savedRevision) {
      setDraft(data.settings);
      setSavedRevision(data.settings.revision);
    }
  }, [data, draft, savedRevision]);

  const dirty = Boolean(
    data &&
      draft &&
      (draft.hybrid_enabled !== data.settings.hybrid_enabled ||
        draft.rerank_enabled !== data.settings.rerank_enabled ||
        draft.contextual_embedding_enabled !== data.settings.contextual_embedding_enabled ||
        draft.rrf_k !== data.settings.rrf_k ||
        draft.rerank_top_k !== data.settings.rerank_top_k ||
        draft.active_profile_id !== data.settings.active_profile_id),
  );

  async function saveSettings(patch?: Partial<RagSettingsState>) {
    if (!data || !draft) return;
    const target = patch ? { ...draft, ...patch } : draft;
    setBusy(true);
    setActionError('');
    setNotice('');
    try {
      const updated = await send<RetrievalStatus>(
        '/api/retrieval/settings',
        {
          expected_revision: savedRevision >= 0 ? savedRevision : data.settings.revision,
          hybrid_enabled: target.hybrid_enabled,
          rerank_enabled: target.rerank_enabled,
          contextual_embedding_enabled: target.contextual_embedding_enabled,
          rrf_k: target.rrf_k,
          rerank_top_k: target.rerank_top_k,
          active_profile_id: target.active_profile_id,
        },
        'PATCH',
      );
      setDraft(updated.settings);
      setSavedRevision(updated.settings.revision);
      setNotice('RAG 检索设置已保存。');
      setTick((value) => value + 1);
    } catch (err) {
      setActionError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  async function runRebuild(force: boolean) {
    setRebuilding(true);
    setActionError('');
    setNotice('');
    try {
      const result = await send<{ summary: RebuildSummary; status: RetrievalStatus }>(
        '/api/retrieval/rebuild',
        {
          contextual: draft?.contextual_embedding_enabled,
          activate: true,
          force,
        },
        'POST',
      );
      setLastRebuild(result.summary);
      setDraft(result.status.settings);
      setSavedRevision(result.status.settings.revision);
      setNotice(
        `向量索引${force ? '全量重建' : '增量补建'}完成（档案 ${result.summary.profile_id}，耗时 ${result.summary.elapsed_ms} ms）。`,
      );
      setTick((value) => value + 1);
    } catch (err) {
      setActionError(messageOf(err));
    } finally {
      setRebuilding(false);
    }
  }

  async function removeProfile(profileId: string) {
    setBusy(true);
    setActionError('');
    setNotice('');
    try {
      const result = await send<{ deleted_profile_id: string; status?: RetrievalStatus }>(
        `/api/retrieval/profiles/${encodeURIComponent(profileId)}`,
        undefined,
        'DELETE',
      );
      if (result?.status?.settings) {
        setDraft(result.status.settings);
        setSavedRevision(result.status.settings.revision);
      }
      setNotice(`已删除向量档案 ${profileId}。`);
      setTick((value) => value + 1);
    } catch (err) {
      setActionError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  const coveragePercent = data ? Math.round(data.coverage.ratio * 100) : 0;

  return (
    <>
      <PageTitle title="智能体设置">
        <Button className="button-secondary" onClick={() => setTick((value) => value + 1)}>
          <RefreshCw size={15} />
          刷新状态
        </Button>
      </PageTitle>
      <AgentSettingsTabs />
      <ErrorBox message={error || actionError} />
      {notice && (
        <p className="mb-4 rounded-lg border border-[#b9d8c0] bg-[#edf6ef] px-3.5 py-2.5 text-sm text-[#245932]">
          {notice}
        </p>
      )}
      {loading && !data && <Loading />}
      {data && draft && (
        <div className="grid gap-4">
          {/* 1. 当前向量库与检索总览 */}
          <section className="card rounded-xl border border-line bg-white p-5" aria-label="向量数据库状态">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="m-0 flex items-center gap-2 text-base">
                <Database size={18} aria-hidden="true" />
                pgvector 向量数据库状态
              </h2>
              <span
                className={
                  data.effective_mode === 'bm25_only'
                    ? 'rounded-full bg-[#eef2ee] px-2.5 py-1 text-xs font-medium text-control'
                    : 'rounded-full bg-[#dceadf] px-2.5 py-1 text-xs font-medium text-brand'
                }
              >
                {modeLabel[data.effective_mode]}
              </span>
            </div>
            <p className="mt-2 mb-4 text-sm text-muted">{data.effective_reason}</p>

            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-line bg-[#fafcfa] p-3">
                <div className="text-xs text-muted">pgvector 扩展</div>
                <div className="mt-1 text-sm font-semibold">
                  {data.pgvector.installed ? `已安装 (v${data.pgvector.version})` : '未安装'}
                </div>
              </div>
              <div className="rounded-lg border border-line bg-[#fafcfa] p-3">
                <div className="text-xs text-muted">知识库片段覆盖</div>
                <div className="mt-1 text-sm font-semibold">
                  {data.coverage.knowledge_embedded_chunks} / {data.coverage.knowledge_total_chunks}
                </div>
              </div>
              <div className="rounded-lg border border-line bg-[#fafcfa] p-3">
                <div className="text-xs text-muted">导入材料片段覆盖</div>
                <div className="mt-1 text-sm font-semibold">
                  {data.coverage.source_embedded_chunks} / {data.coverage.source_total_chunks}
                </div>
              </div>
            </div>

            <div className="mt-4">
              <div className="flex items-center justify-between text-xs text-muted">
                <span>当前活跃档案总覆盖率</span>
                <span>
                  {data.coverage.embedded_chunks} / {data.coverage.total_chunks} ({coveragePercent}%)
                </span>
              </div>
              <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-[#e6ece7]">
                <div
                  className="h-full rounded-full bg-brand transition-all"
                  style={{ width: `${coveragePercent}%` }}
                />
              </div>
            </div>

            <div className="mt-4 grid gap-3 border-t border-line pt-4 sm:grid-cols-2">
              <div className="text-xs">
                <div className="font-semibold text-ink">嵌入服务（Embedding Provider）</div>
                <div className="mt-1 text-muted">
                  类型：{providerLabel[data.embedding_provider.provider] ?? data.embedding_provider.provider}
                </div>
                {data.embedding_provider.model && (
                  <div className="text-muted">
                    模型：{data.embedding_provider.model}
                    {data.embedding_provider.dimensions ? `（${data.embedding_provider.dimensions} 维）` : ''}
                  </div>
                )}
                {data.embedding_provider.endpoint && (
                  <div className="text-muted">端点：{data.embedding_provider.endpoint}</div>
                )}
                {data.embedding_provider.gated_by_model_calls && (
                  <div className="text-[#a44b40]">
                    受 MODEL_CALLS_ENABLED=false 保护，云端嵌入调用当前处于锁定状态。
                  </div>
                )}
              </div>
              <div className="text-xs">
                <div className="font-semibold text-ink">可选重排服务（Reranker Provider）</div>
                <div className="mt-1 text-muted">
                  类型：{providerLabel[data.reranker_provider.provider] ?? data.reranker_provider.provider}
                </div>
                {data.reranker_provider.model && (
                  <div className="text-muted">模型：{data.reranker_provider.model}</div>
                )}
                {data.reranker_provider.endpoint && (
                  <div className="text-muted">端点：{data.reranker_provider.endpoint}</div>
                )}
                {data.reranker_provider.gated_by_model_calls && (
                  <div className="text-[#a44b40]">
                    受 MODEL_CALLS_ENABLED=false 保护，云端重排调用当前处于锁定状态。
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* 2. RAG 开关与参数管理 */}
          <section className="card rounded-xl border border-line bg-white p-5" aria-label="RAG 功能管理">
            <h2 className="m-0 mb-3 flex items-center gap-2 text-base">
              <Sparkles size={18} aria-hidden="true" />
              RAG 混合检索与可选重排管理
            </h2>
            <p className="mt-0 mb-4 text-xs text-muted">
              端点与 API 密钥仅由部署环境变量（.env）注入；此处管理运行时开关与索引重建。服务不可用时会自动降级为联合 BM25，不中断对话。
            </p>

            <div className="grid gap-3">
              <label className="flex items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={draft.hybrid_enabled}
                  onChange={(e) => setDraft({ ...draft, hybrid_enabled: e.target.checked })}
                />
                <span>
                  <strong className="block font-medium">启用向量混合检索（BM25 + pgvector RRF）</strong>
                  <span className="text-xs text-muted">
                    开启后，资料问答将融合词法 BM25 与 pgvector 余弦相似度结果；未配置嵌入服务时自动回退纯 BM25。
                  </span>
                </span>
              </label>

              <label className="flex items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={draft.rerank_enabled}
                  onChange={(e) => setDraft({ ...draft, rerank_enabled: e.target.checked })}
                />
                <span>
                  <strong className="block font-medium">启用可选重排（Reranker）</strong>
                  <span className="text-xs text-muted">
                    仅在混合检索启用且配置了 Reranker 服务时对 RRF 候选做二次精排；重排超时或关闭时保留原 RRF 排序。
                  </span>
                </span>
              </label>

              <label className="flex items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={draft.contextual_embedding_enabled}
                  onChange={(e) =>
                    setDraft({ ...draft, contextual_embedding_enabled: e.target.checked })
                  }
                />
                <span>
                  <strong className="block font-medium">上下文增强嵌入（标题 + 摘要/标签）</strong>
                  <span className="text-xs text-muted">
                    生成向量时自动在片段前拼入文档标题、分类、标签与摘要，不修改原文正文与引用字符偏移。
                  </span>
                </span>
              </label>

              <div className="mt-1 flex flex-wrap gap-4">
                <label className="grid gap-1 text-xs text-muted">
                  RRF 融合常数 k（1-1000）
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    className="w-32 rounded-lg border border-line px-2.5 py-1.5 text-sm text-ink"
                    value={draft.rrf_k}
                    onChange={(e) =>
                      setDraft({ ...draft, rrf_k: Math.max(1, Number(e.target.value) || 60) })
                    }
                  />
                </label>
                <label className="grid gap-1 text-xs text-muted">
                  重排候选数 Top-K（1-100）
                  <input
                    type="number"
                    min={1}
                    max={100}
                    className="w-32 rounded-lg border border-line px-2.5 py-1.5 text-sm text-ink"
                    value={draft.rerank_top_k}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        rerank_top_k: Math.max(1, Number(e.target.value) || 20),
                      })
                    }
                  />
                </label>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
              <Button disabled={busy || !dirty} onClick={() => saveSettings()}>
                {busy ? '保存中…' : '保存设置'}
              </Button>
              <Button
                className="button-secondary"
                disabled={
                  rebuilding ||
                  !data.embedding_provider.configured ||
                  Boolean(data.embedding_provider.gated_by_model_calls)
                }
                onClick={() => runRebuild(false)}
              >
                {rebuilding ? '构建中…' : '增量补建向量索引'}
              </Button>
              <Button
                className="button-secondary"
                disabled={
                  rebuilding ||
                  !data.embedding_provider.configured ||
                  Boolean(data.embedding_provider.gated_by_model_calls)
                }
                onClick={() => runRebuild(true)}
              >
                强制全量重建
              </Button>
            </div>

            {lastRebuild && (
              <p className="mt-3 mb-0 text-xs text-muted">
                最近一次构建：知识新增 {lastRebuild.knowledge_embedded}（跳过 {lastRebuild.knowledge_skipped}），
                材料新增 {lastRebuild.source_embedded}（跳过 {lastRebuild.source_skipped}），
                维度 {lastRebuild.dimensions}，上下文增强：{lastRebuild.contextual ? '开启' : '关闭'}。
              </p>
            )}
          </section>

          {/* 3. 向量档案列表 */}
          <section className="card rounded-xl border border-line bg-white p-5" aria-label="向量档案列表">
            <h2 className="m-0 mb-3 text-base">向量档案（Embedding Profiles）</h2>
            {data.profiles.length === 0 ? (
              <p className="m-0 text-sm text-muted">
                当前尚无向量档案。配置嵌入服务（如本地 GPU TEI）并点击上方“增量补建向量索引”即可生成。
              </p>
            ) : (
              <div className="grid gap-2">
                {data.profiles.map((profile) => (
                  <div
                    key={profile.profile_id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line p-3"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                        <span className="break-all">{profile.profile_id}</span>
                        <span className="rounded bg-[#eef2ee] px-1.5 py-0.5 text-[11px] text-control">
                          {profileStatusLabel[profile.status]}
                        </span>
                        {profile.active && (
                          <span className="rounded bg-[#dceadf] px-1.5 py-0.5 text-[11px] text-brand">
                            当前活跃
                          </span>
                        )}
                      </div>
                      <div className="mt-1 text-xs text-muted">
                        模型：{profile.model} · {profile.dimensions} 维 · 上下文增强：
                        {profile.contextual ? '是' : '否'} · 知识 {profile.knowledge_chunks} 片段 · 材料{' '}
                        {profile.source_chunks} 片段
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {!profile.active && profile.status === 'ready' && (
                        <Button
                          className="button-secondary"
                          disabled={busy}
                          onClick={() => saveSettings({ active_profile_id: profile.profile_id })}
                        >
                          设为当前档案
                        </Button>
                      )}
                      {(!profile.active || !data.settings.hybrid_enabled) && (
                        <Button
                          className="button-quiet text-[#a44b40]"
                          disabled={busy}
                          onClick={() => removeProfile(profile.profile_id)}
                        >
                          <Trash2 size={14} />
                          删除
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );
}
