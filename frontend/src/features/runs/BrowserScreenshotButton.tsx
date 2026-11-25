import { useState } from 'react';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';

export function BrowserScreenshotButton({ runId, artifactId }: { runId: string; artifactId: string }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <>
      <Button type="button" className="button-secondary small text-xs"
        onClick={() => { setFailed(false); setOpen(true); }}>
        查看取证截图 (PNG)
      </Button>
      {open && (
        <Dialog title="取证截图预览" onClose={() => setOpen(false)}
          footer={<Button type="button" className="button-secondary" onClick={() => setOpen(false)}>关闭预览</Button>}>
          {failed ? <p role="alert" className="m-0 text-danger">截图已清理、校验失败或暂不可读取。</p> : (
            <img className="mx-auto block h-auto max-h-[65dvh] max-w-full rounded-lg border border-line object-contain"
              alt="浏览器取证截图" onError={() => setFailed(true)}
              src={`/api/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/image`} />
          )}
        </Dialog>
      )}
    </>
  );
}
