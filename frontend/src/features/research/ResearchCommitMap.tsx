import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { WorkbenchPanel } from '../../shared/ui/Workbench';
import { nodeStatuses, type MapComparison, type MapEvidence, type ResearchMap, type ResearchNode } from './mapApi';

const diameter = 24;
const column = 155;
const row = 112;
const mapChanges: Record<string, string> = { added: '新增', removed: '删除', moved: '移动', conclusion: '结论', status: '状态', evidence: '证据', description: '内容' };

function layout(nodes: ResearchNode[]) {
  const byParent = new Map<string | null, ResearchNode[]>();
  for (const node of nodes) byParent.set(node.parent_id, [...(byParent.get(node.parent_id) ?? []), node]);
  const points = new Map<string, { x: number; y: number }>();
  let leaves = 0;
  let depth = 0;
  function place(node: ResearchNode, level: number): number {
    depth = Math.max(depth, level);
    const children = (byParent.get(node.id) ?? []).map((child) => place(child, level + 1));
    const x = children.length ? (children[0] + children[children.length - 1]) / 2 : column * (leaves++ + 0.5);
    points.set(node.id, { x, y: 70 + row * level });
    return x;
  }
  const roots = (byParent.get(null) ?? []).map((node) => place(node, 1));
  const root = { x: roots.length ? (roots[0] + roots[roots.length - 1]) / 2 : column / 2, y: 70 };
  return { points, root, width: Math.max(1, leaves) * column, height: depth * row + 102 };
}

export function ResearchMapCanvas({ nodes, taskTitle, changes, selectedId, onSelect }: {
  nodes: ResearchNode[]; taskTitle: string; changes: Map<string, string[]>;
  selectedId?: string; onSelect: (id: string) => void;
}) {
  const { points, root, width, height } = layout(nodes);
  return <section className="scrollbar-subtle max-h-[65vh] min-w-0 overflow-auto rounded-xl border border-line bg-canvas"
    aria-label="研发地图" tabIndex={0}>
        <div className="relative mx-auto" style={{ width, height }}>
          {nodes.map((node) => {
            if (changes.get(node.id)?.includes('removed') || (node.parent_id && changes.get(node.parent_id)?.includes('removed'))) return null;
            const start = node.parent_id ? points.get(node.parent_id) : root;
            const end = points.get(node.id);
            if (!start || !end) return null;
            const middle = (start.y + end.y) / 2;
            return <div key={node.id} className="pointer-events-none text-line" aria-hidden="true">
              <span className="absolute border-l border-current" style={{ left: start.x, top: start.y + diameter / 2, height: middle - start.y - diameter / 2 }} />
              <span className="absolute border-t border-current" style={{ left: Math.min(start.x, end.x), top: middle, width: Math.abs(start.x - end.x) }} />
              <span className="absolute border-l border-current" style={{ left: end.x, top: middle, height: end.y - middle - diameter / 2 }} />
            </div>;
          })}
          <div className="absolute size-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-brand bg-sidebar"
            style={{ left: root.x, top: root.y }}>
            <span className="absolute bottom-full left-1/2 mb-2 w-[142px] -translate-x-1/2 truncate rounded bg-canvas px-1 text-center text-xs font-semibold"
              title={taskTitle}>{taskTitle}</span>
          </div>
          {nodes.map((node) => {
            const point = points.get(node.id);
            if (!point) return null;
            const types = changes.get(node.id) ?? [];
            const tone = types.includes('removed') ? 'border-red-500 bg-red-100' : types.includes('added') ? 'border-green-600 bg-green-100'
              : types.length ? 'border-amber-500 bg-amber-100' : 'border-brand bg-transparent';
            return <button key={node.id} type="button" aria-pressed={selectedId === node.id}
              aria-label={[node.question, ...types.map((type) => mapChanges[type] ?? type)].join(' · ')}
              onClick={() => onSelect(node.id)} title={node.question}
              className="absolute flex -translate-x-1/2 -translate-y-1/2 cursor-pointer items-center justify-center rounded-full"
              style={{ left: point.x, top: point.y, width: diameter, height: diameter }}>
              <span className="absolute bottom-full mb-2 w-[142px] truncate rounded bg-canvas px-1 text-center text-xs text-ink" aria-hidden="true">{node.question}</span>
              <span className={`size-6 rounded-full border-2 ${tone} ${selectedId === node.id ? 'ring-2 ring-brand ring-offset-2' : ''}`} />
            </button>;
          })}
        </div>
      </section>;
}

export type CommitNodeActions = (before: ResearchNode | undefined, after: ResearchNode | undefined) => ReactNode;

export function ResearchCommitMap({ map, taskTitle, difference, nodeActions, renderEvidence }: {
  map: ResearchMap; taskTitle: string; difference?: MapComparison; nodeActions?: CommitNodeActions;
  renderEvidence?: (item: MapEvidence) => ReactNode;
}) {
  const [selected, setSelected] = useState<string>();
  const nodes = new Map(map.nodes.map((node) => [node.id, node]));
  for (const node of difference?.before.nodes ?? []) if (!nodes.has(node.id)) nodes.set(node.id, node);
  const all = [...nodes.values()];
  const changes = new Map(difference?.changes.map(({ id, types }) => [id, types]));
  const focused = (selected && nodes.get(selected)) || nodes.get(difference?.changes[0]?.id ?? '') || all[0];
  const earlier = difference?.before.nodes.find((node) => node.id === focused?.id);

  return <WorkbenchPanel title={difference ? '地图变更' : '固定研发地图'}>
    <ResearchMapCanvas nodes={all} taskTitle={taskTitle} changes={changes} selectedId={focused?.id} onSelect={setSelected} />
    {!all.length && <p className="mb-0 text-sm text-control">这个版本还没有研发节点。</p>}
      {focused && <div className="mt-4 grid gap-2 text-sm">
        <h3 className="m-0 text-sm font-semibold [overflow-wrap:anywhere]">{focused.question}</h3>
        <p className="m-0 text-xs text-control">{[nodeStatuses[focused.status], ...(changes.get(focused.id) ?? []).map((type) => mapChanges[type] ?? type)].join(' · ')}</p>
        {earlier?.conclusion && earlier.conclusion !== focused.conclusion && <p className="m-0 whitespace-pre-wrap text-control">此前结论 · {earlier.conclusion}</p>}
        <p className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{focused.conclusion || '暂无结论'}</p>
        <p className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">解决标准 · {focused.criteria}</p>
        {focused.reason && <p className="m-0 whitespace-pre-wrap text-control">{focused.reason}</p>}
        {focused.details && <p className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{focused.details}</p>}
        {focused.evidence.map((item) => renderEvidence ? renderEvidence(item) : item.url ? <Link key={item.id} to={item.url} target="_blank" rel="noreferrer">{item.title ?? '查看固定证据'}</Link>
          : <span key={item.id} className="text-control">固定证据暂不可读取</span>)}
        {nodeActions?.(earlier, map.nodes.find(node => node.id === focused.id))}
      </div>}
  </WorkbenchPanel>;
}
