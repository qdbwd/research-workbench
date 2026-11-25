import { useRef, useState } from 'react';
import { GitBranch, GitCommitHorizontal, GitCompareArrows } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ApiError, api, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox, Loading } from '../../shared/ui/components';
import { DetailDialog, WorkbenchPanel } from '../../shared/ui/Workbench';
import { researchApi, type ResearchRevision, type ResearchTask } from './api';
import { ResearchCommitAdoption } from './ResearchCommitAdoption';
import { ResearchCommitMap, type CommitNodeActions } from './ResearchCommitMap';
import type { CommitAdoptionSource, MapComparison, ResearchMap } from './mapApi';

type Branch = {
  id: string; name: string; head: ResearchRevision; latest_commit_id: string | null;
  fork_commit_id: string | null; archived_at: string | null;
};
type CommitEntry = { id: string; branch_id: string; number: number; message: string; created_at: string };
type Snapshot = {
  revision: ResearchRevision;
  map: ResearchMap;
  material_refs: { source_id: string; source_version_id: string; title: string }[];
  knowledge_refs: { knowledge_version_id: string; title: string; version: number }[];
  experiments: { id: string; status: string; specification: { question: string; script?: string | null }; receipt?: unknown }[];
  deliveries: { id: string; status: string; content: { title: string; diagnosis: string; recommendations: string; limitations?: string } }[];
};
type Commit = CommitEntry & { snapshot: Snapshot };
type Preview = { preview_hash: string; snapshot: Snapshot };
type Difference = {
  map: MapComparison;
  brief_changed: boolean;
  assets: { kind: string; id: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null }[];
};

const branchPath = (base: string, branch: string) => `${base}/branches/${encodeURIComponent(branch)}`;
const statusNames: Record<string, string> = {
  awaiting_approval: '待批准', approved: '已批准', dispatched: '已派发', running: '运行中',
  cancel_requested: '待取消', completed: '已完成', cancelled: '已取消', failed: '失败',
  timed_out: '超时', unknown: '待核对', stale: '已过时', finalizing: '收尾中', blocked: '已阻塞',
  submitted: '待验收', accepted: '已验收', changes_requested: '待修改', superseded: '已替换',
};

function FixedAssets({ snapshot, preview = false }: { snapshot: Snapshot; preview?: boolean }) {
  return <div className="grid gap-3 text-sm">
    <p className="m-0 font-medium">{snapshot.revision.state.brief.goal}</p>
    {!!snapshot.material_refs.length && <ul className="m-0 grid gap-1 pl-5" aria-label="固定资料">
      {snapshot.material_refs.map((item) => <li key={item.source_version_id}>{preview ? item.title :
        <Link to={`/sources/${item.source_id}?version=${item.source_version_id}`} target="_blank" rel="noreferrer">{item.title}</Link>}</li>)}
    </ul>}
    {!!snapshot.knowledge_refs.length && <ul className="m-0 grid gap-1 pl-5" aria-label="固定知识版本">
      {snapshot.knowledge_refs.map((item) => <li key={item.knowledge_version_id}>{item.title} · V{item.version}</li>)}
    </ul>}
    {snapshot.experiments.map((item) => {
      const title = `${item.specification.question} · ${statusNames[item.status] ?? item.status}`;
      return preview ? <p key={item.id} className="m-0">{title}</p> : <DetailDialog key={item.id} title={title}>
        {item.specification.script ? <section>
          <h3 className="text-sm font-semibold">固定实验脚本</h3>
          <pre className="max-h-64 overflow-auto text-xs">{item.specification.script}</pre>
        </section> : <p className="text-control">此记录暂无可查看的脚本正文。</p>}
        {Boolean(item.receipt) && <section>
          <h3 className="text-sm font-semibold">实验回执</h3>
          <pre className="max-h-64 overflow-auto text-xs">{JSON.stringify(item.receipt, null, 2)}</pre>
        </section>}
      </DetailDialog>;
    })}
    {snapshot.deliveries.map((item) => {
      const title = `${item.content.title} · ${statusNames[item.status] ?? item.status}`;
      return preview ? <p key={item.id} className="m-0">{title}</p> : <DetailDialog key={item.id} title={title}>
        <div className="whitespace-pre-wrap [overflow-wrap:anywhere]">
          {[item.content.diagnosis, item.content.recommendations, item.content.limitations].filter(Boolean).join('\n\n')}
        </div>
      </DetailDialog>;
    })}
  </div>;
}

function VersionDialog({ base, taskTitle, fromCommit, onClose, onSaved, onForked }: {
  base: string; taskTitle: string; fromCommit?: Commit; onClose: () => void;
  onSaved: (commit: Commit) => void; onForked: (branch: Branch) => void;
}) {
  const [value, setValue] = useState('');
  const [retry, setRetry] = useState(0);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<{ identity: string; operationId: string } | null>(null);
  const preview = useResource<Preview>(fromCommit ? null : `${base}/commit-preview`, retry);
  const payload = fromCommit ? { source_commit_id: fromCommit.id, name: value.trim() }
    : { preview_hash: preview.data?.preview_hash, message: value.trim() };

  async function save() {
    if (saving || !value.trim() || (!fromCommit && (preview.loading || !preview.data || stale))) return;
    const identity = JSON.stringify(payload);
    if (request.current?.identity !== identity) request.current = { identity, operationId: crypto.randomUUID() };
    const body = { ...payload, operation_id: request.current.operationId };
    setSaving(true);
    setError('');
    try {
      if (fromCommit) onForked(await send<Branch>(base, body));
      else onSaved(await send<Commit>(`${base}/commits`, body));
    } catch (failure) {
      setError(messageOf(failure));
      if (failure instanceof ApiError && failure.code === 'commit_conflict') setStale(true);
    } finally {
      setSaving(false);
    }
  }

  return <Dialog title={fromCommit ? '从提交创建分支' : '预览研发提交'} onClose={onClose} busy={saving} footer={<>
    {(stale || preview.error) && <Button type="button" className="button-secondary" onClick={() => {
      setError(''); setStale(false); setRetry((previous) => previous + 1);
    }}>重新预览</Button>}
    <Button type="button" disabled={saving || !value.trim() || (!fromCommit && (stale || preview.loading || Boolean(preview.error) || !preview.data))}
      onClick={() => void save()}>{fromCommit ? '创建分支' : '确认提交'}</Button>
  </>}>
    <label className="grid gap-2 text-sm">{fromCommit ? '研发分支名称' : '提交说明'}
      <input value={value} onChange={(event) => setValue(event.target.value)} maxLength={fromCommit ? 100 : 1000} disabled={saving} />
    </label>
    <ErrorBox message={error || preview.error} />
    {preview.loading && <Loading />}
    {preview.data && !preview.loading && !preview.error && <div className="mt-4 grid gap-4 border-t border-line pt-4">
      <FixedAssets snapshot={preview.data.snapshot} preview />
      {!!preview.data.snapshot.map.nodes.length && <ResearchCommitMap map={preview.data.snapshot.map} taskTitle={taskTitle} />}
    </div>}
    {fromCommit && <p className="mb-0 text-sm text-control">以「{fromCommit.message}」的固定进展建立独立分支。</p>}
  </Dialog>;
}

function DiffView({ difference, taskTitle, nodeActions }: {
  difference: Difference; taskTitle: string; nodeActions?: CommitNodeActions;
}) {
  const labels: Record<string, string> = { material_refs: '资料', knowledge_refs: '知识', experiments: '实验', deliveries: '成果' };
  return <div className="grid gap-4">
    {!difference.brief_changed && !difference.assets.length && !difference.map.changes.length &&
      <p className="m-0 text-sm text-control">没有差异。</p>}
    {difference.brief_changed && <p className="m-0 text-sm text-control">研发简报已变更。</p>}
    {!!difference.assets.length && <ul className="m-0 grid gap-1 pl-5 text-sm" aria-label="资产变更">
      {difference.assets.map((change) => {
        const entry = change.after ?? change.before;
        const content = entry?.content as { title?: string } | undefined;
        const spec = entry?.specification as { question?: string } | undefined;
        return <li key={`${change.kind}:${change.id}`}>
          {change.before == null ? '新增' : change.after == null ? '移除' : '变更'}{labels[change.kind]} · {String(entry?.title ?? content?.title ?? spec?.question ?? change.id)}
        </li>;
      })}
    </ul>}
    <ResearchCommitMap map={difference.map.after} difference={difference.map} taskTitle={taskTitle} nodeActions={nodeActions} />
  </div>;
}

function CompareCommits({ base, branches, branch, taskTitle, onAdopt }: {
  base: string; branches: Branch[]; branch: Branch; taskTitle: string;
  onAdopt?: (source: CommitAdoptionSource) => void;
}) {
  const [rightBranch, setRightBranch] = useState(branches.find((item) => item.id !== branch.id)?.id ?? branch.id);
  const [leftId, setLeftId] = useState(branch.latest_commit_id ?? '');
  const [rightId, setRightId] = useState(branches.find((item) => item.id === rightBranch)?.latest_commit_id ?? '');
  const left = useResource<{ items: CommitEntry[] }>(`${branchPath(base, branch.id)}/commits`);
  const right = useResource<{ items: CommitEntry[] }>(`${branchPath(base, rightBranch)}/commits`);
  const diff = useResource<Difference & { common_commit_id: string | null }>(leftId && rightId
    ? `${base}/commit-comparison?before=${encodeURIComponent(leftId)}&after=${encodeURIComponent(rightId)}` : null);
  return <div className="grid gap-4">
    <div className="grid gap-3 sm:grid-cols-3">
      <label className="grid gap-1 text-sm">基线提交<select value={leftId} onChange={(event) => setLeftId(event.target.value)}>
        <option value="">选择提交</option>{left.data?.items?.map((item) => <option key={item.id} value={item.id}>{item.message}</option>)}
      </select></label>
      <label className="grid gap-1 text-sm">对照分支<select value={rightBranch} onChange={(event) => {
        setRightBranch(event.target.value);
        setRightId(branches.find((item) => item.id === event.target.value)?.latest_commit_id ?? '');
      }}>{branches.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label className="grid gap-1 text-sm">对照提交<select value={rightId} onChange={(event) => setRightId(event.target.value)}>
        <option value="">选择提交</option>{right.data?.items?.map((item) => <option key={item.id} value={item.id}>{item.message}</option>)}
      </select></label>
    </div>
    <ErrorBox message={left.error || right.error || diff.error} />
    {(left.loading || right.loading || diff.loading) && <Loading />}
    {diff.data && <>
      <p className="m-0 text-sm text-control">共同起点 · {diff.data.common_commit_id ?? '暂无'}</p>
      <DiffView difference={diff.data} taskTitle={taskTitle} nodeActions={onAdopt ? (before, after) => (
        <div className="flex flex-wrap gap-2">
          {before && <Button type="button" className="button-secondary text-xs" onClick={() =>
            onAdopt({ node: before, commitId: leftId, revisionId: diff.data!.map.before.revision_id })}>采纳基线节点</Button>}
          {after && <Button type="button" className="button-secondary text-xs" onClick={() =>
            onAdopt({ node: after, commitId: rightId, revisionId: diff.data!.map.after.revision_id })}>采纳对照节点</Button>}
        </div>
      ) : undefined} />
    </>}
  </div>;
}

function WriteBranchDialog({ task, branch, onClose, onChanged }: {
  task: ResearchTask; branch: Branch; onClose: () => void; onChanged: (task: ResearchTask) => void;
}) {
  const operation = useRef<string | null>(null);
  const [expectedWrite] = useState(task.write_version ?? 1);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState('');
  const base = researchApi(task.id);

  async function confirm() {
    operation.current ??= crypto.randomUUID();
    setBusy(true);
    setError('');
    try {
      const next = await send<ResearchTask>(base + '/write-branch', {
        operation_id: operation.current, branch_id: branch.id, expected_write_version: expectedWrite,
      });
      onChanged(next);
      onClose();
    } catch (failure) {
      setError(messageOf(failure));
      setStale(failure instanceof ApiError && failure.code === 'write_branch_conflict');
    } finally {
      setBusy(false);
    }
  }

  async function reload() {
    setBusy(true);
    try {
      onChanged(await api<ResearchTask>(base));
      onClose();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  return <Dialog title="切换后续消息的写入分支" onClose={onClose} busy={busy}
    footer={stale
      ? <Button type="button" className="button-secondary" disabled={busy} onClick={() => void reload()}>刷新分支</Button>
      : <Button type="button" disabled={busy} onClick={() => void confirm()}>确认切换</Button>}>
    <p className="m-0 text-sm">后续消息将写入 <strong>{branch.name}</strong>。已提交的运行继续使用各自绑定的分支。</p>
    <ErrorBox message={error} />
  </Dialog>;
}

function BranchLifecycleDialog({ action, task, branch, onClose, onChanged, onRefresh }: {
  action: 'archive' | 'unarchive' | 'restart'; task: ResearchTask; branch: Branch;
  onClose: () => void; onChanged: (task: ResearchTask) => void; onRefresh: () => void;
}) {
  const [version] = useState(task.control_version);
  const operation = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const archive = action === 'archive';
  const unarchive = action === 'unarchive';
  const base = researchApi(task.id);

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError('');
    operation.current ??= crypto.randomUUID();
    try {
      if (stale) {
        onChanged(await api<ResearchTask>(base));
      } else if (archive || unarchive) {
        await send(`${branchPath(base, branch.id)}/${archive ? 'archive' : 'unarchive'}`, {
          operation_id: operation.current, expected_revision_id: branch.head.id,
        });
      } else {
        onChanged(await send<ResearchTask>(`${branchPath(base, branch.id)}/restart`, {
          operation_id: operation.current, expected_control_version: version,
        }));
      }
      onRefresh();
      onClose();
    } catch (failure) {
      setError(messageOf(failure));
      if (failure instanceof ApiError && ['branch_conflict', 'control_conflict'].includes(failure.code ?? '')) setStale(true);
    } finally {
      setBusy(false);
    }
  }

  const title = archive ? '归档研发分支' : unarchive ? '恢复已归档分支' : '从固定提交开始新一轮';
  const confirmLabel = stale ? '刷新后再确认' : archive ? '确认归档' : unarchive ? '确认恢复为活跃分支' : '确认开始新一轮';

  return <Dialog title={title} onClose={onClose} busy={busy}
    footer={<Button type="button" disabled={busy} onClick={() => void submit()}>
      {confirmLabel}
    </Button>}>
    <p className="m-0 text-sm">
      <strong>{branch.name}</strong>{archive
        ? ' 将变为只读。历史提交和资料仍可查看。'
        : unarchive
          ? ' 将恢复为活跃分支，可重新切换为写入分支；不会自动改变当前会话写入分支，也不会复活已结束的旧授权或实验。'
          : ' 将承接固定提交并成为后续消息的写入分支。继续在原会话发送新消息即可开始工作。'}
    </p>
    <ErrorBox message={error} />
  </Dialog>;
}

export function ResearchCommits({ task, onChanged }: { task: ResearchTask; onChanged: (task: ResearchTask) => void }) {
  const base = researchApi(task.id);
  const [refresh, setRefresh] = useState(0);
  const [viewBranch, setViewBranch] = useState(task.write_branch_id ?? task.main_branch_id);
  const [selected, setSelected] = useState('');
  const [action, setAction] = useState<'save' | 'fork' | null>(null);
  const [writeTarget, setWriteTarget] = useState<Branch | null>(null);
  const [lifecycle, setLifecycle] = useState<{ action: 'archive' | 'unarchive' | 'restart'; branch: Branch } | null>(null);
  const [adoption, setAdoption] = useState<CommitAdoptionSource | null>(null);
  const [notice, setNotice] = useState('');
  const [comparing, setComparing] = useState(false);
  const branch = branchPath(base, viewBranch);
  const branches = useResource<{ items: Branch[] }>(`${base}/branches`, refresh);
  const history = useResource<{ items: CommitEntry[] }>(`${branch}/commits`, refresh);
  const fixed = useResource<Commit>(selected ? `${branch}/commits/${encodeURIComponent(selected)}` : null);
  const working = useResource<{ difference: Difference | null }>(!selected && !comparing ? `${branch}/working-diff` : null, refresh);
  const items = branches.data?.items ?? [];
  const viewed = items.find((item) => item.id === viewBranch);
  const writingId = task.write_branch_id ?? task.main_branch_id;
  const ended = task.status === 'closed' || task.status === 'completed';
  function switchBranch(id: string) {
    setViewBranch(id);
    setSelected('');
    setComparing(false);
  }
  return <div className="grid gap-4">
    <WorkbenchPanel title="版本历史">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <label className="grid min-w-0 gap-1 text-sm">查看分支<select value={viewBranch} onChange={(event) => switchBranch(event.target.value)}>
          {items.length ? items.map((item) => <option key={item.id} value={item.id}>{item.name}{item.archived_at ? '（已归档）' : ''}</option>)
            : <option value={viewBranch}>{task.write_branch_name ?? 'main'}</option>}
        </select></label>
        <div className="flex flex-wrap gap-2">
          <Button type="button" className="button-secondary" aria-pressed={comparing} disabled={!viewed?.latest_commit_id} onClick={() => { setComparing(!comparing); setSelected(''); }}>
            <GitCompareArrows size={16} aria-hidden="true" /> 跨分支对比
          </Button>
          <Button type="button" className="button-primary" disabled={!viewed || Boolean(viewed.archived_at) || Boolean(selected) || comparing} onClick={() => setAction('save')}>
            <GitCommitHorizontal size={16} aria-hidden="true" /> 预览提交
          </Button>
        </div>
      </div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        {task.home_conversation_id && <p className="m-0 text-xs text-control">
          {ended ? '结束前写入' : '当前会话写入'} {task.write_branch_name ?? 'main'}
        </p>}
        {viewed && !viewed.archived_at && viewed.id !== writingId && <div className="flex flex-wrap gap-2">
          {task.home_conversation_id && task.status === 'open' &&
            <Button type="button" className="button-secondary text-xs" onClick={() => setWriteTarget(viewed)}>将后续消息写入此分支</Button>}
          {task.home_conversation_id && ended && viewed.fork_commit_id && viewed.head.number === 1 &&
            <Button type="button" className="button-secondary text-xs" onClick={() => setLifecycle({ action: 'restart', branch: viewed })}>在此分支开始新一轮</Button>}
          <Button type="button" className="button-secondary text-xs" onClick={() => setLifecycle({ action: 'archive', branch: viewed })}>归档此分支</Button>
        </div>}
        {viewed && viewed.archived_at && <div className="flex flex-wrap gap-2">
          <Button type="button" className="button-secondary text-xs" onClick={() => setLifecycle({ action: 'unarchive', branch: viewed })}>恢复为活跃分支</Button>
        </div>}
      </div>
      <ErrorBox message={branches.error || history.error || fixed.error || working.error} />
      {notice && <p className="m-0 text-sm text-brand" role="status">{notice}</p>}
      {(branches.loading || history.loading || fixed.loading || working.loading) && <Loading />}
      {!history.loading && !history.error && !history.data?.items?.length && <p className="m-0 text-sm text-control">此分支还没有正式提交。</p>}
      {Boolean(history.data?.items?.length) && <ul className="m-0 list-none divide-y divide-line p-0">
        {history.data!.items.map((item) => <li key={item.id}>
          <button type="button" aria-pressed={selected === item.id} onClick={() => { setSelected(item.id); setComparing(false); }}
            className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-3 text-left text-sm hover:bg-canvas aria-pressed:bg-sidebar">
            <span className="min-w-0 [overflow-wrap:anywhere]"><strong className="mr-2 text-xs text-brand">#{item.number}</strong>{item.message}</span>
            <time dateTime={item.created_at} className="shrink-0 text-xs text-control">{new Date(item.created_at).toLocaleDateString('zh-CN')}</time>
          </button>
        </li>)}
      </ul>}
    </WorkbenchPanel>
    {comparing && viewed && <CompareCommits key={viewBranch} base={base} branch={viewed} branches={items} taskTitle={task.title}
      onAdopt={task.status === 'open' ? setAdoption : undefined} />}
    {!selected && !comparing && working.data?.difference && <>
      <h2 className="m-0 text-base font-semibold">当前进展与提交基线</h2>
      <DiffView difference={working.data.difference} taskTitle={task.title} />
    </>}
    {fixed.data && <>
      <WorkbenchPanel title={fixed.data.message} description="提交快照 · 只读">
        <div className="mb-4 flex flex-wrap gap-2">
          <Button type="button" className="button-secondary" onClick={() => setSelected('')}>返回当前进展</Button>
          <Button type="button" className="button-secondary" onClick={() => setAction('fork')}>
            <GitBranch size={16} aria-hidden="true" /> 从此提交创建分支
          </Button>
        </div>
        <FixedAssets snapshot={fixed.data.snapshot} />
      </WorkbenchPanel>
      <ResearchCommitMap key={fixed.data.id} map={fixed.data.snapshot.map} taskTitle={task.title}
        nodeActions={task.status === 'open' ? (_, node) => node && <div>
          <Button type="button" className="button-secondary text-xs" onClick={() => setAdoption({
            node, commitId: fixed.data!.id, revisionId: fixed.data!.snapshot.map.revision_id,
          })}>从此提交采纳节点</Button>
        </div> : undefined} />
    </>}
    {action && <VersionDialog taskTitle={task.title} base={action === 'fork' ? `${base}/branches` : branch} fromCommit={action === 'fork' ? fixed.data ?? undefined : undefined}
      onClose={() => setAction(null)} onSaved={(commit) => { setAction(null); setSelected(commit.id); setRefresh((value) => value + 1); }}
      onForked={(created) => { setAction(null); switchBranch(created.id); setRefresh((value) => value + 1); }} />}
    {writeTarget && <WriteBranchDialog task={task} branch={writeTarget} onClose={() => setWriteTarget(null)} onChanged={onChanged} />}
    {lifecycle && <BranchLifecycleDialog task={task} {...lifecycle} onChanged={onChanged}
      onRefresh={() => setRefresh((value) => value + 1)} onClose={() => setLifecycle(null)} />}
    {adoption && <ResearchCommitAdoption task={task} source={adoption} onClose={() => setAdoption(null)}
      onSaved={(next, target) => {
        setAdoption(null);
        setNotice(`已写入「${target}」的当前进展。`);
        setRefresh(value => value + 1);
        onChanged(next);
      }} />}
  </div>;
}
