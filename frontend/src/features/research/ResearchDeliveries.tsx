import { useId, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { FileCheck } from 'lucide-react';
import { ApiError, api, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { researchApi, researchPath, revisionSuffix, type ResearchTask } from './api';
import type { MapEvidence } from './mapApi';

type ReviewAction = 'accept' | 'request_changes';
type Delivery = {
  id: string;
  branch_id: string;
  branch_name: string;
  branch_archived: boolean;
  source_revision_id: string;
  submit_run_id: string;
  status: string;
  drain_state: string;
  evidence: MapEvidence[];
  blockers: { kind: string; id: string; status: string }[];
  content: {
    title: string;
    diagnosis: string;
    recommendations: string;
    limitations: string;
    materials: { source_id: string; source_version_id: string; title: string }[];
  };
  review_reason: string | null;
  created_at: string;
};

const statuses: Record<string, string> = {
  finalizing: '正在收尾', blocked: '收尾受阻', submitted: '待验收',
  accepted: '已通过验收', changes_requested: '已要求修订', superseded: '已被新版取代',
};

export function ResearchDeliveries({ task, onChanged }: {
  task: ResearchTask;
  onChanged: (task: ResearchTask) => void;
}) {
  const formId = useId();
  const [params, setParams] = useSearchParams();
  const linked = params.get('delivery');
  const [open, setOpen] = useState(Boolean(linked));
  const [version, setVersion] = useState(0);
  const [selectedId, setSelectedId] = useState(linked);
  const [action, setAction] = useState<ReviewAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const base = researchApi(task.id);
  const result = useResource<{ items: Delivery[] }>(open ? base + '/deliveries' : null, version);
  const items = result.data?.items ?? [];
  const selected = items.find(row => row.id === selectedId) ?? items[0];
  const selectedIsWriting = selected?.branch_id === (task.write_branch_id ?? task.main_branch_id);
  const canReview = selected?.status === 'submitted' && !selected.branch_archived &&
    (task.status === 'open' || task.status === 'awaiting_acceptance' || (!selectedIsWriting && task.status === 'completed'));

  function close() {
    setOpen(false);
    setAction(null);
    setError('');
    if (linked) setParams(previous => {
      const next = new URLSearchParams(previous);
      next.delete('delivery');
      return next;
    }, { replace: true });
  }

  async function update(reason?: string) {
    if (!selected || busy) return;
    const path = base + '/deliveries/' + encodeURIComponent(selected.id);
    const review = action && typeof reason === 'string' ? {
      action, reason, source_revision_id: selected.source_revision_id,
      expected_control_version: task.control_version,
    } : null;
    setBusy(true);
    setError('');
    try {
      if (review) {
        const signature = JSON.stringify([selected.id, review]);
        if (pending.current?.signature !== signature) {
          pending.current = { signature, key: crypto.randomUUID() };
        }
        const updated = await send<{ task: ResearchTask }>(path + '/review', {
          ...review, operation_id: pending.current.key,
        });
        onChanged(updated.task);
        pending.current = null;
      } else {
        await send(path + '/refresh');
        onChanged(await api<ResearchTask>(base));
      }
      setAction(null);
      setVersion(value => value + 1);
    } catch (failure) {
      setError(messageOf(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        setAction(null);
        setVersion(value => value + 1);
        try {
          onChanged(await api<ResearchTask>(base));
        } catch (readFailure) {
          setError(messageOf(readFailure));
        }
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button type="button" className="button-secondary text-xs" onClick={() => setOpen(true)}>
        <FileCheck size={14} aria-hidden="true" /> 研发成果
      </Button>
      {open && <Dialog title="研发成果" busy={busy} onClose={close} footer={<>
        <Button type="button" className="button-secondary" disabled={busy} onClick={close}>关闭</Button>
        {selected && selected.drain_state !== 'settled' && <Button type="button" className="button-secondary"
          disabled={busy} onClick={() => void update()}>检查收尾状态</Button>}
        {canReview && !action && <>
          <Button type="button" className="button-secondary" onClick={() => setAction('request_changes')}>要求修订</Button>
          <Button type="button" onClick={() => setAction('accept')}>通过验收</Button>
        </>}
        {canReview && action && <Button type="submit" form={formId} disabled={busy}>
          {action === 'accept' ? '确认通过' : '确认要求修订'}
        </Button>}
      </>}>
        <ErrorBox message={error || result.error} />
        {!selected && !result.error && <p className="text-control">
          {result.loading ? '正在读取成果…' : '本任务尚未提交成果。'}
        </p>}
        {selected && <article className="grid gap-4">
          {items.length > 1 && <label className="grid gap-1 text-sm">提交记录
            <select value={selected.id} onChange={event => {
              setSelectedId(event.target.value);
              setAction(null);
              setError('');
            }}>
              {items.map(row => <option key={row.id} value={row.id}>{row.content.title} · {row.branch_name} · {statuses[row.status]}</option>)}
            </select>
          </label>}
          <div>
            <p role="status" className="m-0 text-sm font-medium text-brand">{statuses[selected.status]}</p>
            <h3 className="mt-1 mb-2 text-xl font-semibold">{selected.content.title}</h3>
            <Link to={researchPath(task.id) + revisionSuffix(selected.branch_id) + '/' + encodeURIComponent(selected.source_revision_id)}>
              查看固定研究版本
            </Link>
          </div>
          {([['诊断', selected.content.diagnosis], ['建议', selected.content.recommendations],
            ['限制', selected.content.limitations], ['验收意见', selected.review_reason]] as const)
            .filter(([, value]) => Boolean(value)).map(([label, value]) => <section key={label}>
              <h4 className="m-0 text-sm font-semibold">{label}</h4>
              <p className="mt-1 mb-0 text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">{value}</p>
            </section>)}
          {selected.content.materials.length > 0 && <ul className="m-0 grid gap-1 text-sm">
            {selected.content.materials.map(material => <li key={material.source_version_id}>
              <Link to={`/sources/${encodeURIComponent(material.source_id)}?version=${encodeURIComponent(material.source_version_id)}`}>
                {material.title}
              </Link>
            </li>)}
          </ul>}
          {selected.evidence.length > 0 && <section>
            <h4 className="m-0 text-sm font-semibold">固定证据</h4>
            <ul className="mt-2 mb-0 grid gap-2 pl-4 text-sm">{selected.evidence.map(item => <li key={item.id}>
              {item.url ? <Link to={item.url}>{item.title ?? '固定来源'}</Link> : '原依据无法读取'}
              {item.available && item.quote && <p className="my-1 text-control">{item.quote}</p>}
            </li>)}</ul>
          </section>}
          {selected.blockers.length > 0 && <p className="m-0 text-sm text-control">
            仍有 {selected.blockers.length} 项运行或操作需要核对。{' '}
            <Link to={`/runs/${encodeURIComponent(selected.submit_run_id)}`}>查看运行记录</Link>
          </p>}
          {canReview && action && <form id={formId} onSubmit={event => {
            event.preventDefault();
            void update(String(new FormData(event.currentTarget).get('reason') ?? '').trim());
          }}>
            <label className="grid gap-1 text-sm font-semibold">
              {action === 'accept' ? '验收理由' : '修订要求'}
              <textarea name="reason" rows={3} maxLength={1000} required autoFocus />
            </label>
          </form>}
        </article>}
      </Dialog>}
    </>
  );
}
