import { useId, useRef, useState } from 'react';
import { ApiError, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { researchApi, type ResearchTask } from './api';
import { nodeStatuses, type MapEvidence, type ResearchMap, type ResearchNode } from './mapApi';

export function ResearchNodeEditor({ task, map, node, onSaved, onClose, onConflict, remove = false }: {
  task: ResearchTask;
  map: ResearchMap;
  node: ResearchNode | null;
  onSaved: () => void;
  onClose: () => void;
  onConflict: () => void;
  remove?: boolean;
}) {
  const formId = useId();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [binding] = useState({ revision: map.revision_id, control: task.control_version });
  const [offset, setOffset] = useState(0);
  const [evidenceIds, setEvidenceIds] = useState(node?.evidence_ids ?? []);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const base = researchApi(task.id) + '/map';
  const catalog = useResource<{ items: MapEvidence[]; next_offset: number | null }>(
    remove ? null : `${base}/evidence?offset=${offset}`,
  );
  const evidence = new Map((node?.evidence ?? []).map(item => [item.id, item]));
  for (const item of catalog.data?.items ?? []) evidence.set(item.id, item);

  async function save(form: HTMLFormElement) {
    if (busy) return;
    const values = new FormData(form);
    const read = (name: string) => String(values.get(name) ?? '').trim();
    const parentId = read('parent_id') || null;
    const operation = remove ? { action: 'remove', node_id: node?.id } : {
      action: 'save', node: {
        id: node?.id, question: read('question'), parent_id: parentId, required: Boolean(values.get('required')),
        status: read('status'), criteria: read('criteria'), conclusion: read('conclusion'),
        reason: read('assessment'), details: read('details'), evidence_ids: evidenceIds, related: node?.related ?? [],
      },
    };
    const body = {
      expected_revision_id: binding.revision, expected_control_version: binding.control,
      reason: read('change_reason'), operation,
    };
    const signature = JSON.stringify(body);
    if (pending.current?.signature !== signature) {
      pending.current = { signature, key: crypto.randomUUID() };
    }
    setBusy(true);
    setError('');
    try {
      await send(base, { ...body, operation_id: pending.current.key });
      onSaved();
    } catch (failure) {
      setError(messageOf(failure));
      if (failure instanceof ApiError && (failure.code === 'map_conflict' || failure.code === 'map_unavailable')) {
        pending.current = null;
        onConflict();
      }
    } finally {
      setBusy(false);
    }
  }

  return <Dialog title={remove ? '移除研究主题' : node ? '编辑研究主题' : '新增研究主题'}
    busy={busy} onClose={onClose} footer={<>
      <Button type="button" className="button-secondary" disabled={busy} onClick={onClose}>取消</Button>
      <Button type="submit" disabled={busy} form={formId} className={remove ? 'bg-danger text-white' : ''}>
        {remove ? '确认移除' : '保存主题'}
      </Button>
    </>}>
    <form id={formId} className="grid gap-4" onSubmit={event => {
      event.preventDefault();
      void save(event.currentTarget);
    }}>
      {remove && <p>将《{node?.question}》从当前地图移除。原版本可以继续查看。</p>}
      {!remove && <>
        <label className="grid gap-1 text-sm font-semibold">具体问题
          <input name="question" defaultValue={node?.question} required minLength={3} maxLength={300} autoFocus />
        </label>
        <label className="grid gap-1 text-sm font-semibold">解决标准
          <textarea name="criteria" defaultValue={node?.criteria} required maxLength={1200} rows={2} />
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-sm font-semibold">上级主题
            <select name="parent_id" defaultValue={node?.parent_id ?? ''}>
              <option value="">顶层主题</option>
              {map.nodes.filter(item => item.id !== node?.id).map(item => (
                <option key={item.id} value={item.id}>{item.question}</option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-sm font-semibold">状态
            <select name="status" defaultValue={node?.status ?? 'open'}>
              {Object.entries(nodeStatuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" name="required" defaultChecked={node?.required ?? true} />
          是上级主题必须解决的依赖
        </label>
        {[
          { name: 'conclusion', label: '结论', initial: node?.conclusion, maxLength: 4000 },
          { name: 'assessment', label: '判断理由', initial: node?.reason, maxLength: 1200 },
          { name: 'details', label: '详情记录', initial: node?.details, maxLength: 4000 },
        ].map(({ name, label, initial, maxLength }) => <label key={name} className="grid gap-1 text-sm font-semibold">
          {label}<textarea name={name} defaultValue={initial} maxLength={maxLength} rows={2} />
        </label>)}
        <fieldset className="grid max-h-48 gap-2 overflow-auto rounded-lg border border-line p-2.5 text-sm">
          <legend className="font-semibold">已读证据 · 已选 {evidenceIds.length} 项</legend>
          {[...evidence.values()].map(item => <label key={item.id} className="flex items-start gap-2">
            <input type="checkbox" checked={evidenceIds.includes(item.id)} disabled={
              !evidenceIds.includes(item.id) && (!item.available || evidenceIds.length >= 12)
            } onChange={event => {
              const checked = event.target.checked;
              setEvidenceIds(current => checked ? [...current, item.id] : current.filter(id => id !== item.id));
            }} />
            <span className="min-w-0 [overflow-wrap:anywhere]">
              {item.title ?? '固定来源'}{item.available ? ` · ${item.quote}` : ' · 已无法读取'}
            </span>
          </label>)}
          {!evidence.size && <span className="text-control">{catalog.loading ? '正在读取证据…' : '这项研发还没有已读证据。'}</span>}
          <ErrorBox message={catalog.error} />
          <div className="flex gap-2">
            {offset > 0 && <Button type="button" className="button-secondary text-xs" onClick={() => setOffset(Math.max(0, offset - 25))}>上一页</Button>}
            {catalog.data?.next_offset != null && <Button type="button" className="button-secondary text-xs"
              onClick={() => setOffset(catalog.data!.next_offset!)}>下一页</Button>}
          </div>
        </fieldset>
      </>}
      <label className="grid gap-1 text-sm font-semibold">变更理由
        <textarea name="change_reason" required maxLength={1000} rows={2} />
      </label>
      <ErrorBox message={error} />
    </form>
  </Dialog>;
}
