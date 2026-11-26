import { useEffect, useState } from 'react';
import { api, messageOf, send, useResource } from '../../../shared/api/api';
import { ErrorBox, Loading } from '../../../shared/ui/components';
import { Dialog } from '../../../shared/ui/Dialog';
import { assetKey, basePath, inputClass, primaryClass, secondaryClass, type AssetPage, type AssetRef, type Collection } from './types';

type Saved = (group: Collection) => void;
const pageSize = 30;

export function CollectionDialog({ group, onSave, onClose }: { group?: Collection; onSave: Saved; onClose: () => void }) {
  const [name, setName] = useState(group?.name ?? '');
  const [description, setDescription] = useState(group?.description ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function save() {
    setBusy(true);
    setError('');
    try {
      const values = { name: name.trim(), description: description.trim() };
      const saved = group
        ? await send<Collection>(`${basePath}/collections/${group.id}`, { ...values, expected_revision: group.revision }, 'PATCH')
        : await send<Collection>(basePath + '/collections', values);
      onSave(saved);
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(false); }
  }

  return <Dialog title={group ? '编辑知识集' : '新建知识集'} onClose={onClose} busy={busy}
    footer={<>
      <button type="button" className={secondaryClass} disabled={busy} onClick={onClose}>取消</button>
      <button type="submit" form="rag-collection" className={primaryClass} disabled={busy || !name.trim()}>
        {group ? '保存知识集' : '创建知识集'}
      </button>
    </>}>
    <form id="rag-collection" className="grid gap-3" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <ErrorBox message={error} />
      <label className="grid gap-1 text-xs">知识集名称
        <input autoFocus value={name} onChange={(event) => setName(event.target.value)} maxLength={120} required className={inputClass} />
      </label>
      <label className="grid gap-1 text-xs">用途说明（可选）
        <textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} rows={2} className={inputClass} />
      </label>
    </form>
  </Dialog>;
}

export function MembersDialog({ group, onSave, onClose }: { group: Collection; onSave: Saved; onClose: () => void }) {
  const [selected, setSelected] = useState<Map<string, AssetRef> | null>(null);
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const params = new URLSearchParams({ query, offset: String(offset), limit: String(pageSize) });
  const assets = useResource<AssetPage>(`${basePath}/assets?${params}`);
  useEffect(() => {
    const controller = new AbortController();
    const initial = new Map<string, AssetRef>();
    async function load() {
      const params = new URLSearchParams({ collection_id: group.id, limit: '100' });
      let offset = 0;
      while (true) {
        params.set('offset', String(offset));
        const page = await api<AssetPage>(`${basePath}/assets?${params}`, { signal: controller.signal });
        for (const { asset_kind, asset_id } of page.items) initial.set(assetKey({ asset_kind, asset_id }), { asset_kind, asset_id });
        offset += page.items.length;
        if (!page.items.length || offset >= page.total) break;
      }
      if (!controller.signal.aborted) setSelected(initial);
    }
    void load().catch((failure) => { if (!controller.signal.aborted) setError(messageOf(failure)); });
    return () => controller.abort();
  }, [group.id]);

  async function save() {
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      onSave(await send<Collection>(`${basePath}/collections/${group.id}/items`, {
        expected_revision: group.revision, items: [...selected.values()],
      }, 'PUT'));
    } catch (failure) { setError(messageOf(failure)); }
    finally { setBusy(false); }
  }

  return <Dialog title={`管理知识集成员 · ${group.name}`} onClose={onClose} busy={busy} scrollBody={false}
    footer={<>
      <button type="button" className={secondaryClass} onClick={onClose} disabled={busy}>取消</button>
      <button type="button" className={primaryClass} onClick={() => void save()} disabled={busy || !selected}>保存成员</button>
    </>}>
    <div className="shrink-0 border-b border-line bg-[#fafcfa] px-5 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input type="search" aria-label="搜索可加入的资产" placeholder="搜索知识文章和材料…" value={query} maxLength={500}
          onChange={(event) => { setQuery(event.target.value); setOffset(0); }} className={`${inputClass} w-full max-w-[260px] bg-white`} />
        <span className="text-xs text-muted">已选 {selected?.size ?? group.asset_count} / 500</span>
      </div>
      <ErrorBox message={error || assets.error} />
    </div>
    <div aria-label="知识集成员列表" className="min-h-0 flex-1 divide-y divide-line overflow-y-auto overscroll-contain px-5 py-2">
      {assets.loading && !assets.data && <Loading />}
      {assets.data?.items.map(({ asset_kind, asset_id, title, subtitle, version }) => {
        const ref = { asset_kind, asset_id };
        const key = assetKey(ref);
        const checked = selected?.has(key) ?? false;
        return <label key={key} className="check m-0 flex-row items-start gap-2.5 py-2.5 font-normal">
          <input type="checkbox" className="mt-0.5 accent-brand" checked={checked}
            disabled={!selected || busy || (!checked && selected.size >= 500)}
            onChange={(event) => {
              if (!selected) return;
              const next = new Map(selected);
              if (event.target.checked) next.set(key, ref);
              else next.delete(key);
              setSelected(next);
            }} />
          <span className="min-w-0 break-words text-xs"><strong className="block text-ink">{title}</strong>
            <span className="text-muted">{asset_kind === 'knowledge' ? '知识文章' : '材料库'} · V{version} · {subtitle}</span></span>
        </label>;
      })}
      {assets.data?.total === 0 && <p className="text-xs text-muted">没有匹配的可用资产。</p>}
      {assets.data && (offset > 0 || offset + pageSize < assets.data.total) && <div className="flex justify-end gap-2 py-2">
        <button className={secondaryClass} disabled={!offset} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一页</button>
        <button className={secondaryClass} disabled={offset + pageSize >= assets.data.total} onClick={() => setOffset(offset + pageSize)}>下一页</button>
      </div>}
    </div>
  </Dialog>;
}
