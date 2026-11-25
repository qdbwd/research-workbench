import { useState } from 'react';
import { Globe2 } from 'lucide-react';
import { api, ApiError, messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox } from '../../shared/ui/components';
import { Dialog } from '../../shared/ui/Dialog';
import { SettingsRow } from '../../shared/ui/SettingsRow';
import { type BrowserMode, type BrowserSettings, browserSettingsPath, effectiveBrowserMode } from './browserSettings';

type Probe = { ok: boolean; code: string; message: string; attach_busy: boolean; browser?: string; tabs_count?: number };

export function BrowserSettingsRow() {
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState(0);
  const settings = useResource<BrowserSettings>(browserSettingsPath, version);
  const data = settings.data?.deployment ? settings.data : null;
  const loadError = settings.error || (settings.data && !data ? '无法读取浏览器部署状态。' : '');
  const ready = data?.deployment.browser_enabled && data.deployment.entrypoint_ready;
  const attach = data?.local_attach_enabled && data.deployment.local_attach_available;
  const status = !ready ? '不可用' : effectiveBrowserMode(data) === 'attach'
    ? '默认本机可见 · CDP' : attach ? '双模式就绪（默认容器）' : '容器隔离';

  return (
    <SettingsRow
      title="动态浏览器取证（Playwright MCP）"
      description={loadError ? '读取浏览器配置失败' : settings.loading && !settings.data
        ? '正在读取部署状态…' : ready
          ? '适用于普通聊天与研发模式，任务中可选择已启用的浏览器'
          : '当前部署未启用或组件尚未就绪'}
      icon={<Globe2 size={17} aria-hidden="true" />}
    >
      {!settings.loading && !loadError && (
        <span className="self-center rounded-full bg-sidebar px-2 py-1 text-xs text-brand">{status}</span>
      )}
      <Button type="button" className="button-secondary" onClick={() => setOpen(true)}>
        配置浏览器
      </Button>
      {open && (data ? (
        <BrowserSettingsDialog initial={data} onClose={() => setOpen(false)} onSaved={() => {
          setOpen(false);
          setVersion((value) => value + 1);
        }} />
      ) : (
        <Dialog title="动态浏览器取证配置" onClose={() => setOpen(false)}>
          {loadError ? <><ErrorBox message={loadError} />
            <Button type="button" className="button-secondary" onClick={() => setVersion((number) => number + 1)}>重新加载</Button>
          </> : <p role="status">正在加载浏览器设置…</p>}
        </Dialog>
      ))}
    </SettingsRow>
  );
}

export function BrowserSettingsDialog({ initial, onClose, onSaved }: {
  initial: BrowserSettings;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [base, setBase] = useState(initial);
  const [allowAttach, setAllowAttach] = useState(initial.local_attach_enabled);
  const [mode, setMode] = useState<BrowserMode>(effectiveBrowserMode(initial));
  const [working, setWorking] = useState<'probe' | 'save' | 'refresh'>();
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState('');
  const [probe, setProbe] = useState<Probe>();
  const deployment = base.deployment;
  const launchReady = deployment.browser_enabled && deployment.entrypoint_ready && deployment.chromium_ready;
  const attachReady = deployment.local_attach_available && deployment.entrypoint_ready;
  const changed = allowAttach !== base.local_attach_enabled || mode !== base.default_mode;

  async function save() {
    setWorking('save');
    setError('');
    try {
      await send<BrowserSettings>(browserSettingsPath, {
        expected_revision: base.revision, local_attach_enabled: allowAttach, default_mode: mode,
      }, 'PATCH');
      onSaved();
    } catch (failure) {
      setConflict(failure instanceof ApiError && failure.status === 409);
      setError(messageOf(failure));
    } finally {
      setWorking(undefined);
    }
  }

  async function refreshRevision() {
    setWorking('refresh');
    setError('');
    try {
      const current = await api<BrowserSettings>(browserSettingsPath);
      setBase(current);
      setConflict(false);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setWorking(undefined);
    }
  }

  async function probeConnection() {
    setWorking('probe');
    setProbe(undefined);
    setError('');
    try {
      setProbe(await send<Probe>('/api/browser/probe'));
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setWorking(undefined);
    }
  }

  const modes = [
    { id: 'launch', title: '容器隔离浏览器', enabled: launchReady,
      detail: '在容器中启动独立的无头 Chromium，任务结束后回收页面和临时资料。' },
    { id: 'attach', title: '本机可见浏览器（CDP）', enabled: allowAttach && attachReady,
      detail: '连接专用调试窗口，同一时间由一项根任务使用，退出后保留本机窗口。' },
  ] as const;
  return (
    <Dialog title="动态浏览器取证配置" onClose={onClose} busy={working === 'save'}
      footer={<>
        <Button type="button" className="button-secondary" onClick={onClose} disabled={working === 'save'}>取消</Button>
        <Button type="button" onClick={() => void save()} disabled={!changed || !!working || conflict || !deployment.browser_enabled
          || (allowAttach && !deployment.local_attach_available)}>
          {working === 'save' ? '正在保存…' : '保存浏览器设置'}
        </Button>
      </>}
    >
      <div className="min-w-0 space-y-4 text-sm">
        <p className="m-0 rounded-lg border border-line bg-sidebar px-3 py-2 text-control" role="status">
          <strong>{launchReady ? '容器隔离模式已就绪' : '容器隔离模式未就绪'}</strong>
          {' · '}{deployment.local_attach_available ? '部署已开放本机 CDP' : '部署未开放本机 CDP'}
          {deployment.attach_busy ? ' · 本机窗口正在使用' : ''}
        </p>
        <fieldset className="m-0 min-w-0 border-0 p-0">
          <legend className="mb-2 font-semibold">默认浏览器模式</legend>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {modes.map((item) => (
              <label key={item.id}
                className={`m-0 flex min-w-0 cursor-pointer flex-row items-start gap-2 rounded-xl border p-3.5 font-normal
                  ${mode === item.id ? 'border-brand bg-canvas' : 'border-line bg-white'}
                  ${item.enabled ? '' : 'cursor-not-allowed opacity-60'}`}>
                <input type="radio" name="browser-default-mode" className="mt-1 size-4 shrink-0 accent-brand"
                  checked={mode === item.id} disabled={!item.enabled || !!working}
                  onChange={() => setMode(item.id)} />
                <span className="min-w-0">
                  <span className="block font-semibold">{item.title}</span>
                  <span className="mt-1 block text-xs leading-relaxed text-control">{item.detail}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="check m-0 flex-row items-start gap-2 rounded-lg border border-line bg-canvas p-3 font-normal">
          <input type="checkbox" className="mt-0.5 shrink-0 accent-brand" checked={allowAttach}
            disabled={(!deployment.local_attach_available && !allowAttach) || !!working}
            onChange={(event) => {
              setAllowAttach(event.target.checked);
              if (!event.target.checked) setMode('launch');
            }} />
          <span>
            <strong className="block font-medium">允许启用本机可见浏览器（CDP 挂接，默认关闭）</strong>
            <span className="mt-1 block text-xs text-muted">启用后，可以在「本次任务设置」按次选择。</span>
          </span>
        </label>
        <div className="min-w-0 rounded-lg border border-line p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <strong className="font-medium">本机 CDP 端点</strong>
            <Button type="button" className="button-secondary text-xs"
              disabled={!deployment.local_attach_available || !!working} onClick={() => void probeConnection()}>
              {working === 'probe' ? '正在检测…' : '检测本机浏览器连接'}
            </Button>
          </div>
          <code className="mt-1 block text-xs [overflow-wrap:anywhere] text-control">
            {deployment.cdp_endpoint_display ?? '部署未配置'}
          </code>
          <p className="mt-2 mb-0 text-xs leading-relaxed text-muted">
            在 Windows PowerShell 运行 <code className="[overflow-wrap:anywhere]">pwsh scripts/start-local-browser.ps1</code> 启动专用调试窗口。
          </p>
          {probe && <p role="status" className={`mt-2 mb-0 text-xs ${probe.ok ? 'text-brand' : 'text-danger'}`}>
            {probe.message}{probe.ok ? ` ${probe.browser} · ${probe.tabs_count} 个页面` : ''}
            {probe.attach_busy ? ' · 本机窗口正在使用' : ''}
          </p>}
        </div>
        <details className="rounded-lg border border-line bg-canvas p-3">
          <summary className="cursor-pointer font-medium">查看 7 个取证工具与使用边界</summary>
          <p className="mt-2 mb-0 text-xs leading-relaxed text-control">
            导航、观察、点击取证控件、等待、返回、采集正文与 PNG、关闭会话。仅主智能体可用，点击须来自最新观察。
          </p>
        </details>
        <ErrorBox message={error} />
        {conflict && <Button type="button" className="button-secondary"
          disabled={!!working} onClick={() => void refreshRevision()}>刷新修订并保留草稿</Button>}
      </div>
    </Dialog>
  );
}
