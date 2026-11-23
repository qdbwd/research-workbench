import { Link, useParams } from 'react-router-dom';
import { useResource } from '../../shared/api/api';
import { ErrorBox } from '../../shared/ui/components';
import { BackLink, WorkbenchHeading, WorkbenchPanel } from '../../shared/ui/Workbench';
import { researchApi, researchPath } from './api';

type Excerpt = {
  kind: 'history' | 'file' | 'run_result' | 'experiment';
  title: string;
  text: string;
  start: number;
  end: number;
  offset_unit: 'characters' | 'lines';
  role?: 'human' | 'ai' | 'tool';
  file_sha256?: string;
  conversation_url?: string;
  image?: string;
  input_sha256?: string;
  script_sha256?: string;
};

export function ResearchExcerptPage() {
  const { taskId, evidenceId } = useParams();
  const evidence = useResource<Excerpt>(taskId && evidenceId
    ? researchApi(taskId) + '/evidence/' + encodeURIComponent(evidenceId) : null);
  if (!taskId) return null;
  const excerpt = evidence.data;
  const kind = excerpt?.kind === 'history'
    ? (excerpt.role === 'human' ? '用户原话' : excerpt.role === 'ai' ? '助手原话' : '会话活动')
    : excerpt?.kind === 'file' ? '固定文件片段' : excerpt?.kind === 'experiment' ? '独立实验结果' : '固定运行结果';
  return <div className="mx-auto max-w-4xl">
    <BackLink to={researchPath(taskId) + '/map'}>返回研究地图</BackLink>
    <WorkbenchHeading eyebrow="研究证据" title={excerpt?.title ?? '固定读取摘录'} />
    <ErrorBox message={evidence.error} />
    {evidence.loading && <p role="status">正在读取证据…</p>}
    {excerpt && <WorkbenchPanel title={kind} description={excerpt.offset_unit === 'lines'
      ? `第 ${excerpt.start + 1}–${excerpt.end} 行` : `原文第 ${excerpt.start}–${excerpt.end} 字符`}>
      <pre className="m-0 font-sans text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">{excerpt.text}</pre>
      {excerpt.conversation_url && <Link className="mt-3 inline-block text-sm" to={excerpt.conversation_url}>在会话中定位原话</Link>}
      {excerpt.kind === 'experiment' && <div className="mt-4 grid gap-2 border-t border-line pt-3 text-sm">
        <Link to={researchPath(taskId) + '/experiments'}>查看实验尝试与复现</Link>
        <p className="m-0 break-all text-control">{excerpt.image}</p>
        <p className="m-0 break-all text-control">输入 SHA256 · {excerpt.input_sha256}</p>
        <p className="m-0 break-all text-control">脚本 SHA256 · {excerpt.script_sha256}</p>
      </div>}
      {excerpt.file_sha256 && <p className="mt-4 mb-0 border-t border-line pt-3 text-xs text-control [overflow-wrap:anywhere]">
        文件 SHA256 · {excerpt.file_sha256}
      </p>}
    </WorkbenchPanel>}
  </div>;
}
