import type { ReactNode } from 'react';

/** 同一区域的设置入口采用同等的按钮层级，留出窄屏换行空间。 */
export function SettingsRow({ title, description, icon, children }: {
  title: string;
  description: string;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        {icon && <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-canvas text-brand">{icon}</span>}
        <div className="min-w-0">
          <h3 className="m-0 text-sm font-semibold">{title}</h3>
          <p className="mt-1 mb-0 text-xs text-muted">{description}</p>
        </div>
      </div>
      <div className="ml-auto flex max-w-full flex-wrap justify-end gap-2 [&>.button]:min-w-28">
        {children}
      </div>
    </div>
  );
}
