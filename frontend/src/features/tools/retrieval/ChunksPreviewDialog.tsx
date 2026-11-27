import { useState } from 'react';
import { Link } from 'react-router-dom';
import { messageOf, send, useResource } from '../../../shared/api/api';
import { ErrorBox, Loading } from '../../../shared/ui/components';
import { Dialog } from '../../../shared/ui/Dialog';
import { assetPath, inputClass, originalPath, primaryClass, secondaryClass, type AssetRef, type Chunk, type ChunkPage } from './types';

const vectorLabel = { ready: '已向量化', stale: '过时', missing: '待向量化' };

export function ChunksPreviewDialog({ asset, revision, canEmbed, onChange, onClose }: {
  asset: AssetRef; revision: number; canEmbed: boolean; onChange: (notice?: string) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [enabled, setEnabled] = useState('');
  const [contextual, setContextual] = useState(true);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const params = new URLSearchParams({ query, offset: String(offset) });
  if (enabled) params.set('enabled', enabled);
  const path = assetPath(asset);
  const { data, error: loadError, loading } = useResource<ChunkPage>(`${path}/chunks?${params}`, revision);

  async function toggle(chunk: Chunk) {
    if (!data) return;
    setBusy(true);
    setError('');
    try {
      await send(`${path}/chunks/status`, {
        version_id: data.version_id, chunk_index: chunk.chunk_index, segment_id: chunk.segment_id, enabled: !chunk.enabled,
      }, 'PATCH');
      onChange();
    } catch (failure) {
      setError(messageOf(failure));
      onChange();
    } finally { setBusy(false); }
  }

  async function reindex() {
    setBusy(true);
    setError('');
    try {
      await send(`${path}/reindex`);
      onChange('已提交单篇向量索引作业。');
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(false); }
  }

  return <Dialog title={data ? `分块预览 · ${data.asset.title}` : '分块预览'} onClose={onClose} busy={busy}
    scrollBody={false}
    footer={<>
      <Link className="mr-auto text-xs font-medium text-brand hover:underline" to={originalPath(asset)}>
        {asset.asset_kind === 'knowledge' ? '前往原文编辑 →' : '查看来源原文 →'}
      </Link>
      <button className={secondaryClass} disabled={busy || !canEmbed || !data} onClick={() => void reindex()}>单篇重算向量</button>
      <button className={primaryClass} disabled={busy} onClick={onClose}>关闭</button>
    </>}>
    <div className="shrink-0 border-b border-line bg-[#fafcfa] px-5 py-2.5 text-xs">
      {data && <p className="mb-2 mt-0 break-words text-muted">
        V{data.asset.version} · 已启用 {data.enabled_count} 块 · 已排除 {data.disabled_count} 块 · {data.profile_id ?? '未启用向量档案'}
      </p>}
      <div className="flex flex-wrap items-center gap-2">
        <input type="search" aria-label="筛选分块正文" className={`${inputClass} w-full max-w-[260px] bg-white`}
          placeholder="在分块正文中过滤关键词…" value={query} maxLength={500}
          onChange={(event) => { setQuery(event.target.value); setOffset(0); }} />
        <select aria-label="分块检索状态" className={`${inputClass} w-auto`} value={enabled}
          onChange={(event) => { setEnabled(event.target.value); setOffset(0); }}>
          <option value="">全部分块</option><option value="true">已启用</option><option value="false">已排除</option>
        </select>
        <label className="check m-0 flex-row items-center gap-1.5 text-xs font-normal">
          <input type="checkbox" className="accent-brand" checked={contextual}
            onChange={(event) => setContextual(event.target.checked)} /> 显示上下文增强前缀
        </label>
      </div>
      <ErrorBox message={error || loadError} />
    </div>
    <div aria-label="分块列表" className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-5 max-md:p-3">
      {loading && !data && <Loading />}
      {data?.items.map((item, idx) => <article key={`${item.segment_id ?? ''}:${item.chunk_index}`}
        className={`rounded-lg border border-line p-3 ${item.enabled ? 'bg-white' : 'bg-canvas'}`}>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <strong className="text-ink">块 #{offset + idx + 1}</strong>
            <span className="font-mono">[{item.start}, {item.end})</span>
            <span>{item.char_count} 字符{item.page !== null ? ` · 第 ${item.page} 页` : ''}</span>
            <span className={`rounded px-1.5 py-0.5 ${item.vector_status === 'ready' ? 'bg-[#e3f1e8] text-brand' : 'bg-canvas'}`}>
              {vectorLabel[item.vector_status]}{item.dimensions ? ` · ${item.dimensions} 维` : ''}
            </span>
          </div>
          <button type="button" role="switch" aria-checked={item.enabled}
            title={item.enabled ? '点击排除该分块，不参与新检索' : '点击恢复该分块参与检索'}
            className={`inline-flex items-center gap-1.5 ${secondaryClass}`} disabled={busy || loading} onClick={() => void toggle(item)}>
            <span aria-hidden="true" className={`inline-flex w-7 rounded-full p-0.5 ${item.enabled ? 'justify-end bg-brand' : 'bg-[#aeb9ae]'}`}>
              <span className="size-2.5 rounded-full bg-white" />
            </span>
            {item.enabled ? '参与检索' : '已排除'}
          </button>
        </div>
        {contextual && item.contextual_header && <pre className="mb-2 whitespace-pre-wrap break-words rounded bg-canvas p-2 text-xs text-control">{item.contextual_header}</pre>}
        <p className="m-0 whitespace-pre-wrap break-words text-xs leading-relaxed text-ink">{item.text}</p>
      </article>)}
      {data && !data.items.length && <p className="text-xs text-muted">没有匹配的分块。</p>}
      {data && (offset > 0 || offset + data.items.length < data.total) && <div className="flex justify-end gap-2">
        <button className={secondaryClass} disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button>
        <button className={secondaryClass} disabled={offset + 30 >= data.total} onClick={() => setOffset(offset + 30)}>下一页</button>
      </div>}
    </div>
  </Dialog>;
}
