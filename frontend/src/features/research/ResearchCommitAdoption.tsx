import { useRef, useState } from 'react';
import { ApiError, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox, Loading } from '../../shared/ui/components';
import { researchApi, type ResearchTask } from './api';
import type { CommitAdoptionPreview, CommitAdoptionSource, ResearchMap } from './mapApi';
import { AdoptionDetails } from './ResearchAdoption';

type Target = { id: string; name: string; head: { id: string }; archived_at: string | null };
type Request = {
  source_commit_id: string;
  node_id: string;
  target_branch_id: string;
  target_parent_id: string | null;
  expected_revision_id: string;
  expected_control_version: number;
};

export function ResearchCommitAdoption({ task, source, onClose, onSaved }: {
  task: ResearchTask; source: CommitAdoptionSource;
  onClose: () => void; onSaved: (next: ResearchTask, branch: string) => void;
}) {
  const [chosen, setChosen] = useState('');
  const [parent, setParent] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [preview, setPreview] = useState<{ request: Request; result: CommitAdoptionPreview } | null>(null);
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const base = researchApi(task.id);
  const branches = useResource<{ items: Target[]; control_version: number }>(base + '/branches', refresh);
  const eligible = branches.data?.items.filter(item => !item.archived_at && item.head.id !== source.revisionId) ?? [];
  const writing = task.write_branch_id ?? task.main_branch_id;
  const target = chosen ? eligible.find(item => item.id === chosen) : eligible.find(item => item.id === writing) ?? eligible[0];
  const map = useResource<ResearchMap>(target ? `${base}/map?revision_id=${encodeURIComponent(target.head.id)}` : null, refresh);
  const exists = map.data?.nodes.some(node => node.id === source.node.id);
  const parents = map.data?.nodes ?? [];
  const validParent = parent === 'root' || parents.some(node => node.id === parent);
  const canPreview = Boolean(target && map.data && (exists ? source.node.conclusion : validParent)
    && !branches.loading && !branches.error && !map.loading && !map.error);

  function showFailure(failure: unknown) {
    setError(messageOf(failure));
    if (failure instanceof ApiError && ['map_conflict', 'adoption_conflict', 'branch_archived'].includes(failure.code ?? '')) {
      setStale(true);
    }
  }

  async function makePreview() {
    if (!target || !branches.data || !canPreview || busy) return;
    const request = {
      source_commit_id: source.commitId, node_id: source.node.id, target_branch_id: target.id,
      target_parent_id: exists || parent === 'root' ? null : parent,
      expected_revision_id: target.head.id, expected_control_version: branches.data.control_version,
    };
    setBusy(true);
    setError('');
    try {
      const result = await send<CommitAdoptionPreview>(base + '/map/commit-adoption-preview', request);
      setPreview({ request, result });
    } catch (failure) {
      showFailure(failure);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!preview || !reason.trim() || busy || stale) return;
    const body = { ...preview.request, preview_hash: preview.result.preview_hash, reason: reason.trim() };
    const signature = JSON.stringify(body);
    if (operation.current?.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
    setBusy(true);
    setError('');
    try {
      const next = await send<{ task: ResearchTask }>(base + '/map/commit-adopt', { ...body, operation_id: operation.current.id });
      onSaved(next.task, preview.result.target_branch_name);
    } catch (failure) {
      showFailure(failure);
    } finally {
      setBusy(false);
    }
  }

  function refreshTarget() {
    setStale(false);
    setPreview(null);
    setError('');
    setRefresh(value => value + 1);
  }

  return <Dialog title="采纳固定提交的节点" onClose={onClose} busy={busy} footer={<>
    <Button type="button" className="button-secondary" disabled={busy} onClick={onClose}>取消</Button>
    {stale || branches.error || map.error
      ? <Button type="button" disabled={busy} onClick={refreshTarget}>刷新目标进展</Button>
      : preview ? <Button type="button" disabled={busy || !reason.trim()} onClick={() => void confirm()}>确认采纳</Button>
        : <Button type="button" disabled={busy || !canPreview} onClick={() => void makePreview()}>生成采纳预览</Button>}
  </>}>
    <div className="grid gap-3">
      <p className="m-0 text-sm font-semibold [overflow-wrap:anywhere]">{source.node.question}</p>
      <div className={`grid gap-3 ${map.data && !exists ? 'grid-cols-2 max-md:grid-cols-1' : ''}`}>
        <label className="m-0 grid min-w-0 gap-1 text-sm">目标分支
          <select value={target?.id ?? ''} onChange={event => {
            setChosen(event.target.value); setParent(''); setPreview(null); setError(''); setStale(false);
          }} disabled={busy || branches.loading}>
            <option value="" disabled>选择目标分支</option>
            {eligible.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        {map.data && !exists && <label className="m-0 grid min-w-0 gap-1 text-sm">目标上级主题
          <select value={validParent ? parent : ''} onChange={event => {
            setParent(event.target.value); setPreview(null); setError(''); setStale(false);
          }} disabled={busy}>
            <option value="">选择放置位置</option>
            <option value="root">作为顶层主题</option>
            {parents.map(node => <option key={node.id} value={node.id}>{node.question}</option>)}
          </select>
        </label>}
      </div>
      {!branches.loading && !branches.error && !eligible.length && <p className="m-0 text-sm text-control">暂无可采纳的目标分支。</p>}
      {(branches.loading || map.loading) && <Loading />}
      {exists && !source.node.conclusion && <p className="m-0 text-sm text-control">目标已有此节点，所选提交里暂无结论。</p>}
      {preview && <>
        <p className="m-0 text-sm text-control [overflow-wrap:anywhere]">
          来源「{preview.result.source_commit_message}」 · 目标「{preview.result.target_branch_name}」V{preview.result.target_revision_number}
          {' · '}{preview.result.target_parent_title ?? '顶层主题'}
        </p>
        <AdoptionDetails preview={preview.result} />
        {!!preview.result.omitted_relations.length && <div className="text-sm">
          <p className="m-0 font-medium">以下关联随源提交保留</p>
          <ul className="my-1 pl-5">{preview.result.omitted_relations.map(relation =>
            <li key={relation.target_id}>{relation.question} · {relation.description}</li>)}</ul>
        </div>}
        {!!preview.result.reopened_ancestors.length && <div className="text-sm">
          <p className="m-0 font-medium">一并回到待复核的上级主题</p>
          <ul className="my-1 pl-5">{preview.result.reopened_ancestors.map(node =>
            <li key={node.id}>{node.question}</li>)}</ul>
        </div>}
        <p className="m-0 text-sm text-control">采纳后，目标节点进入待复核。</p>
        <label className="m-0 grid gap-1 text-sm">采纳理由
          <textarea value={reason} rows={2} maxLength={1000} onChange={event => setReason(event.target.value)} disabled={busy} />
        </label>
      </>}
      <ErrorBox message={error || branches.error || map.error} />
    </div>
  </Dialog>;
}
