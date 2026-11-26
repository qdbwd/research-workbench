import { useState } from 'react';
import { messageOf, send } from '../../../shared/api/api';
import { ErrorBox } from '../../../shared/ui/components';
import { basePath, inputClass, primaryClass, type AssetRef, type Collection, type GroupTag, type RagSettingsState } from './types';

const modes = { bm25: '纯 BM25', vector: '纯向量', hybrid: '混合 RRF', hybrid_rerank: '混合 + 重排' };
type Mode = keyof typeof modes;
type Hit = {
  rank: number; kind: 'knowledge' | 'material'; asset_id: string; version_id: string; title: string;
  segment_id: string | null; page: number | null; chunk_index: number; start: number; end: number;
  snippet: string; retrieval_mode: string; collections: GroupTag[];
  scores: Record<'final' | 'bm25' | 'bm25_rank' | 'vector_similarity' | 'vector_rank' | 'rrf' | 'rerank', number | null>;
};
type Result = {
  requested_mode: Mode; effective_mode: Mode | 'unavailable'; degraded_reason: string | null;
  active_profile_id: string | null; timing_ms: Record<string, number>; hits: Hit[];
};
const timerLabels: Record<string, string> = { total_ms: '总耗时', bm25_ms: 'BM25', embed_ms: '查询嵌入',
  vector_ms: '向量', rrf_ms: '融合', rerank_ms: '重排' };
const scoreLabels: Record<keyof Hit['scores'], string> = {
  final: '最终', bm25: 'BM25', bm25_rank: '词法名次', vector_similarity: '余弦', vector_rank: '向量名次', rrf: 'RRF', rerank: '重排',
};
const box = 'min-w-0 rounded-xl border border-line bg-white p-4 max-md:p-3';

export function HitTestingTab({ groups, settings, onPreview }: {
  groups: Collection[]; settings?: RagSettingsState; onPreview: (asset: AssetRef) => void;
}) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<Mode>('hybrid');
  const [scope, setScope] = useState('all');
  const [top, setTop] = useState(5);
  const [rrf, setRrf] = useState<number | null>(null);
  const [rerank, setRerank] = useState<number | null>(null);
  const [threshold, setThreshold] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit() {
    setBusy(true);
    setError('');
    setResult(null);
    try {
      setResult(await send<Result>(basePath + '/hit-test', {
        query: query.trim(), mode, scope: scope.startsWith('collection:') ? 'all' : scope,
        collection_ids: scope.startsWith('collection:') ? [scope.slice(11)] : null,
        top_k: top, rrf_k: rrf, rerank_top_k: rerank, min_vector_similarity: threshold,
      }));
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(false); }
  }

  return <div className="grid items-start gap-4 lg:grid-cols-[330px_minmax(0,1fr)]">
    <form className={`${box} grid gap-3`} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div>
        <h2 className="mb-1 mt-0 text-sm font-semibold">在线召回测试（Hit Testing）</h2>
        <p className="m-0 text-xs text-muted">只读查询，用于查看召回命中、排序分数和每阶段耗时。</p>
      </div>
      <label className="m-0 grid gap-1 text-xs font-medium">测试查询语句
        <textarea rows={3} maxLength={500} value={query} placeholder="输入要测试的问题或关键词…"
          onChange={(event) => setQuery(event.target.value)} className={`${inputClass} w-full leading-relaxed`} />
      </label>
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className="rounded-full bg-canvas px-2 py-0.5 text-[11px] text-control"
          onClick={() => setQuery('生产变更上线后应检查哪些监控信号？')}>示例：变更后监控信号</button>
        <button type="button" className="rounded-full bg-canvas px-2 py-0.5 text-[11px] text-control"
          onClick={() => setQuery('pgvector 跨 Schema 如何执行余弦距离查询？')}>示例：pgvector 跨 Schema</button>
      </div>
      <fieldset className="m-0 min-w-0 border-0 p-0">
        <legend className="mb-1 text-xs font-medium">检索算子模式</legend>
        <div className="grid grid-cols-2 gap-1.5 text-xs">
          {(Object.entries(modes) as [Mode, string][]).map(([id, label]) => <button type="button" key={id}
            aria-pressed={id === mode} onClick={() => setMode(id)}
            className={`rounded-lg border px-2 py-1.5 ${id === mode ? 'border-brand bg-[#e3f1e8] text-brand' : 'border-line text-control'}`}>{label}</button>)}
        </div>
      </fieldset>
      <label className="m-0 grid gap-1 text-xs">检索范围
        <select className={inputClass} value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="all">全库（知识文章 + 材料库）</option>
          <option value="knowledge">仅已发布知识文章</option>
          <option value="material">仅已保存材料库</option>
          {groups.map((group) => <option key={group.id} value={`collection:${group.id}`}>知识集：{group.name} ({group.asset_count})</option>)}
        </select>
      </label>
      <div className="grid gap-2.5 rounded-lg border border-line bg-[#fafcfa] p-3 text-xs">
        <label className="m-0 grid gap-1"><span className="flex justify-between">返回条数 Top-K <strong>{top}</strong></span>
          <input type="range" className="h-4 cursor-pointer border-0 bg-transparent p-0 accent-brand" min={1} max={20} value={top} onChange={(event) => setTop(Number(event.target.value))} />
        </label>
        <label className="m-0 grid gap-1"><span className="flex justify-between">RRF 融合常数 k <strong>{rrf ?? settings?.rrf_k ?? 60}</strong></span>
          <input type="range" className="h-4 cursor-pointer border-0 bg-transparent p-0 accent-brand" min={1} max={1000} value={rrf ?? settings?.rrf_k ?? 60}
            onChange={(event) => setRrf(Number(event.target.value))} disabled={mode === 'vector' || mode === 'bm25'} />
        </label>
        <label className="m-0 grid gap-1"><span className="flex justify-between">向量相似度下限 <strong>{threshold.toFixed(2)}</strong></span>
          <input type="range" className="h-4 cursor-pointer border-0 bg-transparent p-0 accent-brand" min={0} max={1} step={0.05} value={threshold}
            onChange={(event) => setThreshold(Number(event.target.value))} disabled={mode === 'bm25'} />
        </label>
        {mode === 'hybrid_rerank' && <label className="m-0 grid gap-1"><span className="flex justify-between">重排候选数 <strong>{rerank ?? settings?.rerank_top_k ?? 20}</strong></span>
          <input type="range" className="h-4 cursor-pointer border-0 bg-transparent p-0 accent-brand" min={1} max={100} value={rerank ?? settings?.rerank_top_k ?? 20}
            onChange={(event) => setRerank(Number(event.target.value))} />
        </label>}
      </div>
      <button className={primaryClass} disabled={busy || !query.trim()}>{busy ? '查询中…' : '运行召回测试'}</button>
    </form>
    <section className={box} aria-label="召回结果">
      <ErrorBox message={error} />
      {!result && !error && <p className="m-0 rounded-lg border border-line bg-[#fafcfa] p-3 text-xs text-muted">
        {busy ? '正在召回…' : '运行召回测试后查看结果。'}</p>}
      {result && <>
        <div className="mb-3 grid gap-1 rounded-lg border border-line bg-[#fafcfa] p-3 text-xs">
          <strong>{result.hits.length} 条命中 · {result.effective_mode === 'unavailable' ? '向量不可用' : modes[result.effective_mode]}</strong>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted">
            {Object.entries(result.timing_ms).sort(([key]) => key === 'total_ms' ? -1 : 0).map(([key, ms]) =>
              <span key={key}>{timerLabels[key] ?? key} {ms} ms</span>)}
          </div>
          {result.degraded_reason && <p role="status" className="m-0 text-danger">{result.degraded_reason}</p>}
        </div>
        <div className="grid gap-3">
          {result.hits.map((hit) => <article key={hit.rank} className="min-w-0 rounded-lg border border-line p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-[#dceadf] px-2 py-0.5 text-xs font-semibold text-brand">#{hit.rank}</span>
              <h3 className="m-0 min-w-0 break-words text-sm font-semibold">{hit.title}</h3>
              <span className="text-[11px] text-muted">{hit.kind === 'knowledge' ? '知识文章' : '材料库'} · {hit.retrieval_mode}</span>
            </div>
            <p className="my-2 whitespace-pre-wrap break-words rounded bg-canvas p-2 text-xs leading-relaxed">{hit.snippet}</p>
            <div className="flex flex-wrap gap-1.5 text-[11px] text-control">
              {(Object.entries(hit.scores) as [keyof Hit['scores'], number | null][]).filter(([, value]) => value !== null)
                .map(([key, value]) => <span key={key} className="rounded bg-canvas px-1.5 py-0.5">{scoreLabels[key]} {Number(value?.toFixed(4))}</span>)}
              {hit.collections.map((group) => <span key={group.id} className="break-words rounded bg-[#e3f1e8] px-1.5 py-0.5 text-brand">{group.name}</span>)}
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
              <span>块 #{hit.chunk_index + 1} · [{hit.start}, {hit.end}){hit.page != null ? ` · 第 ${hit.page} 页` : ''}</span>
              <button type="button" className="font-medium text-brand hover:underline"
                onClick={() => onPreview({ asset_kind: hit.kind === 'knowledge' ? 'knowledge' : 'source', asset_id: hit.asset_id })}>查看所属分块 →</button>
            </div>
          </article>)}
        </div>
      </>}
    </section>
  </div>;
}
