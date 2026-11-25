import { useState } from 'react';
import { GitCompareArrows, Map as MapIcon, Plus } from 'lucide-react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox } from '../../shared/ui/components';
import { BackLink, WorkbenchHeading, WorkbenchPanel } from '../../shared/ui/Workbench';
import { researchApi, researchPath, revisionSuffix, type ResearchTask, type RevisionSummary } from './api';
import { nodeStatuses, type AdoptionPreview, type MapComparison, type ResearchMap, type ResearchNode } from './mapApi';
import { ResearchAdoption } from './ResearchAdoption';
import { ResearchMapCanvas } from './ResearchCommitMap';
import { ResearchNodeEditor, type ResearchNodeDraft } from './ResearchNodeEditor';

function NodeDetail({ node, version, nodes, onSelect, metadataOnly = false }: {
  node?: ResearchNode;
  version?: number;
  nodes: ResearchNode[];
  onSelect?: (id: string) => void;
  metadataOnly?: boolean;
}) {
  if (!node) return <p className="m-0 text-sm text-control">这个版本没有该主题。</p>;
  return <article className="grid min-w-0 gap-4">
    <header>
      <p className="mb-1 text-xs text-control">{version != null && `V${version} · `}{nodeStatuses[node.status]}{!node.required && ' · 可选探索'}</p>
      <h3 className="m-0 text-lg font-semibold [overflow-wrap:anywhere]">{node.question}</h3>
    </header>
    {!metadataOnly && ([
      ['解决标准', node.criteria], ['结论', node.conclusion], ['判断理由', node.reason], ['详情', node.details],
    ] as const).filter(([, value]) => Boolean(value)).map(([label, value]) => <section key={label}>
      <h4 className="m-0 text-sm font-semibold">{label}</h4>
      <p className="mt-1 mb-0 text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">{value}</p>
    </section>)}
    {node.evidence.length > 0 && <section>
      <h4 className="m-0 text-sm font-semibold">原始依据</h4>
      <ul className="mt-2 mb-0 grid gap-2 pl-4 text-sm">
        {node.evidence.map(item => <li key={item.id}>
          {item.url ? <Link to={item.url}>{item.title ?? '打开固定证据'}</Link> : <strong>固定证据无法读取</strong>}
          {item.available && <p className="mt-1 mb-0 text-control [overflow-wrap:anywhere]">
            {item.page ? `第 ${item.page} 页 · ` : ''}
            {item.offset_unit === 'lines' ? `${(item.start ?? 0) + 1}–${item.end} 行` : `${item.start}–${item.end} 字符`}
            {' · '}{item.quote}
          </p>}
        </li>)}
      </ul>
    </section>}
    {node.related.length > 0 && <section>
      <h4 className="m-0 text-sm font-semibold">关联主题</h4>
      <ul className="mt-2 mb-0 grid gap-2 pl-4 text-sm">{node.related.map(edge => {
        const target = nodes.find(row => row.id === edge.target_id);
        return <li key={edge.target_id}>
          {onSelect ? <button type="button" className="button-quiet" onClick={() => onSelect(edge.target_id)}>
            {target?.question ?? '关联主题'}
          </button> : <strong>{target?.question ?? '关联主题'}</strong>}{' · '}{edge.description}
        </li>;
      })}</ul>
    </section>}
  </article>;
}

function comparisonTree(diff: MapComparison, changedOnly: boolean) {
  const combined = new Map(diff.after.nodes.map(node => [node.id, node]));
  for (const removed of diff.before.nodes) {
    if (!combined.has(removed.id)) combined.set(removed.id, removed);
  }
  const visible = new Set(diff.changes.map(item => item.id));
  if (changedOnly) {
    for (const id of [...visible]) {
      let parent = combined.get(id)?.parent_id;
      while (parent && !visible.has(parent)) {
        visible.add(parent);
        parent = combined.get(parent)?.parent_id;
      }
    }
  }
  return [...combined.values()].filter(node => !changedOnly || visible.has(node.id));
}

function ResearchMapScreen({ taskId, query, setQuery, initialNodeId, embedded = false }: {
  taskId: string; query: URLSearchParams; setQuery: (next: Record<string, string>) => void;
  initialNodeId?: string; embedded?: boolean;
}) {
  const comparing = query.has('before') && query.has('after');
  const before = query.get('before') ?? '';
  const after = query.get('after') ?? '';
  const fixed = query.get('revision');
  const [refresh, setRefresh] = useState(0);
  const [selectedId, setSelected] = useState(initialNodeId ?? query.get('node') ?? undefined);
  const [edit, setEdit] = useState<{ node: ResearchNode | null; remove?: boolean } | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ResearchNodeDraft>>({});
  const [editorNonce, setEditorNonce] = useState(0);
  const [adoption, setAdoption] = useState<AdoptionPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [error, setError] = useState('');
  const [onlyChanges, setOnlyChanges] = useState(true);
  const base = researchApi(taskId);
  const task = useResource<ResearchTask>(base, refresh);
  const map = useResource<ResearchMap>(comparing ? null : base + '/map' + (
    fixed ? `?revision_id=${encodeURIComponent(fixed)}` : ''
  ), refresh);
  const branch = task.data?.write_branch_id ?? task.data?.main_branch_id;
  const versions = useResource<{ items: RevisionSummary[] }>(
    branch ? base + revisionSuffix(branch) + '?limit=100' : null, refresh,
  );
  const diff = useResource<MapComparison>(comparing
    ? `${base}/map/compare?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`
    : null);
  const working = useResource<{ difference: { map: MapComparison } | null }>(
    branch && !fixed && !comparing ? `${base}/branches/${encodeURIComponent(branch)}/working-diff` : null, refresh,
  );
  const workingMap = working.data?.difference?.map;
  const activeDiff = diff.data ?? (workingMap?.after.revision_id === map.data?.revision_id ? workingMap : undefined);
  const differences = new Map(activeDiff?.changes.map(row => [row.id, row.types]));
  const nodes = activeDiff ? comparisonTree(activeDiff, comparing && onlyChanges) : map.data?.nodes ?? [];
  const selected = nodes.find(node => node.id === selectedId) ?? (
    diff.data ? nodes.find(node => differences.has(node.id)) : null
  ) ?? nodes[0];
  const selectedBefore = diff.data?.before.nodes.find(node => node.id === selected?.id);
  const selectedAfter = diff.data?.after.nodes.find(node => node.id === selected?.id);
  const adopted = selected ? map.data?.node_adoptions?.[selected.id] : undefined;
  const editable = !fixed && !comparing && task.data?.status === 'open' && task.data.head.id === map.data?.revision_id;
  const removed = selected ? differences.get(selected.id)?.includes('removed') : false;
  const draftKey = branch && selected ? `${branch}:${selected.id}` : '';
  const draft = drafts[draftKey];
  const inline = editable && !removed;

  function clearDraft() {
    setDrafts((current) => {
      const next = { ...current };
      delete next[draftKey];
      return next;
    });
    setEditorNonce(value => value + 1);
  }

  function chooseVersions(left: string, right: string) {
    setQuery({ before: left, after: right });
    setError('');
  }

  function afterWrite() {
    setRefresh(value => value + 1);
    setEdit(null);
    setAdoption(null);
  }

  async function preview() {
    if (!task.data || !selectedBefore || previewBusy) return;
    setPreviewBusy(true);
    setError('');
    try {
      setAdoption(await send<AdoptionPreview>(base + '/map/adoption-preview', {
        source_revision_id: before, node_id: selectedBefore.id,
        expected_revision_id: task.data.head.id,
        expected_control_version: task.data.control_version,
      }));
    } catch (failure) {
      setError(messageOf(failure));
      setRefresh(value => value + 1);
    } finally {
      setPreviewBusy(false);
    }
  }

  function versionOptions() {
    return versions.data?.items.map(row => <option key={row.id} value={row.id}>V{row.number} · {row.reason}</option>);
  }

  return <div className="mx-auto max-w-6xl">
    {!embedded && <BackLink to={researchPath(taskId)}>返回研发任务</BackLink>}
    <WorkbenchHeading title="研究地图" eyebrow={task.data?.title ?? '研发工作台'} actions={<>
      {(comparing || fixed) && <Button type="button" className="button-secondary" onClick={() => setQuery({})}>当前进展</Button>}
      {!comparing && <Button type="button" className="button-secondary" disabled={!versions.data?.items.length}
        onClick={() => {
          const available = versions.data?.items;
          if (available?.length) chooseVersions(available[1]?.id ?? available[0].id, available[0].id);
        }}><GitCompareArrows size={16} aria-hidden="true" /> 版本对比</Button>}
      {editable && <Button type="button" onClick={() => setEdit({ node: null })}>
        <Plus size={16} aria-hidden="true" /> 新增主题
      </Button>}
    </>} />
    {comparing && <div className="mb-5 grid items-end gap-3 rounded-xl border border-line bg-white p-4 sm:grid-cols-[1fr_1fr_auto]">
      <label className="grid min-w-0 gap-1.5 text-sm font-semibold">起始版本
        <select value={before} onChange={event => chooseVersions(event.target.value, after)}>{versionOptions()}</select>
      </label>
      <label className="grid min-w-0 gap-1.5 text-sm font-semibold">对照版本
        <select value={after} onChange={event => chooseVersions(before, event.target.value)}>{versionOptions()}</select>
      </label>
      <label className="flex items-center gap-2 py-2 text-sm"><input type="checkbox" checked={onlyChanges}
        onChange={event => setOnlyChanges(event.target.checked)} /> 只看差异</label>
    </div>}
    <ErrorBox message={error || task.error || map.error || diff.error || versions.error || working.error} />
    <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <WorkbenchPanel title={comparing ? '主题差异' : '研究主题'} description={map.data ? `版本 ${map.data.number}` : undefined}>
        {task.data && !map.loading && !diff.loading && <ResearchMapCanvas nodes={nodes} taskTitle={task.data.title}
          changes={differences} selectedId={selected?.id} onSelect={setSelected} />}
        {!nodes.length && <p className="flex items-center gap-2 text-sm text-control">
          <MapIcon size={16} aria-hidden="true" />
          {map.loading || diff.loading ? '正在读取地图…' : comparing ? '所选版本没有地图差异。' : '研究地图还没有主题。'}
        </p>}
      </WorkbenchPanel>
      {selected && <WorkbenchPanel title={comparing ? '版本详情' : '主题详情'}>
        {comparing && diff.data ? <>
          {selectedBefore && selectedAfter && selectedBefore.parent_id !== selectedAfter.parent_id && <p className="mb-4 text-sm text-control">
            {diff.data.before.nodes.find(node => node.id === selectedBefore.parent_id)?.question ?? '顶层主题'}
            {' → '}
            {diff.data.after.nodes.find(node => node.id === selectedAfter.parent_id)?.question ?? '顶层主题'}
          </p>}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="min-w-0 rounded-xl border border-line p-4"><NodeDetail node={selectedBefore}
              version={diff.data.before.number} nodes={diff.data.before.nodes} /></div>
            <div className="min-w-0 rounded-xl border border-line p-4"><NodeDetail node={selectedAfter}
              version={diff.data.after.number} nodes={diff.data.after.nodes} /></div>
          </div>
          {task.data?.status === 'open' && selectedBefore?.conclusion && before !== task.data.head.id &&
            <Button type="button" className="button-secondary mt-4" disabled={previewBusy} onClick={() => void preview()}>预览采纳历史结论</Button>}
        </> : <>
          {removed && <p className="mb-2 text-xs font-medium text-danger">已删除 · 历史详情</p>}
          <NodeDetail node={selected} nodes={nodes} onSelect={setSelected} metadataOnly={inline} />
          {adopted && <p className="mt-2 mb-0 text-xs text-control">
            来源提交 {adopted.research_commit_id.slice(0, 8)} · <Link target="_blank" rel="noreferrer"
              to={researchPath(taskId) + '/map?' + new URLSearchParams({ revision: adopted.research_revision_id, node: adopted.node_id })}>
              查看固定来源地图
            </Link>
          </p>}
          {inline && task.data && map.data && <div className="mt-4 grid gap-4 border-t border-line pt-4">
            <ResearchNodeEditor key={`${draftKey}:${draft?.binding.revision ?? map.data.revision_id}:${editorNonce}`}
              task={task.data} map={map.data} node={selected} inline draft={draft}
              onDraft={value => setDrafts(current => ({ ...current, [draftKey]: value }))}
              onSaved={() => { clearDraft(); afterWrite(); }} onClose={clearDraft}
              onConflict={() => setRefresh(value => value + 1)} />
            <Button type="button" className="button-quiet justify-self-start text-danger" onClick={() => setEdit({ node: selected, remove: true })}>移除主题</Button>
          </div>}
        </>}
      </WorkbenchPanel>}
    </div>
    {edit && task.data && map.data && <ResearchNodeEditor task={task.data} map={map.data} node={edit.node}
      remove={edit.remove} onSaved={() => { if (edit.remove) clearDraft(); afterWrite(); }}
      onClose={() => setEdit(null)} onConflict={() => setRefresh(value => value + 1)} />}
    {adoption && task.data && <ResearchAdoption task={task.data} preview={adoption}
      onClose={() => setAdoption(null)} onSaved={afterWrite} />}
    {comparing && task.data && after !== task.data.head.id && <p className="mt-4 text-sm text-control">
      对照保留当前所选版本。{' '}
      {embedded ? <button type="button" className="button-quiet" onClick={() => chooseVersions(before, task.data!.head.id)}>将右侧切换到最新进展</button>
        : <Link to={`?before=${encodeURIComponent(before)}&after=${encodeURIComponent(task.data.head.id)}`}>将右侧切换到最新进展</Link>}
    </p>}
  </div>;
}

export function ResearchMapPage() {
  const { taskId } = useParams();
  const [query, setQuery] = useSearchParams();
  return taskId ? <ResearchMapScreen key={taskId} taskId={taskId} query={query} setQuery={setQuery} /> : null;
}

/** 会话内浏览地图使用局部参数，避免改变会话锚点或丢失草稿。 */
export function EmbeddedResearchMap({ taskId, initialNodeId }: { taskId: string; initialNodeId?: string }) {
  const [query, setQuery] = useState<Record<string, string>>({});
  return <ResearchMapScreen taskId={taskId} query={new URLSearchParams(query)} setQuery={setQuery} initialNodeId={initialNodeId} embedded />;
}
