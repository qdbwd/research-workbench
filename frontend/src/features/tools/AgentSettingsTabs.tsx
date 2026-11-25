import { NavLink } from 'react-router-dom';

const sections = [
  ['/tools', '内置工具'],
  ['/skills', '技能'],
  ['/mcp', 'MCP 连接'],
  ['/subagents', '子智能体'],
  ['/retrieval', '检索与向量'],
] as const;

export function AgentSettingsTabs() {
  return (
    <nav aria-label="智能体设置" className="mb-5 flex flex-wrap gap-1 border-b border-line">
      {sections.map(([to, label]) => (
        <NavLink key={to} to={to}
          className="border-b-2 border-transparent px-3 py-2 text-sm text-control hover:no-underline [&.active]:border-brand [&.active]:font-semibold [&.active]:text-brand">
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
