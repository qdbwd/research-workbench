import { useEffect, useRef, useState } from 'react';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { Empty, ErrorBox, Loading } from '../../shared/ui/components';
import {
  EvolutionSettingsDialog,
  ManualReviewDialog,
  type EvolutionSettings,
} from '../evolution/EvolutionDialogs';
import {
  LearningJourneyList,
  evolutionTabClass,
  reasonLabels,
  type Job,
} from '../evolution/LearningJourneyList';

type Candidate = {
  target_id: string;
  target_kind: 'skill' | 'memory';
  action: string;
  label: string;
  reason_code: string;
  similarity?: number;
  readonly?: boolean;
  telemetry?: {
    load_count: number;
    success_run_count: number;
    failed_run_count: number;
    correction_count: number;
    last_used_at: string | null;
  };
};

function CuratorPanel({ version, refresh }: { version: string; refresh: () => void }) {
  const candidates = useResource<{
    items: Candidate[];
    due: boolean;
    curator_last_run_at: string | null;
  }>('/api/evolution/curator/preview', version);
  const [model, setModel] = useState(false);
  const [jobId, setJobId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [jobTick, setJobTick] = useState(0);
  const jobs = useResource<Job[]>(
    jobId ? '/api/evolution/jobs?limit=20' : null,
    `${version}:${jobTick}`,
  );
  const job = jobs.data?.find((entry) => entry.id === jobId);
  const active = Boolean(jobId && (!job || job.status === 'pending' || job.status === 'running'));
  const key = useRef<{ model: boolean; id: string } | null>(null);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setJobTick((value) => value + 1), 1800);
    return () => clearInterval(timer);
  }, [active]);
  async function scan() {
    setBusy(true);
    setError('');
    if (key.current?.model !== model) key.current = { model, id: crypto.randomUUID() };
    try {
      const result = await send<Job>('/api/evolution/curator', {
        request_key: key.current.id,
        use_model_merge: model,
      });
      key.current = null;
      setJobId(result.id);
      refresh();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="grid min-w-0 gap-4">
      <section className="rounded-xl border border-line bg-canvas p-3.5">
        <p className="mt-0 text-sm">
          整理保护 Pin、Lock 与有效任务引用。合并和归档建议须经人工确认。
        </p>
        {candidates.data?.due && <p className="text-xs text-control">已到建议扫描时间。</p>}
        <label className="check flex-row items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={model}
            disabled={busy || active}
            onChange={(e) => setModel(e.target.checked)}
          />
          启用模型生成语义合并草稿（默认关闭）
        </label>
        <Button type="button" disabled={busy || active} onClick={() => void scan()}>
          运行整理扫描
        </Button>
        {active && (
          <p role="status" className="text-sm text-control">
            整理任务已入队，正在等待结果。
          </p>
        )}
        {job?.status === 'completed' && (
          <p role="status" className="text-sm text-brand">
            整理完成，请审阅生成的建议。
          </p>
        )}
        <ErrorBox message={error || candidates.error || jobs.error || job?.error_message || ''} />
      </section>
      <section aria-label="整理候选预览" className="grid gap-2">
        <h3 className="m-0 text-sm font-semibold">整理候选预览</h3>
        {candidates.loading && !candidates.data && <Loading />}
        {candidates.data?.items?.length === 0 && <Empty title="暂无符合规则的候选" />}
        {candidates.data?.items?.map((item) => (
          <article
            key={item.target_kind + item.action + item.target_id}
            className="min-w-0 rounded-lg border border-line p-3 text-sm [overflow-wrap:anywhere]"
          >
            <strong>{item.label}</strong>
            <p className="my-1 text-xs text-control">
              {reasonLabels[item.reason_code] ?? item.reason_code}
              {item.readonly ? ' · 只读提示' : ''}
              {typeof item.similarity === 'number'
                ? ` · 相似度 ${Math.round(item.similarity * 100)}%`
                : ''}
            </p>
            {item.telemetry && (
              <p className="my-1 text-xs text-muted">
                读取 {item.telemetry.load_count} 次 · 成功 {item.telemetry.success_run_count} · 失败{' '}
                {item.telemetry.failed_run_count} · 纠偏 {item.telemetry.correction_count} ·{' '}
                {item.telemetry.last_used_at
                  ? new Date(item.telemetry.last_used_at).toLocaleDateString('zh-CN')
                  : '尚无最近使用'}
              </p>
            )}
          </article>
        ))}
      </section>
      <LearningJourneyList
        key={jobId || 'curator'}
        version={`${version}:${jobTick}`}
        refreshed={refresh}
        jobId={jobId || undefined}
        showJobs={false}
        pendingOnly
      />
    </div>
  );
}

export function SkillEvolutionDialog({
  onClose,
  onChanged,
  version,
}: {
  onClose: () => void;
  onChanged: () => void;
  version: string;
}) {
  const [tab, setTab] = useState<'changes' | 'curator'>('changes');
  const [nested, setNested] = useState<'settings' | 'review' | null>(null);
  const settings = useResource<EvolutionSettings>(
    nested === 'settings' ? '/api/evolution/settings' : null,
    version,
  );
  return (
    <>
      <Dialog
        title="技能进化与整理"
        onClose={onClose}
        footer={
          <Button type="button" className="button-secondary" onClick={onClose}>
            完成
          </Button>
        }
      >
        <div className="mb-3 flex flex-wrap justify-end gap-2">
          <Button type="button" className="button-secondary" onClick={() => setNested('settings')}>
            自进化设置
          </Button>
          <Button type="button" className="button-secondary" onClick={() => setNested('review')}>
            立即复盘（技能）
          </Button>
        </div>
        <nav aria-label="技能进化视图" className="mb-4 flex flex-wrap gap-1 border-b border-line">
          <button
            type="button"
            className={evolutionTabClass}
            aria-pressed={tab === 'changes'}
            onClick={() => setTab('changes')}
          >
            技能变更与回滚
          </button>
          <button
            type="button"
            className={evolutionTabClass}
            aria-pressed={tab === 'curator'}
            onClick={() => setTab('curator')}
          >
            Curator 周期整理
          </button>
        </nav>
        {tab === 'changes' ? (
          <LearningJourneyList targetKind="skill" version={version} refreshed={onChanged} />
        ) : (
          <CuratorPanel version={version} refresh={onChanged} />
        )}
      </Dialog>
      {nested === 'settings' && settings.data?.slots && (
        <EvolutionSettingsDialog
          key={settings.data.revision}
          data={settings.data}
          onClose={() => setNested(null)}
          onReload={onChanged}
          onSaved={() => {
            setNested(null);
            onChanged();
          }}
        />
      )}
      {nested === 'settings' && !settings.data?.slots && (
        <Dialog title="自进化设置" onClose={() => setNested(null)}>
          <ErrorBox message={settings.error} />
          {settings.loading && <Loading />}
        </Dialog>
      )}
      {nested === 'review' && (
        <ManualReviewDialog
          initialType="skill_review"
          onClose={() => setNested(null)}
          onQueued={() => {
            setNested(null);
            setTab('changes');
            onChanged();
          }}
        />
      )}
    </>
  );
}
