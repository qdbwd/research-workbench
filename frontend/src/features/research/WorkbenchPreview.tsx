import { useState } from 'react';
import { ChevronDown, FileText, FlaskConical, GitBranch, LayoutList } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, Empty, ErrorBox, Loading, Markdown } from '../../shared/ui/components';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { SettingsRow } from '../../shared/ui/SettingsRow';
import { BackLink, DetailDialog, WorkbenchHeading, WorkbenchPanel } from '../../shared/ui/Workbench';
import { ResearchBrief, sampleBrief } from './ResearchBrief';

const lab = [
  { icon: FlaskConical, href: '#brief', name: '研发简报' },
  { icon: LayoutList, href: '#collections', name: '资料与树' },
  { icon: FileText, href: '#controls', name: '表单与弹层' },
  { icon: GitBranch, href: '#feedback', name: '阅读与反馈' },
];

export function WorkbenchPreview() {
  const [popup, setPopup] = useState<'dialog' | 'drawer' | null>(null);
  const [menu, setMenu] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const matches = '检索基线'.includes(query.trim());
  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <BackLink to="/">返回聊天</BackLink>
        <WorkbenchHeading
          eyebrow="开发预览"
          title="界面组件预览"
          description="检查研发、资料和管理界面中共用的布局、控件与反馈。"
        />
        <nav aria-label="样例目录" className="flex flex-wrap gap-2">
          {lab.map(({ icon: Icon, href, name }) => (
            <a key={href} href={href} className="button button-secondary">
              <Icon size={16} aria-hidden="true" />
              {name}
            </a>
          ))}
        </nav>
      </div>
      <div id="brief">
        <ResearchBrief title="研发简报样例" brief={sampleBrief} />
      </div>
      <div id="collections" className="grid gap-5 lg:grid-cols-2">
        <WorkbenchPanel title="材料与研究树" description="列表用于逐条操作，树用于呈现父子关系。">
          <label className="grid gap-2 text-sm">
            搜索材料
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          {matches ? (
            <ul className="my-3 divide-y divide-line p-0">
              <li className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-2">
                <span>检索基线</span>
                <Badge status="published" />
              </li>
            </ul>
          ) : (
            <Empty title="没有匹配材料">调整搜索词后重试。</Empty>
          )}
          <DetailDialog title="比较检索方案">
            <ul className="mt-2 mb-0 list-disc pl-5 text-sm">
              <li>确认漏检分布</li>
              <li>检查切片边界</li>
            </ul>
          </DetailDialog>
        </WorkbenchPanel>
        <WorkbenchPanel title="运行诊断" description="列表留给近期活动，指标和状态可以直接对照。">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <caption className="sr-only">研发活动样例</caption>
              <thead>
                <tr className="border-b border-line">
                  <th className="p-2">步骤</th>
                  <th className="p-2">状态</th>
                  <th className="p-2">耗时</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-line">
                  <td className="p-2">读取材料</td>
                  <td className="p-2">
                    <Badge status="completed" />
                  </td>
                  <td className="p-2">0.4 秒</td>
                </tr>
                <tr>
                  <td className="p-2">验证索引</td>
                  <td className="p-2">
                    <Badge status="running" />
                  </td>
                  <td className="p-2">1.2 秒</td>
                </tr>
              </tbody>
            </table>
          </div>
        </WorkbenchPanel>
      </div>
      <div id="controls" className="grid items-start gap-5 lg:grid-cols-2">
        <WorkbenchPanel title="设置操作行">
          <SettingsRow title="主智能体工具" description="继承默认本地工具选择">
            <Button type="button" className="button-secondary" onClick={() => setPopup('dialog')}>配置选择</Button>
            <Button type="button" className="button-secondary" onClick={() => setPopup('drawer')}>查看有效工具</Button>
          </SettingsRow>
        </WorkbenchPanel>
        <WorkbenchPanel title="Skill 导入表单" description="短表单和确认操作放在模态窗口内。">
          <form
            className="grid min-w-0 grid-cols-1 gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              setSubmitted(true);
            }}
          >
            <label className="grid gap-1.5 text-sm">
              显示名称
              <input required placeholder="例如检索验收" />
            </label>
            <label className="grid gap-1.5 text-sm">
              可见范围
              <select defaultValue="project">
                <option value="project">当前项目</option>
                <option value="private">仅自己</option>
              </select>
            </label>
            <label className="grid gap-1.5 text-sm">
              技能文件
              <input type="file" accept=".md,.zip" className="w-full min-w-0" />
            </label>
            <label className="m-0 flex flex-row items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
              />
              导入后启用
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit">确认导入</Button>
              <Button type="button" className="button-secondary" onClick={() => setPopup('dialog')}>
                查看确认框
              </Button>
              {submitted && (
                <span role="status" className="text-sm text-brand">
                  已验证表单
                </span>
              )}
            </div>
          </form>
        </WorkbenchPanel>
        <WorkbenchPanel title="弹层与详情" description="轻操作放在弹出层，阅读细节用侧抽屉。">
          <div className="flex flex-wrap gap-2">
            <div
              className="relative"
              onKeyDown={(event) => {
                if (event.key === 'Escape' && menu) {
                  setMenu(false);
                  event.currentTarget.querySelector('button')?.focus();
                }
              }}
            >
              <Button
                className="button-secondary"
                aria-expanded={menu}
                onClick={() => setMenu((value) => !value)}
              >
                更多操作 <ChevronDown size={16} aria-hidden="true" />
              </Button>
              {menu && (
                <div className="absolute top-full left-0 z-10 mt-2 min-w-36 rounded-lg border border-line bg-white p-2 shadow-md">
                  <Link to="/sources" className="block rounded p-2 text-sm hover:bg-canvas">
                    打开材料列表
                  </Link>
                </div>
              )}
            </div>
            <Button className="button-secondary" onClick={() => setPopup('drawer')}>
              查看侧抽屉
            </Button>
          </div>
        </WorkbenchPanel>
      </div>
      <div id="feedback" className="grid items-start gap-5 lg:grid-cols-2">
        <WorkbenchPanel title="知识详情" description="连续正文、带边界的代码与可追溯资料。">
          <Markdown
            text={
              '比较搜索前，先固定样本与评估口径。\n\n```python\nassert len(results) <= limit\n```'
            }
          />
          <Link to="/knowledge" className="button button-secondary mt-2">
            查看知识库
          </Link>
        </WorkbenchPanel>
        <WorkbenchPanel title="状态与反馈">
          <ErrorBox message="示例：版本已更新，请核对最新版后重新提交。" />
          <div className="flex flex-wrap gap-2">
            <Button disabled>正在提交…</Button>
            <Badge status="completed" />
          </div>
          <Loading />
          <Empty title="暂无结果">结果将在这里显示。</Empty>
        </WorkbenchPanel>
      </div>
      {popup === 'dialog' && (
        <Dialog
          title="确认移除材料"
          onClose={() => setPopup(null)}
          footer={
            <>
              <Button className="button-secondary" onClick={() => setPopup(null)}>
                取消
              </Button>
              <Button className="bg-danger text-white" onClick={() => setPopup(null)}>
                确认移除
              </Button>
            </>
          }
        >
          这条材料将从当前列表移除。样例使用本地演示数据。
        </Dialog>
      )}
      {popup === 'drawer' && (
        <Dialog
          title="材料预览"
          placement="side"
          onClose={() => setPopup(null)}
          footer={
            <Button className="button-secondary" onClick={() => setPopup(null)}>
              关闭预览
            </Button>
          }
        >
          <Markdown text={'## 检索基线\n\n逐条检查评分、排序和引用位置。'} />
        </Dialog>
      )}
    </div>
  );
}
