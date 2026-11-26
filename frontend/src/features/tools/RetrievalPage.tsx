import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox, Loading, PageTitle } from '../../shared/ui/components';
import { AgentSettingsTabs } from './AgentSettingsTabs';
import { ChunksPreviewDialog } from './retrieval/ChunksPreviewDialog';
import { CollectionsAssetsTab } from './retrieval/CollectionsAssetsTab';
import { EngineConfigTab } from './retrieval/EngineConfigTab';
import { HitTestingTab } from './retrieval/HitTestingTab';
import { assetKey, basePath, modeLabel, type AssetRef, type Collection, type RetrievalStatus } from './retrieval/types';

export type { RetrievalStatus } from './retrieval/types';
const tabs = [['docs', '知识集与文档索引'], ['hittest', '召回测试（Hit Testing）'], ['engine', '引擎与档案配置']] as const;
const statClass = 'min-w-0 rounded-xl border border-line bg-white p-3.5';

export function RetrievalPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'hittest' ? 'hittest' : params.get('tab') === 'engine' ? 'engine' : 'docs';
  const [kind, identity] = (params.get('asset') ?? '').split(':');
  const asset: AssetRef | null = (kind === 'knowledge' || kind === 'source') && identity ? { asset_kind: kind, asset_id: identity } : null;
  const [revision, setRevision] = useState(0);
  const status = useResource<RetrievalStatus>(basePath + '/status', revision);
  const groups = useResource<Collection[]>(basePath + '/collections', revision);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const data = status.data;
  const queue = data?.index_queue;
  const active = data?.profiles.find((profile) => profile.active);
  const canEmbed = Boolean(data?.embedding_provider.configured && !data.embedding_provider.gated_by_model_calls);
  const refresh = useCallback((message?: string) => {
    if (message) setNotice(message);
    setRevision((value) => value + 1);
  }, []);
  const hasWork = Boolean(queue?.pending || queue?.indexing);
  useEffect(() => {
    if (!hasWork) return;
    const interval = window.setInterval(refresh, 4000);
    return () => window.clearInterval(interval);
  }, [hasWork, refresh]);

  function openAsset(target: AssetRef | null) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (target) next.set('asset', assetKey(target));
      else next.delete('asset');
      return next;
    });
  }

  async function retryFailed() {
    setBusy(true);
    setError('');
    try {
      const result = await send<{ retried: number }>(basePath + '/jobs/retry-failed');
      refresh(`已重新提交 ${result.retried} 个作业。`);
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(false); }
  }

  return <>
    <PageTitle title="智能体设置">
      <Button className="button-secondary" onClick={() => refresh()}><RefreshCw size={15} />刷新状态</Button>
    </PageTitle>
    <AgentSettingsTabs />
    <ErrorBox message={status.error || groups.error || error} />
    {status.loading && !data && <Loading />}
    {data && <section aria-label="向量索引概览" className="mb-4 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
      <div className={statClass}>
        <div className="text-xs text-muted">当前生效模式</div>
        <div className="mt-1.5 break-words text-sm font-semibold">{modeLabel[data.effective_mode]}</div>
        <div className="mt-0.5 text-[11px] text-muted">pgvector {data.pgvector.installed ? `v${data.pgvector.version}` : '未安装'} · 自动入队{data.settings.auto_index_enabled ? '开启' : '关闭'}</div>
      </div>
      <div className={statClass}>
        <div className="flex flex-wrap justify-between gap-1 text-xs text-muted"><span>活跃档案覆盖率</span>
          <strong className="text-brand">{Math.round(data.coverage.ratio * 100)}%</strong></div>
        <div className="mt-1.5 text-sm font-semibold">{data.coverage.embedded_chunks} / {data.coverage.total_chunks} 分块</div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e6ece7]">
          <div className="h-full rounded-full bg-brand" style={{ width: `${data.coverage.ratio * 100}%` }} />
        </div>
      </div>
      <div className={statClass}>
        <div className="text-xs text-muted">活跃嵌入档案</div>
        <div className="mt-1.5 truncate text-sm font-semibold" title={active?.profile_id}>{active?.model ?? '暂无活跃档案'}</div>
        {active && <div className="mt-0.5 text-[11px] text-muted">{active.dimensions} 维 · {active.provider} · {active.contextual ? '上下文增强' : '原文嵌入'}</div>}
      </div>
      <div className={statClass}>
        <div className="flex flex-wrap items-center justify-between gap-1 text-xs text-muted">
          <span>异步向量化队列</span>
          {!!queue?.failed && <button className="rounded bg-[#fbeae8] px-1.5 py-0.5 text-[11px] text-danger disabled:opacity-50"
            disabled={busy || !canEmbed} onClick={() => void retryFailed()}>重试失败 ({queue.failed})</button>}
        </div>
        <div className="mt-1.5 text-xs font-semibold">{queue?.pending ?? 0} 待索引 · {queue?.indexing ?? 0} 索引中 · {queue?.failed ?? 0} 失败</div>
        <div className="mt-0.5 text-[11px] text-muted">Celery 事务外盒 · 重启等待手动重试</div>
      </div>
    </section>}
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div className="inline-flex max-w-full flex-wrap rounded-xl border border-line bg-white p-1 text-xs font-medium" role="tablist" aria-label="检索与向量">
        {tabs.map(([value, label]) => <button key={value} id={`rag-tab-${value}`} role="tab"
          aria-controls={`rag-panel-${value}`} aria-selected={tab === value}
          className={`rounded-lg px-3 py-1.5 ${tab === value ? 'bg-brand text-white' : 'text-control hover:bg-canvas'}`}
          onClick={() => setParams(value === 'docs' ? {} : { tab: value })}>{label}</button>)}
      </div>
      {notice && <p role="status" className="m-0 rounded-lg bg-[#e3f1e8] px-3 py-1.5 text-xs text-brand">{notice}</p>}
    </div>
    <div role="tabpanel" id={`rag-panel-${tab}`} aria-labelledby={`rag-tab-${tab}`} className="min-w-0">
      {tab === 'docs' && <CollectionsAssetsTab groups={groups.data ?? []} revision={revision}
        canEmbed={canEmbed} onChange={refresh} onPreview={openAsset} />}
      {tab === 'hittest' && <HitTestingTab groups={groups.data ?? []} settings={data?.settings} onPreview={openAsset} />}
      {tab === 'engine' && <EngineConfigTab data={data} refresh={refresh} />}
    </div>
    {asset && <ChunksPreviewDialog key={assetKey(asset)} asset={asset} revision={revision} canEmbed={canEmbed}
      onClose={() => openAsset(null)} onChange={refresh} />}
  </>;
}
