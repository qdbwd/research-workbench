import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { BackLink, WorkbenchHeading, WorkbenchPanel } from '../../shared/ui/Workbench';
import { researchApi, researchPath, revisionSuffix, type ResearchTask } from './api';

type Attempt = {
  id: string;
  status: string;
  question: string;
  hypothesis: string;
  source_revision_id: string;
  reason: string | null;
  evidence_id: string | null;
  image: string;
  script_sha256: string;
  input_sha256: string;
  timeout_seconds: number;
  case_count: number;
  reproduced_from: string | null;
  result: {
    conclusion: string;
    limitation: string;
    cases: {
      id: string;
      fault: string;
      observed_context_has_gold: boolean;
      reranked_context_has_gold: boolean;
      observed_answer_correct: boolean;
    }[];
  } | null;
};

type Action = 'approve' | 'cancel' | 'reproduce' | 'acknowledge_stop';
const labels: Record<string, string> = {
  awaiting_approval: '待审批', approved: '已批准', dispatched: '等待执行器', running: '执行中',
  cancel_requested: '正在撤回', completed: '结果已收录', cancelled: '已取消', failed: '执行失败',
  timed_out: '执行超时', unknown: '状态未知', stale: '结果未采纳',
};
const faults: Record<string, string> = {
  retrieval_or_context_selection: '正确片段未进上下文', answer_misuse: '已有证据被误用', none_observed: '未发现这两类问题',
};
const actionLabels: Record<Action, string> = {
  approve: '审批实验', cancel: '撤回实验', reproduce: '准备复现', acknowledge_stop: '记录停止确认',
};
const inflight = ['approved', 'dispatched', 'running', 'cancel_requested'];

function ExperimentsScreen({ taskId }: { taskId: string }) {
  const base = researchApi(taskId);
  const [version, refresh] = useState(0);
  const [selection, setSelection] = useState<string>();
  const [action, setAction] = useState<Action>();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{ signature: string; operationId: string } | null>(null);
  const task = useResource<ResearchTask>(base, version);
  const collection = useResource<{ enabled: boolean; items: Attempt[] }>(base + '/experiments', version);
  const selected = collection.data?.items.find(row => row.id === selection) ?? collection.data?.items[0];
  const dataset = useResource<{ dataset: unknown }>(
    selected && action === 'approve' ? base + '/experiments/' + encodeURIComponent(selected.id) : null,
  );
  const observing = Boolean(collection.data?.items.some(row => inflight.includes(row.status)));

  useEffect(() => {
    if (!observing) return;
    const timer = window.setInterval(() => refresh(value => value + 1), 2500);
    return () => window.clearInterval(timer);
  }, [observing]);

  async function submit() {
    if (!task.data || !selected || !action || busy) return;
    const body = {
      action, reason, expected_revision_id: task.data.head.id,
      expected_control_version: task.data.control_version,
    };
    const signature = JSON.stringify([selected.id, body]);
    if (pending.current?.signature !== signature) {
      pending.current = { signature, operationId: crypto.randomUUID() };
    }
    setBusy(true);
    setError('');
    try {
      const updated = await send<Attempt>(base + '/experiments/' + encodeURIComponent(selected.id),
        { ...body, operation_id: pending.current.operationId });
      pending.current = null;
      setAction(undefined);
      setReason('');
      setSelection(updated.id);
      refresh(value => value + 1);
    } catch (failure) {
      setError(messageOf(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        pending.current = null;
        refresh(value => value + 1);
      }
    } finally {
      setBusy(false);
    }
  }

  return <div className="mx-auto max-w-6xl">
    <BackLink to={researchPath(taskId)}>返回研发任务</BackLink>
    <WorkbenchHeading eyebrow={task.data?.title ?? '研发工作台'} title="独立实验" />
    <ErrorBox message={task.error || collection.error} />
    {collection.data?.enabled === false && <p className="text-sm text-control">当前部署尚未配置独立执行器。</p>}
    <div className="grid gap-5 lg:grid-cols-[minmax(16rem,0.8fr)_minmax(0,1.5fr)]">
      <WorkbenchPanel title="实验尝试">
        {collection.data?.items.length ? <ul className="m-0 grid list-none gap-2 p-0">
          {collection.data.items.map(row => <li key={row.id}>
            <button type="button" aria-pressed={selected?.id === row.id} onClick={() => setSelection(row.id)}
              className={`grid w-full gap-1 rounded-lg border px-3 py-2 text-left ${selected?.id === row.id ? 'border-brand/30 bg-sidebar' : 'border-line hover:bg-sidebar'}`}>
              <span className="text-sm font-semibold [overflow-wrap:anywhere]">{row.question}</span>
              <span className="text-xs text-control">{labels[row.status] ?? row.status}</span>
            </button>
          </li>)}
        </ul> : <p className="text-sm text-control">{collection.loading ? '正在读取实验…' : '本任务尚无实验提案。'}</p>}
      </WorkbenchPanel>
      {selected && <WorkbenchPanel title={selected.question}>
        <div className="grid gap-4">
          <p role="status" className="m-0 text-sm font-semibold text-brand">{labels[selected.status]}</p>
          <p className="m-0 text-sm">{selected.hypothesis}</p>
          {selected.reason && <p className="m-0 text-sm text-control">{selected.reason}</p>}
          <div className="flex flex-wrap gap-3 text-sm">
            {task.data && <Link to={researchPath(taskId) + revisionSuffix(task.data.main_branch_id) + '/' + selected.source_revision_id}>
              固定研究版本
            </Link>}
            {selected.evidence_id && <Link to={researchPath(taskId) + '/evidence/' + selected.evidence_id}>查看实验回执和全部指标</Link>}
          </div>
          {selected.result && <div className="grid gap-3 rounded-lg border border-line p-3 text-sm">
            {selected.status === 'stale' && <p className="m-0 font-medium text-control">历史回执，仅供核对</p>}
            <p className="m-0 font-semibold">{selected.result.conclusion}</p>
            {selected.result.cases.map(row => <div key={row.id} className="border-t border-line pt-2">
              <span className="font-medium">{row.id} · {faults[row.fault] ?? row.fault}</span>
              <p className="my-1 text-control">
                正确片段在上下文中 · {row.observed_context_has_gold ? '是' : '否'} → {row.reranked_context_has_gold ? '是' : '否'}
                {' · 原回答'}{row.observed_answer_correct ? '正确' : '有误'}
              </p>
            </div>)}
            <p className="m-0 text-control">{selected.result.limitation}</p>
          </div>}
          <dl className="m-0 grid gap-1 break-all rounded-lg bg-sidebar p-3 text-xs text-control">
            <div>镜像 · {selected.image}</div>
            <div>输入 SHA256 · {selected.input_sha256}</div>
            <div>脚本 SHA256 · {selected.script_sha256}</div>
            <div>{selected.case_count} 个固定问题 · {selected.timeout_seconds} 秒执行上限</div>
          </dl>
          <div className="flex flex-wrap gap-2">
            {task.data?.status === 'open' && collection.data?.enabled && selected.status === 'awaiting_approval'
              && <Button type="button" onClick={() => setAction('approve')}>审批实验</Button>}
            {['awaiting_approval', 'approved', 'dispatched', 'running'].includes(selected.status)
              && <Button type="button" className="button-secondary" onClick={() => setAction('cancel')}>撤回实验</Button>}
            {selected.status === 'unknown' && <Button type="button" className="button-secondary"
              onClick={() => setAction('acknowledge_stop')}>记录停止确认</Button>}
            {task.data?.status === 'open' && ['completed', 'failed', 'cancelled', 'timed_out', 'stale'].includes(selected.status)
              && <Button type="button" className="button-secondary" onClick={() => setAction('reproduce')}>准备复现</Button>}
          </div>
        </div>
      </WorkbenchPanel>}
    </div>
    {action && selected && <Dialog title={actionLabels[action]} busy={busy}
      onClose={() => { setAction(undefined); setError(''); }}
      footer={<>
        <Button type="button" className="button-secondary" disabled={busy} onClick={() => {
          setAction(undefined);
          setError('');
        }}>返回</Button>
        <Button type="button" disabled={busy || (action === 'approve' && !dataset.data)
          || (action === 'acknowledge_stop' && !reason.trim())} onClick={() => void submit()}>
          {action === 'approve' ? '确认批准固定输入' : '确认操作'}
        </Button>
      </>}>
      {action === 'approve' && <>
        <p>核对固定原文、问题、候选和原回答。本次批准只适用于所选尝试。</p>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">
          {dataset.data ? JSON.stringify(dataset.data.dataset, null, 2) : '正在读取固定输入…'}
        </pre>
      </>}
      {action === 'acknowledge_stop' && <label className="grid gap-2 text-sm">
        请记录如何确认执行器和该尝试的容器都已停止。
        <textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={4000} rows={4} />
      </label>}
      {action === 'cancel' && <p>撤回本次授权，迟到结果保留供核对。</p>}
      {action === 'reproduce' && <p>沿用所选尝试的镜像、脚本和输入，为新尝试单独审批。</p>}
      <ErrorBox message={error || dataset.error} />
    </Dialog>}
  </div>;
}

export function ResearchExperimentsPage() {
  const { taskId } = useParams();
  return taskId ? <ExperimentsScreen key={taskId} taskId={taskId} /> : null;
}
