import { useRef, useState } from 'react';
import type { DragEvent } from 'react';
import { Link } from 'react-router-dom';
import { Folder, FolderOpen, FolderPlus, MoreHorizontal, Pin } from 'lucide-react';
import { messageOf, send, usePagedResource, useResource } from '../../shared/api/api';
import { useWorkspace } from '../../shared/state/workspace';
import type { Conversation, ConversationProject } from '../../shared/types/types';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { ConversationLink } from './ConversationLink';

const projectPath = '/api/conversation-projects';

type DirectoryActions = {
  projects: ConversationProject[];
  move: (chat: Conversation, destination: string | null) => Promise<void>;
  drag: (chat: Conversation | null) => void;
  onOpen?: () => void;
};

function ProjectChats({ project, actions, version }: {
  project?: ConversationProject;
  actions: DirectoryActions;
  version: number;
}) {
  const { refresh } = useWorkspace();
  const destination = project?.id ?? 'unfiled';
  const rows = usePagedResource<Conversation>('/api/conversations/page?limit=12&project=' + encodeURIComponent(destination), version, true);
  return (
    <section aria-label={project ? project.name + '内的会话' : '未分组'} className={project ? 'ml-2 border-l border-line pl-1' : 'min-w-0'}>
      {!project && <p className="mx-2.5 my-1 text-xs font-medium text-control">未分组</p>}
      {rows.data.map((chat) => <ConversationLink key={chat.id} chat={chat} projects={actions.projects}
        onMove={actions.move} onDrag={actions.drag} onOpen={actions.onOpen} contained={Boolean(actions.onOpen)} />)}
      {rows.loading && !rows.data.length && <p className="mx-2.5 my-1 text-xs text-control" role="status">正在加载会话…</p>}
      {!rows.loading && !rows.error && !rows.data.length && <p className="mx-2.5 my-1 text-xs text-control">还没有对话</p>}
      <ErrorBox message={rows.error} />
      {rows.error && <Button className="button-quiet text-xs" onClick={refresh}>重试</Button>}
      {rows.next && <Button className="button-quiet text-xs" disabled={rows.loading} onClick={() => void rows.loadMore()}>加载更多</Button>}
    </section>
  );
}

type ProjectEditor = { project?: ConversationProject; name: string; pinned: boolean; token: string; removing: boolean };

export function ConversationDirectory({ onOpen }: { onOpen?: () => void }) {
  const { version, refresh } = useWorkspace();
  const directory = useResource<{ items: ConversationProject[] }>(projectPath, version);
  const projects = directory.data?.items ?? [];
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  const [editor, setEditor] = useState<ProjectEditor>();
  const [notice, setNotice] = useState('');
  const [dropError, setDropError] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const dragging = useRef<Conversation | null>(null);

  function manage(project?: ConversationProject) {
    setFailure('');
    setEditor({ project, name: project?.name ?? '', pinned: project?.pinned ?? false,
      token: crypto.randomUUID(), removing: false });
  }

  async function move(chat: Conversation, destination: string | null) {
    await send('/api/conversations/' + encodeURIComponent(chat.id) + '/project', {
      project_id: destination, expected_revision: chat.project_revision ?? 0,
    }, 'PATCH');
    setNotice('已移动会话');
    refresh();
  }

  function target(destination: string | null) {
    return {
      onDragOver: (event: DragEvent) => {
        if (dragging.current && dragging.current.project_id !== destination) {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
        }
      },
      onDrop: (event: DragEvent) => {
        const chat = dragging.current;
        if (!chat || event.dataTransfer.getData('application/x-knowledgeops-conversation') !== chat.id) return;
        event.preventDefault();
        dragging.current = null;
        setDropError('');
        void move(chat, destination).catch((error) => { setDropError(messageOf(error)); refresh(); });
      },
    };
  }

  async function save() {
    if (!editor || busy || (!editor.removing && !editor.name.trim())) return;
    setBusy(true);
    setFailure('');
    const project = editor.project;
    try {
      if (!project) {
        await send(projectPath, { name: editor.name.trim(), request_key: editor.token });
      } else if (editor.removing) {
        await send(projectPath + '/' + project.id, undefined, 'DELETE');
      } else {
        await send(projectPath + '/' + project.id, { name: editor.name.trim(), pinned: editor.pinned }, 'PATCH');
      }
      setEditor(undefined);
      refresh();
    } catch (error) {
      setFailure(messageOf(error));
    } finally {
      setBusy(false);
    }
  }

  const actions = { projects, move, drag: (chat: Conversation | null) => { dragging.current = chat; }, onOpen };
  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between px-2.5 text-xs text-control">
        <span>项目</span>
        <button type="button" title="新建项目" aria-label="新建项目"
          className="rounded p-1 hover:bg-[#e2ebe2]" onClick={() => manage()}>
          <FolderPlus size={16} aria-hidden="true" />
        </button>
      </div>
      <ErrorBox message={directory.error || dropError} />
      {directory.error && <Button className="button-quiet text-xs" onClick={refresh}>重新加载项目</Button>}
      {projects.map(project => {
        const expanded = opened.has(project.id);
        const Icon = expanded ? FolderOpen : Folder;
        return (
          <div key={project.id} className="min-w-0">
            <div className="group flex min-w-0 items-center rounded-md hover:bg-[#e2ebe2]" {...target(project.id)}>
              <button type="button" className="flex min-w-0 flex-1 items-center gap-2 rounded px-2.5 py-2 text-xs"
                aria-expanded={expanded} title={project.name}
                onClick={() => setOpened(previous => {
                  const next = new Set(previous);
                  if (next.has(project.id)) next.delete(project.id);
                  else next.add(project.id);
                  return next;
                })}>
                <Icon size={16} className="shrink-0 text-brand" aria-hidden="true" />
                <span className="min-w-0 truncate">{project.name}</span>
                {project.pinned && <Pin size={13} className="shrink-0 text-control" aria-hidden="true" />}
              </button>
              <button type="button" className="shrink-0 rounded p-1 text-control hover:bg-[#dceadf]" aria-label={'项目“' + project.name + '”的操作'}
                onClick={() => manage(project)}><MoreHorizontal size={16} aria-hidden="true" /></button>
            </div>
            {expanded && <ProjectChats project={project} actions={actions} version={version} />}
          </div>
        );
      })}
      <div className="pt-1" {...target(null)}><ProjectChats actions={actions} version={version} /></div>
      {notice && <span className="sr-only" role="status">{notice}</span>}
      {editor && (
        <Dialog title={editor.removing ? '移除项目' : editor.project ? '管理项目' : '新建项目'}
          onClose={() => setEditor(undefined)} busy={busy}>
          <form className="grid gap-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
            {editor.removing ? <p className="m-0">「{editor.name}」中的会话会移回「未分组」。</p> : <>
              {editor.project && <Link className="button button-secondary justify-self-start"
                to={'/?draft=new&project=' + encodeURIComponent(editor.project.id)}
                onClick={() => { setEditor(undefined); onOpen?.(); }}>在项目中新建会话</Link>}
              <label>项目名称<input value={editor.name} maxLength={100} autoFocus
                onChange={(event) => setEditor({ ...editor, name: event.target.value })} /></label>
              {editor.project && <label className="flex items-center gap-2"><input type="checkbox" checked={editor.pinned}
                onChange={(event) => setEditor({ ...editor, pinned: event.target.checked })} />置顶项目</label>}
            </>}
            <ErrorBox message={failure} />
            <div className="flex flex-wrap items-center justify-end gap-2">
              {editor.project && !editor.removing && <Button type="button" className="button-quiet mr-auto text-red-700"
                disabled={busy} onClick={() => { setFailure(''); setEditor({ ...editor, removing: true }); }}>移除项目</Button>}
              <Button type="button" className="button-secondary" disabled={busy}
                onClick={() => setEditor(editor.removing ? { ...editor, removing: false } : undefined)}>取消</Button>
              <Button disabled={busy || (!editor.removing && !editor.name.trim())}>
                {editor.removing ? '确认移除' : editor.project ? '保存' : '创建项目'}
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </div>
  );
}

