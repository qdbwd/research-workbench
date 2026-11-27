import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox, Loading, PageTitle } from '../../shared/ui/components';

/** 与 /api/readiness 对齐：只展示服务端算出的事实，不在前端推导状态。 */
export type Readiness = {
  status: 'ready' | 'degraded' | 'blocked';
  checked_at: number;
  cached: boolean;
  layers: {
    process: { ok: boolean; detail: string };
    foundation: {
      ok: boolean;
      database: { ok: boolean; detail?: string };
      migration: { ok: boolean; current?: string | null; head?: string | null; detail?: string };
      receipts: { ok: boolean; detail?: string };
      directories: { ok: boolean; items: Record<string, boolean | string> };
    };
    execution: {
      ok: boolean;
      state: 'ready' | 'busy' | 'offline';
      worker_lease: boolean;
      active_runs: number;
      heartbeat_seconds: number | null;
      redis: Record<string, { ok: boolean; detail?: string }>;
    };
    backlog: { ok: boolean; queued_runs: number; due_dispatches: number; retrying_dispatches: number };
  };
};

const statusText: Record<Readiness['status'], string> = {
  ready: '运行就绪',
  degraded: '基础就绪，执行未就绪',
  blocked: '存在阻塞，需要处理',
};
const executionText: Record<Readiness['layers']['execution']['state'], string> = {
  ready: 'Worker 空闲，可接单',
  busy: 'Worker 正在执行任务',
  offline: 'Worker 未启动或已停止',
};

export type BackupRecord = {
  id: string;
  created_at: string | null;
  format: string;
  status: 'verified' | 'created' | 'incomplete';
  detail: string;
  operations: { id: string; kind: string; state: string; phase: string; database: string }[];
  restore: { checked: boolean; passed: boolean; failed_checks: string[]; database: string };
};
export type BackupRecords = { available: boolean; detail?: string; items: BackupRecord[]; truncated: boolean };

const backupStatus: Record<BackupRecord['status'], string> = {
  verified: '已校验并有隔离恢复',
  created: '已创建，未通过校验',
  incomplete: '未完成',
};

type RowTone = 'ok' | 'idle' | 'error';

const toneStyle: Record<RowTone, { text: string; pill: string }> = {
  ok: { text: '正常', pill: 'bg-[#e7f1e8] text-[#347044]' },
  idle: { text: '空闲', pill: 'bg-[#edf1eb] text-[#5f7765]' },
  error: { text: '未就绪', pill: 'bg-[#f8e9e4] text-[#a44b40]' },
};

function Row({ label, ok, tone, detail }: { label: string; ok?: boolean; tone?: RowTone; detail?: string }) {
  const resolved: RowTone = tone ?? (ok ? 'ok' : 'error');
  const style = toneStyle[resolved];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line py-2.5 last:border-b-0">
      <span className="w-24 shrink-0 text-xs font-medium text-control">{label}</span>
      <span className={`inline-flex shrink-0 items-center rounded-[5px] px-2 py-0.5 text-[11px] font-medium ${style.pill}`}>
        {style.text}
      </span>
      {detail && <span className="min-w-0 flex-1 break-words text-xs text-muted">{detail}</span>}
    </div>
  );
}

export function SystemPage() {
  const [tick, setTick] = useState(0);
  const { data, loading, error } = useResource<Readiness>('/api/readiness', tick);
  const backups = useResource<BackupRecords>('/api/maintenance/backups', tick);
  useEffect(() => {
    // 页面隐藏时不探测；回到前台由下一次可见检查刷新。
    const timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') setTick((value) => value + 1);
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  const layers = data?.layers;
  const statusTone = data?.status === 'ready'
    ? 'bg-[#e7f1e8] text-[#347044]'
    : data?.status === 'blocked'
      ? 'bg-[#f8e9e4] text-[#a44b40]'
      : 'bg-[#f5eedc] text-[#96772d]';
  const verifiedBackups = backups.data?.available
    ? backups.data.items.filter((item) => item.status === 'verified').length
    : null;
  const heartbeatTone: RowTone = layers?.execution.heartbeat_seconds !== null
    ? 'ok'
    : layers?.execution.state === 'offline'
      ? 'error'
      : layers?.execution.active_runs === 0
        ? 'idle'
        : 'error';
  return (
    <>
      <PageTitle title="系统与维护">
        <Button className="button-secondary" onClick={() => setTick((value) => value + 1)}>重新检查</Button>
      </PageTitle>
      <ErrorBox message={error} />
      <p className="mt-0 mb-4 text-xs text-muted">
        任务进度与结果仍以运行记录为准；本页只读展示当前运行条件，不领取、批准或恢复任何任务。
      </p>
      {loading && !data && <Loading />}
      {data && layers && (
        <div className="grid gap-4">
          <section className="card rounded-xl border border-line bg-white p-5 max-md:p-4" aria-label="总体状态">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2.5">
                <span className={`inline-flex items-center rounded-[5px] px-2.5 py-0.5 text-xs font-medium ${statusTone}`}>
                  {statusText[data.status]}
                </span>
                <h2 className="m-0 text-base font-semibold">总体状态：{statusText[data.status]}</h2>
              </div>
              <span className="text-xs text-muted">
                检查时间 {new Date(data.checked_at * 1000).toLocaleString()}
                {data.cached ? '（短缓存）' : ''}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2.5 md:grid-cols-4 [&>div]:min-w-0 [&>div]:rounded-lg [&>div]:bg-canvas [&>div]:px-3 [&>div]:py-2.5">
              <div>
                <span className="block text-[11px] text-muted">数据库版本</span>
                <strong className="mt-0.5 block font-mono text-base text-ink">
                  {layers.foundation.migration.current ?? '未就绪'}
                </strong>
              </div>
              <div>
                <span className="block text-[11px] text-muted">执行器状态</span>
                <strong className="mt-0.5 block text-sm font-semibold text-ink">
                  {layers.execution.state === 'ready' ? '空闲可接单' : layers.execution.state === 'busy' ? `执行中 (${layers.execution.active_runs})` : '离线'}
                </strong>
              </div>
              <div>
                <span className="block text-[11px] text-muted">排队与积压</span>
                <strong className="mt-0.5 block text-base text-ink">
                  {layers.backlog.queued_runs} <small className="text-xs font-normal text-muted">个排队</small>
                </strong>
              </div>
              <div>
                <span className="block text-[11px] text-muted">已校验备份</span>
                <strong className="mt-0.5 block text-base text-ink">
                  {verifiedBackups === null ? '—' : `${verifiedBackups} 份`}
                </strong>
              </div>
            </div>
          </section>

          <div className="grid gap-4 md:grid-cols-2">
            <section className="card min-w-0 rounded-xl border border-line bg-white p-5 max-md:p-4" aria-label="基础依赖">
              <h2 className="m-0 mb-2 text-base font-semibold">基础依赖</h2>
              <Row label="API 进程" ok={layers.process.ok} detail="能响应请求即表示进程存活" />
              <Row label="业务库" ok={layers.foundation.database.ok} detail={layers.foundation.database.detail || 'PostgreSQL 业务库可连接'} />
              <Row label="数据库版本" ok={layers.foundation.migration.ok}
                detail={layers.foundation.migration.ok
                  ? `当前 ${layers.foundation.migration.current}`
                  : layers.foundation.migration.detail
                    ?? `当前 ${layers.foundation.migration.current ?? '未知'} / 期望 ${layers.foundation.migration.head ?? '未知'}`} />
              <Row label="回执库" ok={layers.foundation.receipts.ok} detail={layers.foundation.receipts.detail || '独立模型账本库可连接'} />
              <Row label="工作目录" ok={layers.foundation.directories.ok}
                detail={Object.entries(layers.foundation.directories.items ?? {})
                  .filter(([key]) => key.endsWith('_path'))
                  .map(([key, value]) => `${key.replace('_path', '')}: ${value || '未配置'}`)
                  .join('；')} />
            </section>

            <div className="grid min-w-0 gap-4">
              <section className="card min-w-0 rounded-xl border border-line bg-white p-5 max-md:p-4" aria-label="执行就绪">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <h2 className="m-0 text-base font-semibold">执行就绪</h2>
                  <span className="text-xs text-control">{executionText[layers.execution.state]}</span>
                </div>
                <Row label="Worker 租约" ok={layers.execution.worker_lease} detail="同一库只允许一个 Worker 实例" />
                <Row label="正在执行" ok detail={`${layers.execution.active_runs} 个任务`} />
                <Row label="最近心跳" tone={heartbeatTone}
                  detail={layers.execution.heartbeat_seconds === null
                    ? (layers.execution.state === 'offline' ? 'Worker 未启动' : '当前无在途任务（空闲正常）')
                    : `${layers.execution.heartbeat_seconds} 秒前`} />
                {Object.entries(layers.execution.redis ?? {}).map(([name, item]) => (
                  <Row key={name} label={name === 'broker' ? '任务队列' : '实时通道'} ok={item.ok}
                    detail={item.ok ? (name === 'broker' ? 'Celery Broker (Redis) 已连接' : 'SSE 事件桥 (Redis) 已连接') : item.detail} />
                ))}
              </section>

              <section className="card min-w-0 rounded-xl border border-line bg-white p-5 max-md:p-4" aria-label="排队与积压">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <h2 className="m-0 text-base font-semibold">排队与积压</h2>
                  <Link className="text-xs" to="/runs">查看运行记录 →</Link>
                </div>
                <Row label="排队任务" ok detail={`${layers.backlog.queued_runs} 个`} />
                <Row label="待投递" ok={layers.backlog.due_dispatches === 0}
                  detail={`${layers.backlog.due_dispatches} 个到期未发出`} />
                <Row label="投递重试" ok={layers.backlog.retrying_dispatches === 0}
                  detail={`${layers.backlog.retrying_dispatches} 个正在重试`} />
              </section>
            </div>
          </div>

          <section className="card min-w-0 rounded-xl border border-line bg-white p-5 max-md:p-4" aria-label="备份与恢复">
            <h2 className="m-0 mb-2 text-base font-semibold">备份与恢复</h2>
            <ErrorBox message={backups.error} />
            {backups.loading && !backups.data && <Loading />}
            {backups.data && !backups.data.available && (
              <p className="m-0 text-sm text-muted">{backups.data.detail}。</p>
            )}
            {backups.data?.available && (
              <>
                {backups.data.items.length === 0 && <p className="m-0 text-sm text-muted">尚无备份记录。</p>}
                {backups.data.items.length > 0 && (
                  <div className="scrollbar-subtle max-h-[260px] divide-y divide-line overflow-y-auto overscroll-contain pr-1">
                    {backups.data.items.map((record) => (
                      <div key={record.id} className="py-2.5 first:pt-1 last:pb-1">
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                          <span className="font-mono text-xs">{record.id}</span>
                          <span className="text-xs text-muted">
                            {record.created_at ? new Date(record.created_at).toLocaleString() : '时间未知'}
                          </span>
                          {record.format && <span className="text-xs text-muted">格式 {record.format}</span>}
                          <span className={`rounded-[5px] px-2 py-0.5 text-[11px] font-medium ${record.status === 'verified' ? 'bg-[#e7f1e8] text-[#347044]' : 'bg-[#f8e9e4] text-[#a44b40]'}`}>
                            {backupStatus[record.status]}
                          </span>
                        </div>
                        <p className="mt-1 mb-0 text-xs text-muted">
                          {record.detail}
                          {record.restore.failed_checks.length > 0 && `；未通过：${record.restore.failed_checks.join('、')}`}
                        </p>
                        {record.operations.map((operation) => (
                          <p key={operation.id} className="m-0 text-xs text-muted">
                            操作 {operation.kind} · {operation.state}
                            {operation.database ? ` · ${operation.database}` : ''}
                          </p>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
                {backups.data.truncated && <p className="mt-2 mb-0 text-xs text-muted">仅显示最近的记录。</p>}
              </>
            )}
            <p className="mt-3 mb-0 border-t border-line pt-2.5 text-xs text-muted">
              创建与校验由本机命令执行（<code>./scripts/backup.ps1</code>、独立校验 <code>./scripts/verify_backup.ps1</code>）；
              网页只读展示结果，不控制 Docker，也不覆盖正式库。
            </p>
          </section>
        </div>
      )}
    </>
  );
}
