import { useState } from 'react';
import { Plus } from 'lucide-react';
import { messageOf, send, useResource } from '../../../shared/api/api';
import { ErrorBox, Loading } from '../../../shared/ui/components';
import { Dialog } from '../../../shared/ui/Dialog';
import { CollectionDialog, MembersDialog } from './CollectionDialogs';
import { assetKey, assetPath, basePath, inputClass, primaryClass, secondaryClass, stateLabel,
  type AssetPage, type AssetRef, type Collection, type IndexStatus } from './types';

const filters: Array<IndexStatus | ''> = ['', 'ready', 'pending', 'indexing', 'failed', 'partial', 'unindexed'];
const chip = 'rounded-full px-2 py-0.5 text-[11px]';
const panel = 'min-w-0 rounded-xl border border-line bg-white';
const pageSize = 30;

export function CollectionsAssetsTab({ groups, revision, canEmbed, onChange, onPreview }: {
  groups: Collection[]; revision: number; canEmbed: boolean;
  onChange: (message?: string) => void; onPreview: (asset: AssetRef) => void;
}) {
  const [selection, setSelection] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<IndexStatus | ''>('');
  const [offset, setOffset] = useState(0);
  const [dialog, setDialog] = useState<{ kind: 'create' } | { kind: 'edit' | 'members' | 'delete'; group: Collection } | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const group = groups.find((item) => item.id === selection);
  const params = new URLSearchParams({ query, offset: String(offset), limit: String(pageSize) });
  if (selection) params.set('collection_id', selection);
  if (filter) params.set('status', filter);
  const assets = useResource<AssetPage>(`${basePath}/assets?${params}`, revision);

  function select(id: string) {
    setSelection(id);
    setOffset(0);
  }

  function saved(group: Collection) {
    select(group.id);
    setDialog(null);
    onChange('知识集已保存。');
  }

  async function act(key: string, path: string, notice: string, method = 'POST', body?: unknown) {
    setBusy(key);
    setError('');
    try {
      await send(path, body, method);
      if (method === 'DELETE') { select(''); setDialog(null); }
      onChange(notice);
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(''); }
  }

  return <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
    <aside className={`${panel} p-3.5`}>
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-sm font-semibold">知识集（Collections）</h2>
        <button className={`inline-flex items-center gap-1 ${primaryClass}`} onClick={() => setDialog({ kind: 'create' })}><Plus size={13} />新建</button>
      </div>
      <p className="mb-3 mt-0 text-[11px] leading-relaxed text-muted">用知识集整理已发布文章和材料库资料，供聊天挂载与定向召回测试。</p>
      <div className="grid gap-1" aria-label="知识集列表">
        <button type="button" aria-pressed={!selection}
          className={`rounded-lg px-2.5 py-2 text-left text-xs font-medium ${!selection ? 'bg-[#dceadf] text-brand' : 'hover:bg-canvas'}`}
          onClick={() => select('')}>全部可索引资产</button>
        {groups.map((item) => <button key={item.id} type="button" aria-pressed={selection === item.id}
          className={`min-w-0 rounded-lg px-2.5 py-2 text-left text-xs ${selection === item.id ? 'bg-[#dceadf] text-brand' : 'hover:bg-canvas'}`}
          onClick={() => select(item.id)}>
          <strong className="block break-words">{item.name}</strong>
          <span className="text-muted">{item.asset_count} 篇 · {item.chunks_embedded}/{item.chunks_total} 分块已向量化</span>
        </button>)}
      </div>
    </aside>

    <section className={`${panel} p-4 max-md:p-3`}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 basis-[220px]">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="m-0 break-words text-base font-semibold">{group?.name ?? '全部可索引资产'}</h2>
            {assets.data && <span className={`${chip} bg-canvas text-control`}>{assets.data.total} 篇</span>}
          </div>
          <p className="mb-0 mt-0.5 break-words text-xs text-muted">{group
            ? group.description : '已发布知识文章与已保存到材料库的资料。'}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {group && <>
            <button className={secondaryClass} onClick={() => setDialog({ kind: 'members', group })}>管理成员</button>
            <button className={secondaryClass} onClick={() => setDialog({ kind: 'edit', group })}>编辑</button>
            <button className={`${secondaryClass} text-danger`} onClick={() => setDialog({ kind: 'delete', group })}>删除知识集</button>
          </>}
          <button className={secondaryClass} disabled={Boolean(busy) || !canEmbed}
            onClick={() => void act('rebuild', basePath + '/rebuild', '已完成向量增量补建。', 'POST', { activate: true })}>
            {busy === 'rebuild' ? '补建中…' : '增量补建未索引'}
          </button>
        </div>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-2 border-b border-line pb-3">
        <input type="search" aria-label="搜索可索引资产" placeholder="搜索文档标题、分类或来源…" value={query} maxLength={500}
          onChange={(event) => { setQuery(event.target.value); setOffset(0); }} className={`${inputClass} w-full max-w-[260px]`} />
        <div className="flex flex-wrap gap-1.5" aria-label="索引状态筛选">
          {filters.map((value) => <button key={value} aria-pressed={value === filter}
            className={`${chip} ${value === filter ? 'bg-brand text-white' : 'bg-canvas text-control'}`}
            onClick={() => { setFilter(value); setOffset(0); }}>{value ? stateLabel[value] : '全部状态'}</button>)}
        </div>
      </div>
      <ErrorBox message={error || assets.error} />
      {assets.loading && !assets.data && <Loading />}
      {assets.data?.total === 0 && <p className="py-4 text-center text-xs text-muted">没有匹配的资产。</p>}
      <div className="divide-y divide-line">
        {assets.data?.items.map((asset) => <article key={assetKey(asset)} className="flex flex-wrap items-center justify-between gap-2 py-3">
          <div className="min-w-0 flex-1 basis-[220px]">
            <div className="flex flex-wrap items-center gap-1.5">
              <h3 className="m-0 min-w-0 break-words text-sm font-semibold">{asset.title}</h3>
              <span className={`${chip} ${asset.index_status === 'failed' ? 'bg-[#fbeae8] text-danger' :
                asset.index_status === 'ready' ? 'bg-[#e3f1e8] text-brand' : 'bg-canvas text-control'}`}>{stateLabel[asset.index_status]}</span>
            </div>
            <div className="mt-1 text-xs text-muted">{asset.asset_kind === 'knowledge' ? '知识文章' : '材料库'} · V{asset.version} · {asset.subtitle}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
              <span>{asset.chunks_embedded} / {asset.chunks_total} 分块已向量化</span>
              {asset.chunks_total > asset.chunks_enabled && <span className="text-muted">· {asset.chunks_total - asset.chunks_enabled} 已排除</span>}
              {asset.collections.map(({ id, name }) => <span className={`${chip} max-w-full break-words bg-canvas text-brand`} key={id}>{name}</span>)}
            </div>
            {asset.last_error && <p className="mb-0 mt-1 break-words text-xs text-danger">{asset.last_error}</p>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button className={secondaryClass} onClick={() => onPreview(asset)}>分块预览 ({asset.chunks_total})</button>
            <button className={secondaryClass} disabled={Boolean(busy) || !canEmbed || ['pending', 'indexing'].includes(asset.index_status)}
              onClick={() => void act(assetKey(asset), assetPath(asset) + '/reindex', '已提交单篇向量索引作业。')}>
              {asset.index_status === 'failed' ? '重试' : '重算向量'}
            </button>
          </div>
        </article>)}
      </div>
      {assets.data && (offset > 0 || offset + pageSize < assets.data.total) && <div className="flex justify-end gap-2 border-t border-line pt-3">
        <button className={secondaryClass} disabled={!offset} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一页</button>
        <button className={secondaryClass} disabled={offset + pageSize >= assets.data.total} onClick={() => setOffset(offset + pageSize)}>下一页</button>
      </div>}
    </section>
    {(dialog?.kind === 'create' || dialog?.kind === 'edit') && <CollectionDialog group={dialog.kind === 'edit' ? dialog.group : undefined}
      onSave={saved} onClose={() => setDialog(null)} />}
    {dialog?.kind === 'members' && <MembersDialog group={dialog.group} onSave={saved} onClose={() => setDialog(null)} />}
    {dialog?.kind === 'delete' && <Dialog title="删除知识集" busy={Boolean(busy)} onClose={() => setDialog(null)}
      footer={<><button className={secondaryClass} disabled={Boolean(busy)} onClick={() => setDialog(null)}>取消</button>
        <button className={primaryClass} disabled={Boolean(busy)}
          onClick={() => void act(dialog.group.id, `${basePath}/collections/${dialog.group.id}?expected_revision=${dialog.group.revision}`, '已删除知识集。', 'DELETE')}>
          确认删除</button></>}>
      <p className="break-words text-sm">删除「{dialog.group.name}」的分组和成员关系。原文、版本和向量仍可使用。</p>
      <ErrorBox message={error} />
    </Dialog>}
  </div>;
}
