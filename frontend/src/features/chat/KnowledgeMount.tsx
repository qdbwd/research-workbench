import { useEffect, useId, useRef, useState } from 'react';
import { BookOpen, ChevronDown, X } from 'lucide-react';
import type { Collection, RagScope } from '../tools/retrieval/types';

export type MountProps = {
  scope: RagScope;
  ids: string[];
  groups: Collection[];
  loading?: boolean;
  error?: string;
  trial?: boolean;
  disabled?: boolean;
  onChange: (scope: RagScope, ids: string[]) => void;
};

export function MountedKnowledgeChips({ scope, ids, groups, trial, disabled, onChange }: MountProps) {
  if (scope === 'auto' || trial) return null;
  const entries = scope === 'all' ? [{ id: '', name: '全库' }] : ids.map((id) => ({
    id, name: groups.find((group) => group.id === id)?.name ?? '知识集',
  }));
  return <div className="mb-2 flex min-w-0 flex-wrap gap-1.5" aria-label="已挂载知识库">
    {entries.map(({ id, name }) => <button key={id} type="button" disabled={disabled}
      className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-[#e3f1e8] px-2 py-1 text-xs text-brand disabled:opacity-50"
      aria-label={`移除挂载 ${name}`} onClick={() => {
        const next = ids.filter((identity) => identity !== id);
        onChange(scope === 'collections' && next.length ? 'collections' : 'auto', scope === 'collections' ? next : []);
      }}>
      <BookOpen size={12} className="shrink-0" /><span className="truncate">{name}</span><X size={12} className="shrink-0" />
    </button>)}
  </div>;
}

export function KnowledgeMount({ scope, ids, groups, loading, error, trial, disabled, onChange }: MountProps) {
  const [open, setOpen] = useState(false);
  const [expand, setExpand] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const visible = open && !trial && !disabled;
  useEffect(() => {
    if (!visible) return;
    function outside(event: PointerEvent) {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false);
        trigger.current?.focus();
      }
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [visible]);

  function pick(next: 'auto' | 'all') {
    onChange(next, []);
    setOpen(false);
    trigger.current?.focus();
  }

  return <div ref={box} className="relative min-w-0">
    <button ref={trigger} type="button" aria-expanded={visible} aria-controls={visible ? menuId : undefined}
      title={trial ? '试用会话使用固定资料，无法挂载知识库。' : undefined} disabled={trial || disabled}
      className={`inline-flex h-8 min-w-0 items-center gap-1 rounded-full border px-2 text-xs font-medium max-md:text-[11px]
        ${scope !== 'auto' && !trial ? 'border-[#b9d8c0] bg-[#e3f1e8] text-brand' : 'border-line bg-white text-control'} disabled:opacity-50`}
      onClick={() => { setExpand(scope === 'collections'); setOpen(!visible); }}>
      <BookOpen size={13} className="shrink-0" /><span>挂载知识库</span><ChevronDown size={12} className="shrink-0" />
    </button>
    {trial && <span className="sr-only">试用会话使用固定资料，无法挂载知识库。</span>}
    {visible && <div id={menuId} role="region" aria-label="知识库挂载范围"
      className="absolute bottom-[calc(100%+8px)] left-0 z-20 grid max-h-[min(56dvh,375px)] w-[min(306px,calc(100vw-82px))] gap-1 overflow-y-auto rounded-xl border border-line bg-white p-2 shadow-xl">
      {([
        ['auto', '按需检索', '需要资料时由 Agent 决定'],
        ['all', '一键挂载全库', '优先检索已发布知识与材料'],
      ] as const).map(([mode, label, description]) => {
        const selected = scope === mode;
        return <button key={mode} type="button" aria-pressed={selected}
          className={`rounded-lg p-2 text-left text-xs ${selected ? 'bg-[#e3f1e8] text-brand' : 'hover:bg-canvas'}`}
          onClick={() => pick(mode)}>
          <span className="flex items-center justify-between gap-2">
            <strong>{label}</strong>
            {selected && <span aria-hidden="true">✓</span>}
          </span>
          <span className="block text-muted">{description}</span>
        </button>;
      })}
      <button type="button" aria-expanded={expand} className="rounded-lg p-2 text-left text-xs hover:bg-canvas"
        onClick={() => setExpand(!expand)}><strong>按知识集多选</strong></button>
      {expand && <div className="grid gap-1 rounded-lg bg-canvas p-2">
        {loading && <p className="m-0 text-xs text-muted">正在读取知识集…</p>}
        {error && <p role="alert" className="m-0 text-xs text-danger">{error}</p>}
        {!loading && !error && !groups.length && <p className="m-0 text-xs text-muted">还没有知识集。</p>}
        {groups.map((group) => {
          const checked = scope === 'collections' && ids.includes(group.id);
          return <label key={group.id} className="check m-0 flex-row items-start gap-1.5 px-1 py-1 font-normal">
            <input type="checkbox" checked={checked} disabled={!checked && ids.length >= 10}
              onChange={(event) => {
                const next = event.target.checked ? [...ids, group.id] : ids.filter((id) => id !== group.id);
                onChange(next.length ? 'collections' : 'auto', next);
              }} />
            <span className="min-w-0 break-words text-xs"><strong className="block">{group.name}</strong>
              <span className="text-muted">{group.asset_count} 篇 · {group.chunks_embedded}/{group.chunks_total} 分块已向量化</span></span>
          </label>;
        })}
      </div>}
    </div>}
  </div>;
}
