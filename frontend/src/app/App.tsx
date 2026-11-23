import { useCallback, useMemo, useState } from 'react';
import {
  createBrowserRouter,
  createRoutesFromElements,
  Link,
  NavLink,
  Navigate,
  Outlet,
  Route,
  RouterProvider,
  useLocation,
  useOutletContext,
  useParams,
} from 'react-router-dom';
import { Activity, BookOpen, Compass, Files, FlaskConical, GraduationCap, List, Plus, Settings, SlidersHorizontal } from 'lucide-react';
import { useResource } from '../shared/api/api';
import { ChatPage } from '../features/chat/ChatPage';
import { BackgroundDeletions } from '../features/chat/BackgroundDeletions';
import { ConversationLink } from '../features/chat/ConversationLink';
import { ConversationsPage } from '../features/chat/ConversationsPage';
import { ArticlePage, KnowledgePage } from '../features/knowledge/KnowledgePage';
import { ChangesPage, ChangePage } from '../features/changes/ChangesPage';
import { SourcesPage, SourcePage, EvidencePage } from '../features/sources/SourcesPage';
import { WorkspaceFilePage } from '../features/sources/WorkspaceFilePage';
import { RunPage, RunArtifactPage } from '../features/runs/RunsPage';
import { ActivityRunsPage } from '../features/activity/ActivityRunsPage';
import { RunDiagnosticsPage } from '../features/runs/RunDiagnosticsPage';
import { PreferencesPage } from '../features/memory/PreferencesPage';
import { LearningPage } from '../features/learning/LearningPage';
import { LearningProfilesPage } from '../features/learning/LearningProfilesPage';
import { MCPPage } from '../features/mcp/MCPPage';
import { SubagentsPage } from '../features/subagents/SubagentsPage';
import { SubagentEditorPage } from '../features/subagents/SubagentEditorPage';
import { ToolsPage } from '../features/tools/ToolsPage';
import { ApplicationActivity, ActivityIndicator } from '../features/activity/ApplicationActivity';
import { SkillsPage } from '../features/skills/SkillsPage';
import { ToolGroupsPage } from '../features/tools/ToolGroupsPage';
import { SystemPage } from '../features/system/SystemPage';
import { ResearchList, ResearchPage } from '../features/research/ResearchPage';
import { ResearchMapPage } from '../features/research/ResearchMapPage';
import { ResearchExcerptPage } from '../features/research/ResearchExcerptPage';
import { ResearchExperimentsPage } from '../features/research/ResearchExperimentsPage';
import { WorkbenchPreview } from '../features/research/WorkbenchPreview';
import { ErrorBox } from '../shared/ui/components';
import { Button } from '../shared/ui/Button';
import { Dialog } from '../shared/ui/Dialog';
import { WorkspaceContext } from '../shared/state/workspace';
import type { Conversation } from '../shared/types/types';

const navigationStyle =
  'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-ink hover:bg-[#e2ebe2] hover:no-underline [&.active]:bg-[#dceadf] [&.active]:text-brand max-md:gap-1.5 max-md:px-2 max-md:text-xs [&>svg]:shrink-0';

function WorkspaceLayout() {
  const { pathname } = useLocation();
  const [showRecent, setShowRecent] = useState(false);
  const configuringAgent = /^\/(tools|skills|mcp|subagents)(\/|$)/.test(pathname);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);
  const workspace = useMemo(() => ({ version, refresh }), [version, refresh]);
  const conversations = useResource<{ items: Conversation[] }>('/api/conversations/page?limit=12', version);
  // 目录响应异常时外壳仍要能渲染：缺 items 只表示最近列表为空，不整页崩溃。
  const recent = conversations.data?.items ?? [];
  const settings = useResource<{ model: string; model_calls_enabled: boolean }>(
    '/api/settings',
    version,
  );
  return (
    <WorkspaceContext.Provider value={workspace}>
      <ApplicationActivity conversationIds={recent.map((item) => item.id)} version={version}>
      <BackgroundDeletions>
        <div className="app-shell flex min-h-screen max-md:block has-[.chat-page]:h-dvh has-[.chat-page]:overflow-hidden max-md:has-[.chat-page]:flex max-md:has-[.chat-page]:flex-col">
          <aside className="sidebar fixed inset-y-0 left-0 flex w-[214px] shrink-0 flex-col gap-4 overflow-y-auto border-r border-line bg-sidebar px-3 pt-5 pb-4 text-ink max-md:static max-md:grid max-md:w-auto max-md:grid-cols-[1fr_auto] max-md:items-center max-md:gap-2 max-md:border-r-0 max-md:border-b max-md:p-2.5">
            <Link
              className="brand mx-1 flex min-w-0 items-center gap-2.5 text-lg font-semibold text-ink hover:no-underline"
              to="/"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-brand text-white max-md:size-8">
                <Compass size={20} aria-hidden="true" />
              </span>
              <div>
                研发助手
                <small className="block text-[11px] font-normal text-control max-md:hidden">
                  技术调研与知识管理
                </small>
              </div>
            </Link>
            <Link
              className="new-chat button button-secondary justify-start"
              to="/?draft=new"
            >
              <Plus size={17} />
              新对话
            </Link>
            <nav
              aria-label="主要功能"
              className="grid gap-0.5 max-md:col-span-2 max-md:flex max-md:flex-wrap max-md:[&_svg]:w-3.5"
            >
              <NavLink className={navigationStyle} to="/research">
                <FlaskConical size={18} aria-hidden="true" />
                研发工作台
              </NavLink>
              <NavLink className={navigationStyle} to="/knowledge">
                <BookOpen size={18} />
                知识库
              </NavLink>
              <NavLink className={navigationStyle} to="/sources">
                <Files size={18} />
                材料与来源
              </NavLink>
              <NavLink className={navigationStyle} to="/learning">
                <GraduationCap size={18} />
                学习与整理
              </NavLink>
            </nav>
            <section className="recent min-w-0 shrink-0 border-t border-line pt-2 max-md:hidden" aria-label="最近对话">
              <div className="flex items-center justify-between px-2.5 text-xs text-control">
                最近对话
                <Link to="/conversations" className="button button-quiet p-1" aria-label="查看全部会话" title="查看全部会话"><List size={17} aria-hidden="true" /></Link>
              </div>
              {recent.map((chat) => <ConversationLink key={chat.id} chat={chat} />)}
              {!recent.length && <p className="mx-2.5 my-1 text-xs text-control">还没有对话</p>}
            </section>
            <nav className="mt-auto grid gap-0.5 border-t border-line pt-2 max-md:col-span-2 max-md:grid-cols-[repeat(2,minmax(0,1fr))] max-md:pt-1" aria-label="管理">
              <NavLink className={navigationStyle} to="/runs">
                <Activity size={18} />
                运行记录
                <ActivityIndicator />
              </NavLink>
              <Link className={navigationStyle + (configuringAgent ? ' active' : '')} to="/tools"
                aria-current={configuringAgent ? 'true' : undefined}>
                <SlidersHorizontal size={18} /> 智能体设置
              </Link>
              <NavLink className={navigationStyle} to="/system">
                <Settings size={18} />
                系统与维护
              </NavLink>
              <Button className="button-quiet hidden justify-start gap-1.5 text-xs max-md:inline-flex"
                type="button" onClick={() => setShowRecent(true)}><List size={16} /> 最近对话</Button>
            </nav>
            {showRecent && <Dialog title="最近对话" onClose={() => setShowRecent(false)}
              footer={<Link to="/conversations" className="button button-secondary" onClick={() => setShowRecent(false)}>查看全部会话</Link>}>
              <div className="mobile-conversations">
                {recent.map((chat) => <ConversationLink key={chat.id} chat={chat} onOpen={() => setShowRecent(false)} contained />)}
                {!recent.length && <p>还没有对话。</p>}
              </div>
            </Dialog>}
          </aside>
          <main className="main ml-[214px] min-w-0 flex-1 px-11 pt-[35px] pb-[42px] max-lg:p-7 max-md:ml-0 max-md:px-4 max-md:py-6 has-[.chat-page]:flex has-[.chat-page]:min-h-0 has-[.chat-page]:flex-col has-[.chat-page]:px-0 has-[.chat-page]:pt-0 has-[.chat-page]:pb-4 max-md:has-[.chat-page]:pb-[max(10px,env(safe-area-inset-bottom))]">
            <div className="main-content mx-auto w-full max-w-[1160px] has-[.chat-page]:flex has-[.chat-page]:min-h-0 has-[.chat-page]:max-w-none has-[.chat-page]:flex-1 has-[.chat-page]:flex-col">
              <ErrorBox message={settings.error || conversations.error} />
              <Outlet context={settings.data} />
            </div>
          </main>
        </div>
      </BackgroundDeletions>
      </ApplicationActivity>
    </WorkspaceContext.Provider>
  );
}

type ChatSettings = { model: string; model_calls_enabled: boolean } | null;
function LegacyReviewRedirect() {
  const { changesetId } = useParams();
  return <Navigate to={`/knowledge/reviews/${changesetId}`} replace />;
}
function ConfiguredChatPage() {
  const settings = useOutletContext<ChatSettings>();
  return <ChatPage model={settings?.model} modelEnabled={settings?.model_calls_enabled} />;
}

// 使用现有 React Router 的数据路由提供官方导航拦截；依然是浏览器内的 SPA。
export default function App() {
  const [router] = useState(() =>
    createBrowserRouter(
      createRoutesFromElements(
        <Route element={<WorkspaceLayout />}>
          <Route path="/" element={<ConfiguredChatPage />} />
          {import.meta.env.DEV && <Route path="/dev/ui" element={<WorkbenchPreview />} />}
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/chat/:conversationId" element={<ConfiguredChatPage />} />
          <Route path="/chat/:conversationId/deletion" element={<Navigate to="/" replace />} />
          <Route path="/research" element={<ResearchList />} />
          <Route path="/research/:taskId" element={<ResearchPage />} />
          <Route path="/research/:taskId/map" element={<ResearchMapPage />} />
          <Route path="/research/:taskId/experiments" element={<ResearchExperimentsPage />} />
          <Route path="/research/:taskId/evidence/:evidenceId" element={<ResearchExcerptPage />} />
          <Route path="/research/:taskId/branches/:branchId/revisions/:revisionId" element={<ResearchPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/knowledge/:documentId" element={<ArticlePage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/files" element={<WorkspaceFilePage />} />
          <Route path="/deliveries/:deliveryId" element={<WorkspaceFilePage />} />
          <Route path="/sources/:sourceId" element={<SourcePage />} />
          <Route path="/evidence/:evidenceId" element={<EvidencePage />} />
          <Route path="/knowledge/reviews" element={<ChangesPage />} />
          <Route path="/knowledge/reviews/:changesetId" element={<ChangePage />} />
          <Route path="/changes" element={<Navigate to="/knowledge/reviews" replace />} />
          <Route path="/changes/:changesetId" element={<LegacyReviewRedirect />} />
          <Route path="/runs" element={<ActivityRunsPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/runs/:runId/diagnostics" element={<RunDiagnosticsPage />} />
          <Route path="/runs/:runId/artifacts/:artifactId" element={<RunArtifactPage />} />
          <Route path="/preferences" element={<PreferencesPage />} />
          <Route path="/memories" element={<PreferencesPage />} />
          <Route path="/learning" element={<LearningPage />} />
          <Route path="/learning/:taskId" element={<LearningPage />} />
          <Route path="/learning-profiles" element={<LearningProfilesPage />} />
          <Route path="/mcp" element={<MCPPage />} />
          <Route path="/subagents" element={<SubagentsPage />} />
          <Route path="/subagents/new" element={<SubagentEditorPage />} />
          <Route path="/subagents/:definitionId/edit" element={<SubagentEditorPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/tools/groups" element={<ToolGroupsPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route
            path="*"
            element={
              <p>
                页面不存在。<Link to="/">返回工作台</Link>
              </p>
            }
          />
        </Route>,
      ),
    ),
  );
  return <RouterProvider router={router} />;
}
