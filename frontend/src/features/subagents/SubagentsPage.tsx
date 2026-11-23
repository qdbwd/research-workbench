import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { Empty, ErrorBox, Loading, PageTitle } from '../../shared/ui/components';
import { HelpHint } from '../../shared/ui/Workbench';
import { AgentSettingsTabs } from '../tools/AgentSettingsTabs';
import { EffectiveCapabilities } from '../tools/EffectiveCapabilities';
import type { Role } from './SubagentFields';

function RoleCard({ row, changed }: { row: Role; changed: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function act(body: Record<string, unknown>) {
    setBusy(true);
    setError('');
    try {
      await send(
        `/api/subagents/${encodeURIComponent(row.id)}`,
        { expected_revision: row.revision, ...body },
        'PATCH',
      );
      setConfirming(false);
      changed();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      aria-label={row.display_name}
      className="rounded-2xl border border-line bg-white p-6 max-md:p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="m-0 text-lg font-semibold">{row.display_name}</h2>
          <p className="mb-0 mt-1 text-xs text-muted">{row.name}</p>
        </div>
        <span className="rounded-md bg-canvas px-2.5 py-1 text-xs text-muted">
          {row.enabled ? '已启用' : '已停用'} · 修订 {row.revision}
        </span>
      </div>
      <p className="text-sm">{row.description}</p>
      <p className="text-xs text-muted">
        {(row.resolved_tools ?? row.tools).length} 项已选工具 · {row.skills.length} 项 Skills
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Link
          className="button button-secondary"
          to={`/subagents/${encodeURIComponent(row.id)}/edit`}
        >
          编辑配置
        </Link>
        <EffectiveCapabilities roleId={row.id} version={row.revision} />
        <Button
          className="button-quiet"
          disabled={busy}
          onClick={() => void act({ enabled: !row.enabled })}
        >
          {row.enabled ? '停用' : '启用'}
        </Button>
        <Button
          className="button-quiet text-danger"
          disabled={busy}
          onClick={() => setConfirming(true)}
        >
          删除
        </Button>
      </div>
      <ErrorBox message={error} />
      {error && !confirming && (
        <Button className="button-secondary" disabled={busy} onClick={changed}>
          刷新配置
        </Button>
      )}
      {confirming && (
        <Dialog
          title={`删除${row.display_name}？`}
          busy={busy}
          onClose={() => setConfirming(false)}
          footer={
            <>
              <Button
                className="button-secondary"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                取消删除
              </Button>
              <Button
                className="button-secondary text-danger"
                disabled={busy}
                onClick={() => void act({ deleted: true })}
              >
                确认删除此角色
              </Button>
            </>
          }
        >
          <p>
            确认删除「{row.display_name}」（{row.name}
            ）？旧绑定将停止，已产生的任务结果保留，名称不可复用。
          </p>
          <ErrorBox message={error} />
          {error && (
            <Button className="button-secondary" disabled={busy} onClick={changed}>
              刷新配置
            </Button>
          )}
        </Dialog>
      )}
    </section>
  );
}

/** 历史直达链接继续可读；常规目录不再展开历史或内嵌长表单。 */
export function SubagentsPage() {
  const [version, setVersion] = useState(0);
  const [search, setSearch] = useSearchParams();
  const result = useResource<Role[]>('/api/subagents', version);
  const historyId = search.get('history');
  const history = useResource<Role[]>(
    historyId ? `/api/subagents/${encodeURIComponent(historyId)}/history` : null,
    version,
  );
  const changed = () => setVersion((value) => value + 1);
  const closeHistory = () => setSearch((previous) => {
    const next = new URLSearchParams(previous);
    next.delete('history');
    return next;
  }, { replace: true });
  return (
    <>
      <PageTitle title="智能体设置" />
      <AgentSettingsTabs />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1"><h2 className="m-0 text-lg font-semibold">子智能体</h2>
          <HelpHint label="子智能体说明">角色只读、无递归，继承主任务的模型并共享预算，最多两个并发。新任务使用新配置；停用或收窄许可会影响后续调用。</HelpHint>
        </div>
        <div className="flex flex-wrap gap-2">
        <Button className="button-secondary" onClick={changed}>
          刷新
        </Button>
        <Link className="button" to="/subagents/new">
          新建子Agent
        </Link>
        </div>
      </div>
      <ErrorBox message={result.error} />
      {result.loading && <Loading />}
      {search.get('effective') && (
        <EffectiveCapabilities
          key={search.get('effective')}
          roleId={search.get('effective')!}
          version={version}
          initialOpen
        />
      )}
      {!result.loading && !result.error && result.data?.length === 0 && (
        <Empty title="尚无子Agent配置">
          新建并启用只读角色后，即可在任务中委派给该角色。
        </Empty>
      )}
      <div className="grid gap-5 xl:grid-cols-2">
        {result.data?.map((row) => (
          <RoleCard key={`${row.id}:${row.revision}`} row={row} changed={changed} />
        ))}
      </div>
      {historyId && (
        <Dialog title="配置历史" onClose={closeHistory} footer={<Button type="button" className="button-secondary" onClick={closeHistory}>关闭历史</Button>}>
          <p className="text-sm text-muted">
            旧地址保留供核对，不会恢复已删除的角色或改变当前配置。
          </p>
          <ErrorBox message={history.error} />
          {history.loading && <Loading />}
          {!history.loading && history.data?.length === 0 && <p>暂无历史版本。</p>}
          {history.data?.map((row) => (
            <article key={row.revision} className="mb-4 rounded-lg border border-line p-4">
              <h3 className="m-0 text-sm">
                {row.display_name} · 修订 {row.revision} ·{' '}
                {row.deleted ? '已删除' : row.enabled ? '已启用' : '已停用'}
              </h3>
              <p>{row.description}</p>
              <p className="text-xs text-muted">
                已保存工具：{(row.resolved_tools ?? row.tools).join('、') || '无'} · Skills：
                {row.skills.join('、') || '无'}
              </p>
              <pre className="whitespace-pre-wrap break-words">{row.system_prompt}</pre>
            </article>
          ))}
        </Dialog>
      )}
    </>
  );
}
