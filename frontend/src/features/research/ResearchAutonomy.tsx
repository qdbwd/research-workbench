import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Play } from 'lucide-react';
import { ApiError, api, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { researchApi, type ResearchTask } from './api';

const budgetFields = [
  { key: 'model_requests', label: '模型请求', defaultValue: 8, min: 1, max: 600 },
  { key: 'tool_calls', label: '工具调用', defaultValue: 20, min: 1, max: 2000 },
  { key: 'searches', label: '网页搜索', defaultValue: 0, min: 0, max: 200 },
  { key: 'active_seconds', label: '活跃执行秒数', defaultValue: 120, min: 1, max: 7200 },
] as const;

type Budget = Record<(typeof budgetFields)[number]['key'], number>;
type Permit = {
  status: 'active' | 'paused' | 'recovery_required' | 'exhausted' | 'revoked';
  reason: string | null;
  run_id: string | null;
  budgets: Budget;
  usage: Omit<Budget, 'active_seconds'> & { active_ms: number };
};

const statuses: Record<Permit['status'], string> = {
  active: '自主授权有效',
  paused: '已暂停',
  recovery_required: '等待你恢复',
  exhausted: '原授权额度已耗尽',
  revoked: '原授权已结束',
};

export function ResearchAutonomy({ task, onChanged }: {
  task: ResearchTask;
  onChanged: (task: ResearchTask) => void;
}) {
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const pending = useRef<{ signature: string; operationId: string } | null>(null);
  const base = researchApi(task.id);
  const permit = useResource<Permit | null>(open ? base + '/autonomy' : null, refresh);
  const authorization = permit.data;
  const state = authorization?.status;

  useEffect(() => {
    if (!open || state !== 'active') return;
    const timer = window.setInterval(() => setRefresh(value => value + 1), 5000);
    return () => window.clearInterval(timer);
  }, [open, state]);

  const canGrant = !state || state === 'exhausted' || state === 'revoked';
  const canResume = state === 'recovery_required';
  const editable = task.status === 'open';

  async function confirm(form: HTMLFormElement) {
    if (busy || (!canGrant && !canResume)) return;
    const formData = new FormData(form);
    const request = {
      action: canResume ? 'resume' : 'grant',
      expected_control_version: task.control_version,
      expected_revision_id: task.head.id,
      ...(canGrant ? { budgets: Object.fromEntries(budgetFields.map(({ key }) =>
        [key, Number(formData.get(key))])) } : {}),
    };
    const signature = JSON.stringify(request);
    if (pending.current?.signature !== signature) {
      pending.current = { signature, operationId: crypto.randomUUID() };
    }
    setBusy(true);
    setError('');
    try {
      const result = await send<{ task: ResearchTask }>(base + '/autonomy', {
        ...request, operation_id: pending.current.operationId,
      });
      pending.current = null;
      onChanged(result.task);
      setRefresh(value => value + 1);
    } catch (failure) {
      setError(messageOf(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        pending.current = null;
        setRefresh(value => value + 1);
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

  if (!editable && !open) return null;
  const { brief } = task.head.state;
  const showAction = editable && !permit.error && (!permit.loading || permit.data) && (canGrant || canResume);
  return (
    <>
      {editable && <Button type="button" className="button-secondary text-xs" onClick={() => {
        setError('');
        setOpen(true);
      }}><Play size={13} aria-hidden="true" /> 自主推进</Button>}
      {open && <Dialog title="自主推进" busy={busy} onClose={() => setOpen(false)}
        footer={<>
          <Button type="button" className="button-secondary" disabled={busy} onClick={() => setOpen(false)}>关闭</Button>
          {showAction && <Button type="submit" form={formId} disabled={busy}>
            {busy ? '正在提交…' : canResume ? '按原额度恢复' : '确认范围和额度，开始执行'}
          </Button>}
        </>}>
        <form id={formId} onSubmit={event => { event.preventDefault(); void confirm(event.currentTarget); }} className="grid gap-4">
          <div className="grid gap-1 rounded-lg border border-line bg-sidebar p-3 text-sm">
            <strong>{brief.goal}</strong>
            {brief.scope && <span>范围 · {brief.scope}</span>}
            {brief.constraints && <span>约束 · {brief.constraints}</span>}
            {brief.acceptance_criteria && <span>验收 · {brief.acceptance_criteria}</span>}
            <span className="text-control">基于当前简报 V{task.head.number} 和已关联材料。允许按范围调查并整理研究记录。</span>
          </div>
          {permit.loading && !permit.data && <p role="status">正在读取授权状态…</p>}
          {authorization && <div className="grid gap-1 text-sm">
            <strong role="status">{statuses[authorization.status]}</strong>
            {authorization.reason && <span>{authorization.reason}</span>}
            {budgetFields.map(({ key, label }) => <span key={key}>{label} · {
              key === 'active_seconds' ? Math.ceil(authorization.usage.active_ms / 1000) : authorization.usage[key]
            } / {authorization.budgets[key]}</span>)}
            {authorization.run_id && <Link to={`/runs/${encodeURIComponent(authorization.run_id)}`}>查看最近一次运行</Link>}
          </div>}
          {showAction && canGrant && <fieldset className="grid grid-cols-2 gap-3 border-0 p-0">
            <legend className="mb-2 font-semibold">{state ? '新一段授权额度' : '本段累计额度'}</legend>
            {budgetFields.map(({ key, label, defaultValue, min, max }) => <label key={key} className="grid gap-1 text-sm">
              {label}
              <input name={key} type="number" required min={min} max={max} defaultValue={defaultValue} />
            </label>)}
          </fieldset>}
          <ErrorBox message={error || permit.error} />
        </form>
      </Dialog>}
    </>
  );
}
