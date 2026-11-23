import { useRef, useState } from 'react';
import { ApiError, api, messageOf, send } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { ResearchAutonomy } from './ResearchAutonomy';
import { ResearchDeliveries } from './ResearchDeliveries';
import { researchApi } from './api';
import type { ResearchTask } from './api';

type Action = 'pause' | 'resume' | 'cancel';
type ControlRequest = {
  action: Action;
  expected_control_version: number;
  operation_id: string;
};

export function ResearchControls({ task, onChanged }: {
  task: ResearchTask;
  onChanged: (task: ResearchTask) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<ControlRequest | null>(null);
  // 控制请求与简报修订使用不同版本。响应丢失后保留原操作编号。
  async function control(action: Action) {
    if (busy) return;
    if (pending.current?.action !== action || pending.current.expected_control_version !== task.control_version) {
      pending.current = { action, expected_control_version: task.control_version, operation_id: crypto.randomUUID() };
    }
    setBusy(true);
    setError('');
    try {
      const next = await send<ResearchTask>(`${researchApi(task.id)}/control`, pending.current);
      pending.current = null;
      setConfirming(false);
      onChanged(next);
    } catch (failure) {
      setError(messageOf(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        pending.current = null;
        setConfirming(false);
        try {
          onChanged(await api<ResearchTask>(researchApi(task.id)));
        } catch (readError) {
          setError(messageOf(readError));
        }
      }
    } finally {
      setBusy(false);
    }
  }
  const open = task.status === 'open';
  const paused = task.status === 'paused';
  const canCancel = open || paused || task.status === 'awaiting_acceptance';
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
          <ResearchAutonomy task={task} onChanged={onChanged} />
          <ResearchDeliveries task={task} onChanged={onChanged} />
          {(open || paused) && (
            <Button type="button" className="button-secondary text-xs" disabled={busy}
              onClick={() => void control(open ? 'pause' : 'resume')}>{open ? '暂停' : '继续'}</Button>
          )}
          {canCancel && <Button type="button" className="button-quiet text-xs text-danger" disabled={busy}
            onClick={() => { setError(''); setConfirming(true); }}>取消任务</Button>}
      </div>
      {!confirming && error && <div className="w-full"><ErrorBox message={error} /></div>}
      {confirming && <Dialog title="取消研发任务？" busy={busy} onClose={() => setConfirming(false)}
        footer={<>
          <Button type="button" className="button-secondary" disabled={busy} onClick={() => setConfirming(false)}>返回</Button>
          <Button type="button" className="text-white bg-danger border-danger" disabled={busy}
            onClick={() => void control('cancel')}>{busy ? '正在取消…' : '确认取消任务'}</Button>
        </>}>
        <p>取消将结束本次研发。已保存的聊天、简报和材料可以继续查看。</p>
        <ErrorBox message={error} />
      </Dialog>}
    </>
  );
}
