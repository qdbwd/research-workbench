import { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox } from '../../shared/ui/components';

type Snapshot = {
  frozen: boolean;
  snapshot_hash: string | null;
  entry_count: number;
  frozen_at: string | null;
};
export function ConversationMemorySnapshot({
  conversationId,
  enabled,
  trial,
}: {
  conversationId?: string;
  enabled: boolean;
  trial?: boolean;
}) {
  const path =
    conversationId && enabled && !trial
      ? `/api/conversations/${encodeURIComponent(conversationId)}/memory-snapshot`
      : null;
  const [tick, setTick] = useState(0);
  const result = useResource<Snapshot>(path, tick);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  async function refresh() {
    if (!path) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const snapshot = await send<{ entry_count: number }>(path + '/refresh');
      setNotice(`快照已更新，共载入 ${snapshot.entry_count} 条生效记忆`);
      setTick((value) => value + 1);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  const snapshot = result.data;
  return (
    <section aria-label="会话记忆快照" className="my-1 min-w-0 border-t border-line pt-4">
      <h3 className="m-0 text-sm font-semibold">会话记忆快照</h3>
      <p className="my-2 text-sm text-control">
        冻结记忆用于保持会话前缀稳定。后台复盘产生的新记忆默认在新会话生效。
      </p>
      {snapshot?.frozen && (
        <p className="my-2 break-words text-xs text-muted">
          已冻结 {snapshot.entry_count} 条 · <code>{snapshot.snapshot_hash?.slice(0, 12)}</code>
          {snapshot.frozen_at && ` · ${new Date(snapshot.frozen_at).toLocaleString('zh-CN')}`}
        </p>
      )}
      {snapshot && !snapshot.frozen && (
        <p className="my-2 text-xs text-muted">本会话尚未冻结记忆。</p>
      )}
      {!path && (
        <p className="my-2 text-xs text-muted">
          {trial ? '试用会话使用隔离记忆。' : '发送首条消息后可刷新。'}
        </p>
      )}
      <Button
        type="button"
        className="button-secondary"
        disabled={!path || busy}
        onClick={() => void refresh()}
      >
        <RefreshCw size={16} aria-hidden="true" /> 刷新本会话记忆快照
      </Button>
      {notice && (
        <p role="status" className="my-2 text-sm text-brand">
          {notice}
        </p>
      )}
      <ErrorBox message={error || result.error} />
    </section>
  );
}
