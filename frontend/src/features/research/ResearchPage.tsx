import { useRef, useState } from 'react';
import { ChevronRight, FileClock, FlaskConical, Pencil, Trash2 } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, messageOf, send, useResource, usePagedResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { Empty, ErrorBox, Loading } from '../../shared/ui/components';
import { BackLink, WorkbenchHeading, WorkbenchPanel } from '../../shared/ui/Workbench';
import { ResearchBrief } from './ResearchBrief';
import { ResearchBriefEditor } from './ResearchBriefEditor';
import { ResearchCommits } from './ResearchCommits';
import { ResearchControls } from './ResearchControls';
import { ResearchFocus } from './ResearchFocus';
import { isNewerResearchTask, researchApi, researchPath, researchPhases, researchStatuses, revisionSuffix } from './api';
import type { ResearchRevision, ResearchSummary, ResearchTask, RevisionSummary } from './api';

const formatDate = (date: string) =>
  new Date(date).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

export function ResearchList() {
  const [version, setVersion] = useState(0);
  const [deleting, setDeleting] = useState<ResearchSummary | null>(null);
  const [notice, setNotice] = useState('');
  const collection = usePagedResource<ResearchSummary>('/api/research-tasks', version);
  function refresh() {
    setDeleting(null);
    setVersion((previous) => previous + 1);
  }
  return (
    <div className="mx-auto max-w-4xl">
      <WorkbenchHeading
        eyebrow="研发"
        title="个人研发工作台"
        description="在任意会话的「+」菜单选择研发模式，在这里继续查看简报和固定版本。"
      />
      <ErrorBox message={collection.error} />
      {notice && <p className="text-sm text-muted" role="status">{notice}</p>}
      {collection.loading && <Loading />}
      {!collection.loading && !collection.error && collection.data.length === 0 && (
        <Empty title="暂无研发任务">
          <Link to="/conversations">打开会话启用研发</Link>
        </Empty>
      )}
      {collection.data.length > 0 && (
        <WorkbenchPanel title="研发任务">
          <ul className="m-0 list-none divide-y divide-line p-0">
            {collection.data.map((task) => (
              <li key={task.id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Link
                  to={researchPath(task.id)}
                  className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-2 rounded-lg py-3 text-sm hover:bg-canvas"
                >
                  <span className="font-semibold [overflow-wrap:anywhere]">{task.title}</span>
                  <span className="flex items-center gap-2 text-control">
                    <span className="rounded-full bg-canvas px-2 py-1 text-xs">{researchStatuses[task.status]}</span>
                    {formatDate(task.created_at)}
                    <ChevronRight size={16} aria-hidden="true" />
                  </span>
                </Link>
                <Button type="button" className="button-quiet p-2 text-danger"
                  aria-label={`删除研发任务：${task.title}`} onClick={() => setDeleting(task)}>
                  <Trash2 size={17} aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        </WorkbenchPanel>
      )}
      {collection.next && (
        <Button
          className="button-secondary mt-4"
          disabled={collection.loading}
          onClick={() => void collection.loadMore()}
        >
          加载更多任务
        </Button>
      )}
      {deleting && <DeleteResearchDialog task={deleting} onClose={() => setDeleting(null)}
        onRefresh={refresh} onDeleted={() => { setNotice('研发任务已删除。'); refresh(); }} />}
    </div>
  );
}

function DeleteResearchDialog({ task, onClose, onRefresh, onDeleted }: {
  task: ResearchSummary;
  onClose: () => void;
  onRefresh: () => void;
  onDeleted: () => void;
}) {
  // 网络失败时保留同一命令编号，重试只查询或完成原删除。
  const operation = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  async function confirm() {
    operation.current ??= crypto.randomUUID();
    setBusy(true);
    setError('');
    try {
      await send(`${researchApi(task.id)}/delete`, {
        operation_id: operation.current,
        expected_control_version: task.control_version,
      });
      onDeleted();
    } catch (failure) {
      setError(messageOf(failure));
      setStale(failure instanceof ApiError && failure.code === 'control_conflict');
    } finally {
      setBusy(false);
    }
  }
  return <Dialog title="删除研发任务？" busy={busy} onClose={onClose}
    footer={<>
      <Button type="button" className="button-secondary" disabled={busy} onClick={onClose}>返回</Button>
      {stale
        ? <Button type="button" className="button-secondary" onClick={onRefresh}>刷新任务列表</Button>
        : <Button type="button" className="button-secondary text-danger" disabled={busy} onClick={() => void confirm()}>
            {busy ? '正在删除…' : error ? '重试删除' : '删除任务'}
          </Button>}
    </>}>
    <p>「{task.title}」会从工作台移除，并解除它与会话的绑定。仍被引用的固定版本会保留。</p>
    <p>原会话、聊天消息、原始材料和已发布知识会保留。</p>
    <ErrorBox message={error} />
  </Dialog>;
}

export function RevisionHistory({ task, endpoint }: { task: ResearchTask; endpoint: string }) {
  const [before, setBefore] = useState<number | null>(null);
  const revisions = useResource<{ items: RevisionSummary[]; next_before: number | null }>(
    before === null ? endpoint : `${endpoint}?before=${before}`,
    task.head.id,
  );
  const history = researchPath(task.id) + revisionSuffix(task.write_branch_id ?? task.main_branch_id);
  return (
    <WorkbenchPanel title="自动修订" description="打开固定修订，查看当时保存的简报。">
      <ErrorBox message={revisions.error} />
      {revisions.loading && !revisions.data && <Loading />}
      {revisions.data && (
        <ul className="m-0 list-none divide-y divide-line p-0">
          {revisions.data.items.map((item) => (
            <li key={item.id}>
              <Link
                className="group block rounded-lg px-2 py-3 text-sm hover:bg-canvas"
                to={`${history}/${item.id}`}
              >
                <span className="flex min-w-0 items-center gap-1.5 font-semibold">
                  <FileClock size={15} aria-hidden="true" /> 版本 {item.number}
                  {item.id === task.head.id && (
                    <span className="ml-auto text-xs font-normal text-control">当前</span>
                  )}
                </span>
                <span className="mt-1 block text-control [overflow-wrap:anywhere]">
                  {item.reason}
                </span>
                <time dateTime={item.created_at} className="mt-1 block text-xs text-control">
                  {formatDate(item.created_at)}
                </time>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        {(revisions.data?.next_before ?? null) !== null && (
          <Button
            className="button-secondary"
            disabled={revisions.loading}
            onClick={() => setBefore(revisions.data?.next_before ?? null)}
          >
            更早版本
          </Button>
        )}
        {before !== null && (
          <Button
            className="button-secondary"
            disabled={revisions.loading}
            onClick={() => setBefore(null)}
          >
            回到最新
          </Button>
        )}
      </div>
    </WorkbenchPanel>
  );
}

function CurrentResearch({ loaded, onRefresh }: { loaded: ResearchTask; onRefresh: () => void }) {
  const [observed, setObserved] = useState<ResearchTask | null>(null);
  const [editing, setEditing] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [confirmation, setConfirmation] = useState(false);
  const [showCommits, setShowCommits] = useState(false);
  // 丢失旧响应后重放可能返回较早的回执；当前页仍以已知的最新修订为准。
  const task = observed && isNewerResearchTask(observed, loaded) ? observed : loaded;
  const endpoint = researchApi(task.id) + revisionSuffix(task.write_branch_id ?? task.main_branch_id);
  function remember(snapshot: ResearchTask) {
    setObserved((prior) => (prior && isNewerResearchTask(prior, snapshot) ? prior : snapshot));
  }
  return (
    <div className="mx-auto max-w-6xl">
      <BackLink to={task.home_conversation_id ? `/chat/${task.home_conversation_id}` : '/research'}>
        {task.home_conversation_id ? '返回会话' : '返回研发工作台'}
      </BackLink>
      <WorkbenchHeading
        eyebrow="研发工作台"
        title={task.title}
        actions={
          !editing &&
          task.status === 'open' && (
            <Button
              onClick={() => {
                setEditing(true);
                setConfirmation(false);
              }}
            >
              <Pencil size={16} aria-hidden="true" /> 编辑简报
            </Button>
          )
        }
      />
      <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-control">
        <Link to={researchPath(task.id) + '/map'} className="button button-secondary text-xs">研究地图</Link>
        <Link to={researchPath(task.id) + '/experiments'} className="button button-secondary text-xs">独立实验</Link>
        <Button type="button" className="button-secondary text-xs" aria-expanded={showCommits}
          onClick={() => setShowCommits((value) => !value)}>分支与提交</Button>
        <span className="inline-flex items-center gap-2 rounded-full bg-brand/10 px-3 py-1 font-semibold text-brand">
          <FlaskConical size={15} aria-hidden="true" />{' '}
          {researchPhases[task.head.state.phase] ?? task.head.state.phase}
        </span>
        <span role="status">{researchStatuses[task.status] ?? task.status}</span>
        <span>{task.write_branch_name ?? 'main'} · 版本 {task.head.number}</span>
        {!editing && task.status === 'open' && (
          <Button type="button" className="button-secondary" onClick={() => setAdjusting(true)}>调整研究焦点</Button>
        )}
        {!editing && <ResearchControls task={task} onChanged={(snapshot) => { remember(snapshot); onRefresh(); }} />}
        {confirmation && (
          <span role="status" className="text-brand">
            简报已保存
          </span>
        )}
      </div>
      {showCommits && !editing && <div className="mb-5"><ResearchCommits key={task.id} task={task}
        onChanged={(next) => { remember(next); onRefresh(); }} /></div>}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
        {editing ? (
          <ResearchBriefEditor
            initial={task}
            endpoint={endpoint}
            onCancel={() => setEditing(false)}
            onObserved={remember}
            onSaved={(response) => {
              remember(response);
              setConfirmation(true);
              setEditing(false);
              onRefresh();
            }}
          />
        ) : (
          <ResearchBrief brief={task.head.state.brief} materials={task.head.state.material_refs} />
        )}
        <RevisionHistory key={task.head.branch_id} task={task} endpoint={endpoint} />
      </div>
      {adjusting && <ResearchFocus task={task} onClose={() => setAdjusting(false)}
        onRefresh={() => { setAdjusting(false); onRefresh(); }}
        onSaved={(snapshot) => { remember(snapshot); setAdjusting(false); onRefresh(); }} />}
    </div>
  );
}

function FixedRevision({
  taskId,
  branchId,
  revisionId,
}: {
  taskId: string;
  branchId: string;
  revisionId: string;
}) {
  const revision = useResource<ResearchRevision>(
    researchApi(taskId) + revisionSuffix(branchId) + `/${encodeURIComponent(revisionId)}`,
  );
  return (
    <div className="mx-auto max-w-4xl">
      <BackLink to={revision.data?.task_removed ? '/research' : researchPath(taskId)}>
        {revision.data?.task_removed ? '返回研发工作台' : '返回研发简报'}
      </BackLink>
      <ErrorBox message={revision.error} />
      {revision.loading && <Loading />}
      {revision.data && (
        <>
          <WorkbenchHeading
            eyebrow="版本历史"
            title={`固定修订 ${revision.data.number}`}
            description={`${revision.data.reason} · ${formatDate(revision.data.created_at)}`}
          />
          <ResearchBrief brief={revision.data.state.brief} materials={revision.data.state.material_refs} />
        </>
      )}
    </div>
  );
}

export function ResearchPage() {
  const { taskId, branchId, revisionId } = useParams();
  const [reload, setReload] = useState(0);
  const task = useResource<ResearchTask>(
    taskId && !revisionId ? researchApi(taskId) : null,
    reload,
  );
  if (taskId && branchId && revisionId)
    return <FixedRevision taskId={taskId} branchId={branchId} revisionId={revisionId} />;
  return (
    <>
      {task.data && (
        <CurrentResearch
          key={task.data.id}
          loaded={task.data}
          onRefresh={() => setReload((v) => v + 1)}
        />
      )}
      {task.error && (
        <div className="mx-auto max-w-4xl">
          {!task.data && <BackLink to="/research">返回研发工作台</BackLink>}
          <ErrorBox message={task.error} />
          <Button className="button-secondary" onClick={() => setReload((v) => v + 1)}>
            重新读取简报
          </Button>
        </div>
      )}
      {task.loading && !task.data && <Loading />}
    </>
  );
}
