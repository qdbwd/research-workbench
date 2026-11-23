import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { ArrowLeft, CircleHelp } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from './Button';
import { Dialog } from './Dialog';

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="button button-secondary mb-5">
      <ArrowLeft size={16} aria-hidden="true" /> {children}
    </Link>
  );
}

export function HelpHint({ label, children }: { label: string; children: string }) {
  const id = useId();
  return (
    <span className="group relative inline-flex">
      <button type="button" className="rounded-lg p-1.5 text-control hover:bg-canvas"
        aria-label={label} aria-describedby={id}>
        <CircleHelp size={17} />
      </button>
      <span id={id} role="tooltip" className="pointer-events-none absolute top-full left-0 z-20 hidden w-[min(260px,calc(100vw_-_156px))] rounded-lg border border-line bg-white p-3 text-left text-xs font-normal text-ink shadow-md group-hover:block group-focus-within:block">
        {children}
      </span>
    </span>
  );
}

export function DetailDialog({
  title, children, className = '', onOpenChange,
}: {
  title: string;
  children: ReactNode;
  className?: string;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  function change(value: boolean) {
    setOpen(value);
    onOpenChange?.(value);
  }
  return (
    <div className={className}>
      <Button type="button" className="button-secondary max-w-full whitespace-normal text-left text-xs"
        onClick={() => change(true)}>{title}</Button>
      {open && <Dialog title={title} onClose={() => change(false)}>{children}</Dialog>}
    </div>
  );
}

export function WorkbenchHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <p className="mb-2 text-sm font-semibold text-brand">{eyebrow}</p>
        <h1 className="m-0 text-[clamp(1.5rem,4vw,2.15rem)] font-semibold leading-tight [overflow-wrap:anywhere]">
          {title}
        </h1>
        {description && (
          <p className="mt-2 mb-0 max-w-[65ch] text-sm leading-relaxed text-control">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function WorkbenchPanel({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 rounded-2xl border border-line bg-white px-5 py-4 md:px-6 md:py-5">
      <h2 className="m-0 text-base font-semibold">{title}</h2>
      {description && <p className="mt-1 mb-0 text-sm text-control">{description}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}
