import { useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { messageOf, send } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { researchApi, type ResearchTask } from './api';
import type { AdoptionPreview } from './mapApi';

export function ResearchAdoption({ task, preview, onClose, onSaved }: {
  task: ResearchTask;
  preview: AdoptionPreview;
  onClose: () => void;
  onSaved: () => void;
}) {
  const formId = useId();
  const [revision] = useState(task.control_version);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{ reason: string; key: string } | null>(null);

  async function adopt(reason: string) {
    if (busy) return;
    if (pending.current?.reason !== reason) pending.current = { reason, key: crypto.randomUUID() };
    setBusy(true);
    setError('');
    try {
      await send(researchApi(task.id) + '/map/adopt', {
        operation_id: pending.current.key, reason, expected_control_version: revision,
        expected_revision_id: preview.target_revision_id, source_revision_id: preview.source_revision_id,
        node_id: preview.node_id, preview_hash: preview.preview_hash,
      });
      onSaved();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  return <Dialog title="采纳历史结论" busy={busy} onClose={onClose} footer={<>
    <Button type="button" className="button-secondary" disabled={busy} onClick={onClose}>取消</Button>
    <Button type="submit" form={formId} disabled={busy}>确认采纳</Button>
  </>}>
    <div className="grid gap-4">
      <p className="m-0 text-sm text-control">这次只更新当前主线的结论、理由和证据。保存新版本后，节点进入待复核。</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {([['当前结论', preview.before], ['将采纳的结论', preview.after]] as const).map(([label, node]) => (
          <section key={label} className="rounded-lg border border-line p-3">
            <h3 className="m-0 text-sm font-semibold">{label}</h3>
            <p className="my-1 whitespace-pre-wrap [overflow-wrap:anywhere]">{node.conclusion || '暂无结论'}</p>
            {node.reason && <p className="m-0 text-sm text-control">{node.reason}</p>}
          </section>
        ))}
      </div>
      {preview.evidence.length > 0 && <ul className="m-0 grid gap-1 text-sm">
        {preview.evidence.map(row => <li key={row.id}>
          {row.url ? <Link to={row.url}>{row.title}</Link> : '原依据无法读取'}
        </li>)}
      </ul>}
      <form id={formId} onSubmit={event => {
        event.preventDefault();
        void adopt(String(new FormData(event.currentTarget).get('reason') ?? '').trim());
      }}>
        <label className="grid gap-1 text-sm font-semibold">采纳理由
          <textarea name="reason" rows={3} required maxLength={1000} />
        </label>
      </form>
      <ErrorBox message={error} />
    </div>
  </Dialog>;
}
