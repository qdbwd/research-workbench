import { useEffect, useId, useRef, useState } from 'react';
import { ArrowLeft, FileText, X } from 'lucide-react';
import { useResource } from '../../shared/api/api';
import type { Evidence } from '../../shared/types/types';
import { Button } from '../../shared/ui/Button';
import { ErrorBox, Loading } from '../../shared/ui/components';

export type EvidenceTab = { id: string; label: string };

/** 窄屏让资料占据当前内容区，底下的会话与草稿保持挂载。 */
export function useNarrowChatPane() {
  const [narrow, setNarrow] = useState(() => window.matchMedia?.('(width < 64rem)').matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.('(width < 64rem)');
    if (!media) return;
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return narrow;
}

/** 文档坐标由后端按 Unicode 字符计算；只有固定正文吻合时才展示定位上下文。 */
function FixedPassage({ evidence }: { evidence: Evidence }) {
  const [whole, setWhole] = useState(false);
  const mark = useRef<HTMLElement>(null);
  const documentPath = evidence.document_id && evidence.knowledge_version_id
    ? `/api/knowledge/${encodeURIComponent(evidence.document_id)}?version_id=${encodeURIComponent(evidence.knowledge_version_id)}`
    : null;
  const sourcePath = evidence.source_version_id
    ? `/api/source-versions/${encodeURIComponent(evidence.source_version_id)}/content` : null;
  const current = useResource<{ body?: string; text?: string; segments?: { id: string; text: string }[] }>(
    documentPath ?? sourcePath,
  );
  const value = evidence.segment_id
    ? current.data?.segments?.find((segment) => segment.id === evidence.segment_id)?.text
    : current.data?.body ?? current.data?.text;
  const characters = value === undefined ? null : Array.from(value);
  const { start, end, text } = evidence;
  const positioned = characters !== null && start >= 0 && end <= characters.length && end > start
    && characters.slice(start, end).join('') === text;
  useEffect(() => {
    if (whole && positioned) mark.current?.scrollIntoView({ block: 'center' });
  }, [whole, positioned]);

  return <>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <p className="m-0 text-xs text-control">
        {evidence.version != null && <>版本 {evidence.version} · </>}
        {evidence.page != null && <>第 {evidence.page} 页 · </>}
        原文第 {start + 1}–{end} 字符
      </p>
      {positioned && <Button className="button-quiet text-xs" onClick={() => setWhole(!whole)}>
        {whole ? '仅看引用附近' : evidence.page == null ? '展开完整正文' : '展开本页正文'}
      </Button>}
    </div>
    <p className="m-0 whitespace-pre-wrap text-sm leading-[1.85] [overflow-wrap:anywhere]">
      {positioned && characters.slice(whole ? 0 : Math.max(0, start - 300), start).join('')}
      <mark ref={mark} className="rounded-sm bg-[#fbefc9] text-inherit">{text}</mark>
      {positioned && characters.slice(end, whole ? undefined : end + 300).join('')}
    </p>
    {current.error && <p role="status" className="mt-3 text-xs text-control">完整版本暂不可读。以上为引用时保存的原文。</p>}
  </>;
}

export function ChatEvidencePane({ tabs, active, onActivate, onClose, onReturn, narrow }: {
  tabs: EvidenceTab[];
  active: string;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onReturn: () => void;
  narrow: boolean;
}) {
  const uid = useId();
  const tablist = useRef<HTMLDivElement>(null);
  const result = useResource<Evidence>(`/api/evidence/${encodeURIComponent(active)}`);
  useEffect(() => {
    const selected = tablist.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]');
    selected?.focus({ preventScroll: true });
    selected?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [active]);

  return <section aria-label="资料查看器" className="chat-evidence-pane flex min-h-0 min-w-0 flex-col border-line bg-white"
    onKeyDown={(event) => { if (event.key === 'Escape' && narrow) onReturn(); }}>
    <div className="flex min-w-0 items-center gap-2 border-b border-line px-3 py-2">
      {narrow && <Button className="button-secondary shrink-0 text-xs" onClick={onReturn}>
        <ArrowLeft size={14} aria-hidden="true" /> 返回会话
      </Button>}
      <div ref={tablist} role="tablist" aria-label="打开的资料" className="scrollbar-subtle flex min-w-0 flex-1 gap-1 overflow-x-auto px-1 py-1"
        onKeyDown={(event) => {
          if ((event.target as HTMLElement).getAttribute('role') !== 'tab') return;
          const index = tabs.findIndex((tab) => tab.id === active);
          const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
            : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
            : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
          if (next === null) return;
          event.preventDefault();
          onActivate(tabs[next].id);
        }}>
        {tabs.map((tab) => <div key={tab.id} className="evidence-tab flex min-w-0 max-w-48 shrink-0 items-center rounded-lg"
          data-active={active === tab.id ? 'true' : undefined}>
          <button type="button" role="tab" id={`${uid}-${tab.id}`} aria-controls={`${uid}-panel`}
            aria-selected={active === tab.id} tabIndex={active === tab.id ? 0 : -1} title={tab.label}
            className="flex min-w-0 items-center gap-1.5 rounded-lg py-1.5 pl-2 pr-1 text-xs outline-offset-[-2px]"
            onClick={() => onActivate(tab.id)}><FileText size={14} className="shrink-0" aria-hidden="true" />
            <span className="truncate">{tab.label}</span>
          </button>
          <button type="button" className="mr-1 rounded-md p-1 text-control hover:bg-sidebar" aria-label={`关闭 ${tab.label}`}
            onClick={() => onClose(tab.id)}><X size={13} aria-hidden="true" /></button>
        </div>)}
      </div>
    </div>
    <div key={active} role="tabpanel" id={`${uid}-panel`} aria-labelledby={`${uid}-${active}`} tabIndex={0}
      className="scrollbar-subtle min-h-0 flex-1 overflow-auto overscroll-contain p-5 max-md:p-4">
      {result.loading && <Loading />}
      <ErrorBox message={result.error} />
      {result.data && <>
        <h2 className="mt-0 mb-2 text-lg font-semibold [overflow-wrap:anywhere]">{result.data.title}</h2>
        <FixedPassage evidence={result.data} />
      </>}
    </div>
  </section>;
}
