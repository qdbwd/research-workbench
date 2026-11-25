import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Empty, ErrorBox, Loading } from '../../shared/ui/components';
import { EvolutionDiffView, type EvolutionDiff } from './EvolutionDiffView';

export type Job = {
  id: string;
  job_type?: 'memory_review' | 'skill_review' | 'combined_review' | 'curator';
  run_id: string;
  conversation_id: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  error_message: string | null;
};
export type Change = {
  id: string;
  job_id?: string | null;
  target_id?: string | null;
  conversation_id: string | null;
  action: string;
  review_state: string;
  created_at: string;
  diff_summary?: EvolutionDiff | null;
  evidence_refs?: {
    quote?: string;
    similarity?: number;
    telemetry?: { load_count?: number; failed_run_count?: number; correction_count?: number };
  } | null;
  security_scan_result?: { reasons?: string[] } | null;
};

const panel = 'card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5';
const actions: Record<string, string> = {
  patch: '修补技能',
  archive: '归档技能',
  merge: '合并记忆',
  create: '新增记忆',
  update: '修改记忆',
  deprecate: '停用记忆',
};
const states: Record<string, string> = {
  pending_review: '待审核',
  applied: '已自动应用',
  approved: '已批准',
  rejected: '已驳回',
  skipped_conflict: '因修订冲突跳过',
  rolled_back: '已回滚',
  superseded: '已被替代',
};
export const reasonLabels: Record<string, string> = {
  target_unavailable: '目标不可用',
  manual_skill_review: '需要人工审阅技能差异',
  skill_high_failure: '近期失败或纠偏比例偏高',
  skill_stale: '长期未使用且有整理依据',
  skill_overlap: '技能方法重叠',
  memory_similarity: '记忆正文高度相似',
  memory_stale: '记忆已到复核期',
  security_scan: '安全扫描拦截',
  invalid_source_evidence: '缺少可核对的真人原话',
  target_not_read: '复盘未读取目标记忆',
  target_locked: '目标已锁定',
  target_revision_changed: '目标已有更新修订',
  invalid_proposal: '提案内容不符合记忆格式',
  automatic_write_disabled: '自动写入已关闭',
  target_pinned: '固定记忆需要人工审核',
  slot_capacity: '超过槽位字符上限',
  deprecation_requires_approval: '停用需要人工审核',
  automatic_write: '已按自动写入设置应用',
};
const jobLabels: Record<Job['status'], string> = {
  pending: '排队中',
  running: '复盘中',
  completed: '已完成',
  failed: '复盘失败',
  cancelled: '已取消',
  interrupted: '已中断',
};

export const evolutionTabClass =
  'border-b-2 border-transparent px-3 py-2 text-sm text-control aria-pressed:border-brand aria-pressed:font-semibold aria-pressed:text-brand';

export function PendingCount({ count }: { count: number }) {
  return count > 0 ? (
    <span
      aria-hidden="true"
      className="ml-1 rounded-full bg-[#f5f0e0] px-2 py-0.5 text-xs text-[#7a5d1c]"
    >
      {count}
    </span>
  ) : null;
}

function ChangeCard({ change, refreshed }: { change: Change; refreshed: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function resolve(action: 'approve' | 'reject' | 'rollback') {
    setBusy(true);
    setError('');
    try {
      await send(`/api/evolution/changes/${change.id}/${action}`);
      refreshed();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  const { reason = '', reason_code = '', rollback_revision } = change.diff_summary ?? {};
  const { quote, telemetry, similarity } = change.evidence_refs ?? {};
  const { reasons } = change.security_scan_result ?? {};
  return (
    <article className="min-w-0 border-b border-line py-4 last:border-0 [overflow-wrap:anywhere]">
      <div className="flex flex-wrap items-center gap-3">
        <strong>
          {actions[change.action] ?? change.action}
          {['patch', 'archive'].includes(change.action) && change.target_id
            ? ' · ' + change.target_id
            : ''}
        </strong>
        <span
          className={
            'rounded-full px-2 py-1 text-xs ' +
            (change.review_state === 'pending_review'
              ? 'bg-[#f5f0e0] text-[#7a5d1c]'
              : ['applied', 'approved'].includes(change.review_state)
                ? 'bg-[#e3f1e8] text-[#185940]'
                : change.review_state === 'rejected'
                  ? 'bg-[#fbeae8] text-danger'
                  : 'border border-line bg-canvas text-control')
          }
        >
          {states[change.review_state] ?? change.review_state}
        </span>
        <time dateTime={change.created_at}>
          {new Date(change.created_at).toLocaleString('zh-CN')}
        </time>
        {change.conversation_id && <Link to={`/chat/${change.conversation_id}`}>来源会话</Link>}
      </div>
      {(reasonLabels[reason_code] || reason) && (
        <p>{[reasonLabels[reason_code], reason].filter(Boolean).join(' · ')}</p>
      )}
      {quote && <blockquote>{quote}</blockquote>}
      <EvolutionDiffView diff={change.diff_summary} />
      {telemetry && (
        <p className="text-xs text-muted">
          读取 {telemetry.load_count ?? 0} 次 · 失败 {telemetry.failed_run_count ?? 0} · 纠偏{' '}
          {telemetry.correction_count ?? 0}
        </p>
      )}
      {typeof similarity === 'number' && (
        <p className="text-xs text-muted">相似度 {Math.round(similarity * 100)}%</p>
      )}
      {reasons?.length ? (
        <ul className="my-2 list-disc rounded-lg bg-[#fbeae8] p-3 pl-7 text-sm text-danger">
          {reasons.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {rollback_revision && <p>回滚修订 {rollback_revision}</p>}
      <ErrorBox message={error} />
      <div className="flex flex-wrap gap-2">
        {change.review_state === 'pending_review' && (
          <>
            <Button type="button" disabled={busy} onClick={() => void resolve('approve')}>
              批准
            </Button>
            <Button
              type="button"
              className="button-secondary"
              disabled={busy}
              onClick={() => void resolve('reject')}
            >
              驳回
            </Button>
          </>
        )}
        {['applied', 'approved'].includes(change.review_state) && (
          <Button
            type="button"
            className="button-secondary"
            disabled={busy}
            onClick={() => void resolve('rollback')}
          >
            单项回滚
          </Button>
        )}
      </div>
    </article>
  );
}

export function LearningJourneyList({
  version,
  refreshed,
  targetKind,
  jobId,
  showJobs = true,
  pendingOnly = false,
}: {
  version: string;
  refreshed: () => void;
  targetKind?: 'memory' | 'skill';
  jobId?: string;
  showJobs?: boolean;
  pendingOnly?: boolean;
}) {
  const [tick, setTick] = useState(0);
  const [error, setError] = useState('');
  const [cancelId, setCancelId] = useState('');
  const [filter, setFilter] = useState(pendingOnly ? 'pending_review' : 'all');
  const key = `${version}:${tick}`;
  const query = targetKind ? '?target_kind=' + targetKind : '';
  const changes = useResource<Change[]>('/api/evolution/changes' + query, key);
  const jobs = useResource<Job[]>(showJobs ? '/api/evolution/jobs?limit=10' : null, key);
  const shownJobs = jobs.data?.filter((job) =>
    targetKind === 'skill'
      ? job.job_type === 'skill_review' || job.job_type === 'combined_review'
      : !job.job_type || job.job_type === 'memory_review' || job.job_type === 'combined_review',
  );
  const active = shownJobs?.some((job) => ['pending', 'running'].includes(job.status)) ?? false;
  const shown = changes.data?.filter(
    (item) =>
      (!jobId || item.job_id === jobId) &&
      (filter === 'all' ||
        (filter === 'applied'
          ? ['applied', 'approved'].includes(item.review_state)
          : item.review_state === filter)),
  );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setTick((value) => value + 1), 3000);
    return () => clearInterval(timer);
  }, [active]);

  async function cancel(job: Job) {
    setCancelId(job.id);
    setError('');
    try {
      await send(`/api/runs/${job.run_id}/cancel`);
      setTick((value) => value + 1);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setCancelId('');
    }
  }

  return (
    <section className={panel + ' min-w-0'} aria-label="复盘记录">
      <Button
        type="button"
        className="button-secondary"
        onClick={() => setTick((value) => value + 1)}
      >
        刷新记录
      </Button>
      <ErrorBox message={error || changes.error || jobs.error} />
      {shownJobs && shownJobs.length > 0 && (
        <details
          className="my-3 rounded-lg border border-line bg-canvas px-3 py-2"
          open={targetKind !== 'skill' || active}
        >
          <summary className="cursor-pointer text-sm font-medium">近期复盘任务</summary>
          <ul className="my-3 space-y-2">
            {shownJobs.map((job) => (
              <li key={job.id} className="flex flex-wrap items-center gap-2">
                {job.conversation_id && <Link to={`/chat/${job.conversation_id}`}>复盘会话</Link>}
                <span>{jobLabels[job.status]}</span>
                {job.error_message && (
                  <span className="text-sm text-muted">{job.error_message}</span>
                )}
                {['pending', 'running', 'interrupted'].includes(job.status) && (
                  <Button
                    type="button"
                    className="button-quiet"
                    disabled={Boolean(cancelId)}
                    onClick={() => void cancel(job)}
                  >
                    取消复盘
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      <nav aria-label="变更状态" className="my-3 flex flex-wrap gap-2">
        {(
          [
            ['all', '全部'],
            ['pending_review', '待审核'],
            ['applied', '已应用'],
            ['rejected', '已驳回'],
            ['rolled_back', '已回滚'],
          ] as const
        ).map(([id, label]) => (
          <Button
            key={id}
            type="button"
            className="button-quiet rounded-full"
            aria-pressed={filter === id}
            onClick={() => setFilter(id)}
          >
            {label}
          </Button>
        ))}
      </nav>
      {changes.loading && !changes.data && <Loading />}
      {shown?.length === 0 && <Empty title="还没有记忆变更记录" />}
      {shown?.map((change) => (
        <ChangeCard key={change.id} change={change} refreshed={refreshed} />
      ))}
    </section>
  );
}
