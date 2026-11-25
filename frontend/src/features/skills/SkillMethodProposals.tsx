import { useState } from 'react';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { EvolutionDiffView } from '../evolution/EvolutionDiffView';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox, Loading } from '../../shared/ui/components';
import { CompletedResearchTrial } from '../research/CompletedResearchTrial';

type Proposal = {
  id: string;
  title: string;
  state: keyof typeof stateLabel;
  base_revision: number;
  published_revision: number | null;
  review_hash: string;
  scope: string;
  diff: string;
  evidence: { report_id: string; report_title: string; decision: string; finding: string; limitation: string };
};

const stateLabel = {
  preparing: '准备中', pending_review: '待审阅', publish_failed: '发布失败，可重试',
  published: '已发布', rejected: '已驳回', expired: '已过期',
};
const route = '/api/skills/proposals';

export function SkillMethodProposals({ onClose, onPublished }: { onClose: () => void; onPublished: () => void }) {
  const [version, setVersion] = useState(0);
  const cases = useResource<{ report: { id: string } }>('/api/research-trial/completed', version);
  const proposals = useResource<{ items: Proposal[] }>(route, version);
  const [selection, setSelection] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const proposal = selection ?? proposals.data?.items[0];
  const canReview = proposal?.state === 'pending_review' || proposal?.state === 'publish_failed';
  const reload = () => setVersion((previous) => previous + 1);

  async function prepare() {
    const report = cases.data?.report;
    if (!report) return;
    setBusy(true);
    setError('');
    try {
      setSelection(await send<Proposal>(route, { report_id: report.id }));
      reload();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  async function review(action: 'publish' | 'reject') {
    if (!proposal) return;
    setBusy(true);
    setError('');
    try {
      setSelection(await send<Proposal>(`${route}/${proposal.id}/review`, { action, review_hash: proposal.review_hash }));
      reload();
      if (action === 'publish') onPublished();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  return <Dialog title="方法改进提案" onClose={onClose} busy={busy} footer={canReview && <>
    <Button className="button-secondary" disabled={busy} onClick={() => void review('reject')}>驳回提案</Button>
    <Button disabled={busy} onClick={() => void review('publish')}>确认发布修订</Button>
  </>}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Button className="button-secondary" disabled={busy || !cases.data} onClick={() => void prepare()}>
        从已验收案例准备提案
      </Button>
      {(cases.error || proposals.error) && <Button className="button-secondary" onClick={reload}>重试读取</Button>}
      {busy && <span role="status" className="text-sm text-control">正在处理…</span>}
    </div>
    {(cases.loading || proposals.loading) && !proposal && <Loading />}
    <ErrorBox message={error || cases.error || proposals.error} />
    {!!proposals.data && proposals.data.items.length > 1 && (
      <div className="my-3 flex flex-wrap gap-2" aria-label="提案历史">
        {proposals.data.items.map((item) => <Button className="button-secondary" key={item.id}
          disabled={busy} aria-pressed={item.id === proposal?.id} onClick={() => { setSelection(item); setError(''); }}>
          修订 {item.base_revision} · {stateLabel[item.state]}
        </Button>)}
      </div>
    )}
    {!proposal && proposals.data && <p className="mb-0 text-sm text-control">暂无待审阅的方法提案。</p>}
    {proposal && <article className="mt-4 grid gap-4" aria-label="提案详情">
      <div className="rounded-xl border border-line bg-canvas p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="m-0 text-base">{proposal.title}</h3>
          <span role="status" className="text-sm font-medium text-brand">
            {proposal.state === 'published' ? `已发布修订 ${proposal.published_revision}` : stateLabel[proposal.state]}
          </span>
        </div>
        <p className="mb-0 mt-2 text-sm text-control">证据核对 · 基于修订 {proposal.base_revision}</p>
        <p className="mb-0 mt-2 text-sm">{proposal.scope}</p>
      </div>
      <section aria-label="固定依据" className="grid gap-2 text-sm leading-relaxed">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="m-0 text-sm font-semibold">{proposal.evidence.report_title} · 已验收</h3>
          {cases.data?.report.id === proposal.evidence.report_id && <CompletedResearchTrial />}
        </div>
        <p className="m-0">{proposal.evidence.decision}</p>
        <p className="m-0">{proposal.evidence.finding}</p>
        <p className="m-0 text-control">{proposal.evidence.limitation}</p>
      </section>
      <section aria-label="技能正文差异">
        <h3 className="mb-2 mt-0 text-sm font-semibold">SKILL.md 变更</h3>
        <EvolutionDiffView diff={{ unified_diff: proposal.diff }} />
      </section>
    </article>}
  </Dialog>;
}
