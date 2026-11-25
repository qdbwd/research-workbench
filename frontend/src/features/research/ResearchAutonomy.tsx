import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Play } from 'lucide-react';
import { ApiError, api, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { researchApi, type ResearchTask } from './api';

const budgetFields = [
  { key: 'model_requests', label: '模型请求', min: 1, max: 600 },
  { key: 'tool_calls', label: '工具调用', min: 1, max: 2000 },
  { key: 'searches', label: '网页搜索', min: 0, max: 200 },
  { key: 'active_seconds', label: '活跃执行秒数', min: 1, max: 7200 },
] as const;

type Budget = Record<(typeof budgetFields)[number]['key'], number>;

const presets = [
  {
    id: 'short',
    label: '短程探索',
    hint: '约 5 分钟 · 适合快速核对线索',
    budgets: { model_requests: 15, tool_calls: 40, active_seconds: 300 },
  },
  {
    id: 'standard',
    label: '标准研发',
    hint: '约 15 分钟 · 推荐，适合常规专题研究',
    budgets: { model_requests: 40, tool_calls: 120, active_seconds: 900 },
  },
  {
    id: 'deep',
    label: '深度推进',
    hint: '约 40 分钟 · 适合多资料交叉验证',
    budgets: { model_requests: 100, tool_calls: 300, active_seconds: 2400 },
  },
] as const;

type PresetId = (typeof presets)[number]['id'];
const DEFAULT_WEB_SEARCHES = 12;

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
  const [preset, setPreset] = useState<PresetId>('standard');
  const [allowWeb, setAllowWeb] = useState(false);
  const [budgets, setBudgets] = useState<Budget>({
    ...presets[1].budgets,
    searches: 0,
  });
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

  function selectPreset(id: PresetId) {
    const found = presets.find((item) => item.id === id) ?? presets[1];
    setPreset(found.id);
    setBudgets((prev) => ({
      ...found.budgets,
      searches: allowWeb ? (prev.searches > 0 ? prev.searches : DEFAULT_WEB_SEARCHES) : 0,
    }));
  }

  function toggleWeb(checked: boolean) {
    setAllowWeb(checked);
    setBudgets((prev) => ({
      ...prev,
      searches: checked ? (prev.searches > 0 ? prev.searches : DEFAULT_WEB_SEARCHES) : 0,
    }));
  }

  const canGrant = !state || state === 'exhausted' || state === 'revoked';
  const canResume = state === 'recovery_required';
  const editable = task.status === 'open';

  async function confirm() {
    if (busy || (!canGrant && !canResume)) return;
    const request = {
      action: canResume ? 'resume' : 'grant',
      expected_control_version: task.control_version,
      expected_revision_id: task.head.id,
      ...(canGrant ? { budgets } : {}),
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
        <form id={formId} onSubmit={event => { event.preventDefault(); void confirm(); }} className="grid gap-4">
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
          {showAction && canGrant && <div className="grid gap-3">
            <fieldset className="grid gap-2 border-0 p-0">
              <legend className="mb-1 text-sm font-semibold">{state ? '选择新一段推进档位' : '选择推进档位（防失控安全上限）'}</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {presets.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={preset === item.id}
                    onClick={() => selectPreset(item.id)}
                    className={`rounded-lg border px-3 py-2 text-left text-xs transition ${
                      preset === item.id
                        ? 'border-brand bg-sidebar font-medium text-brand'
                        : 'border-line bg-white text-control hover:border-brand/50'
                    }`}
                  >
                    <div className="text-sm font-semibold">{item.label}</div>
                    <div className="mt-0.5 text-[11px] opacity-85">{item.hint}</div>
                  </button>
                ))}
              </div>
            </fieldset>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={allowWeb}
                onChange={(event) => toggleWeb(event.target.checked)}
              />
              <span>允许联网搜索（关闭则仅使用当前材料与知识库）</span>
            </label>

            <details className="rounded-lg border border-line bg-white px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium text-control">
                查看或调整安全上限（模型 {budgets.model_requests} 次 · 工具 {budgets.tool_calls} 次 · 搜索 {budgets.searches} 次 · {budgets.active_seconds < 60 ? `${budgets.active_seconds} 秒` : `${Math.round(budgets.active_seconds / 60)} 分钟`}）
              </summary>
              <div className="mt-3 grid grid-cols-2 gap-3">
                {budgetFields.map(({ key, label, min, max }) => (
                  <label key={key} className="grid gap-1 text-xs">
                    {label}
                    <input
                      name={key}
                      type="number"
                      required
                      min={min}
                      max={max}
                      value={budgets[key]}
                      onChange={(event) => {
                        const nextValue = Number(event.target.value);
                        setBudgets((prev) => ({ ...prev, [key]: nextValue }));
                        if (key === 'searches') setAllowWeb(nextValue > 0);
                      }}
                    />
                  </label>
                ))}
              </div>
            </details>
          </div>}
          <ErrorBox message={error || permit.error} />
        </form>
      </Dialog>}
    </>
  );
}
