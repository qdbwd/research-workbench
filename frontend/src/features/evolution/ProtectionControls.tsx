import { LockKeyhole, Pin } from 'lucide-react';
import { Button } from '../../shared/ui/Button';

export type ProtectionLevel = 'normal' | 'pinned' | 'locked';

export function ProtectionBadge({ level }: { level?: ProtectionLevel }) {
  if (!level || level === 'normal') return null;
  const locked = level === 'locked';
  const Icon = locked ? LockKeyhole : Pin;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-xs ${
        locked ? 'bg-[#fbeae8] text-danger' : 'bg-[#f5f0e0] text-[#7a5d1c]'
      }`}
    >
      <Icon size={13} aria-hidden="true" /> {locked ? '已锁定 Lock' : '已固定 Pin'}
    </span>
  );
}

export function ProtectionControls({
  level = 'normal',
  noun,
  disabled,
  onChange,
}: {
  level?: ProtectionLevel;
  noun: string;
  disabled?: boolean;
  onChange: (level: ProtectionLevel) => void;
}) {
  const locked = level === 'locked';
  return (
    <div className="flex flex-wrap items-center gap-2">
      {!locked && (
        <Button
          type="button"
          className="button-quiet"
          disabled={disabled}
          onClick={() => onChange(level === 'pinned' ? 'normal' : 'pinned')}
        >
          {level === 'pinned' ? '取消固定' : `固定${noun}`}
        </Button>
      )}
      <Button
        type="button"
        className="button-secondary"
        disabled={disabled}
        onClick={() => onChange(locked ? 'normal' : 'locked')}
      >
        {locked ? `解锁${noun}` : `锁定${noun}`}
      </Button>
    </div>
  );
}
