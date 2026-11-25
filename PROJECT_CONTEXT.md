# Research Workbench 项目上下文

更新：2026-11-25。开发对象：本 Git 仓库。历史批次交付流水已无损归档至 [历史上下文归档](docs/history/project-context-archive-20261124.md)，本文只维护当前接续状态与核心基线。

## 一、当前接续状态

- **最新迁移与数据库基线（2026-11-25）**：代码迁移与开发部署的正式业务库均已推进至 `0061`（升级前已完成完整备份：`artifacts/backups/ded90504f04c` 对应 `0057`、`artifacts/backups/91372a5c8dc5` 对应 `0057 -> 0061`）；已完成 `0057`–`0061` 里程碑封板提交并推送 `origin/main`。
- **阶段收口已完成项**：
  - 通用辅助模型工具 `call_model` 与 `auxiliary` 工具组已彻底下线（迁移 `0057`，见 [决策 0032](docs/decisions/0032-retire-auxiliary-call-model.md)），应用内部单次调用及历史兼容保留。
  - 工程治理与 CI（2026-11-24）：新增 [数据库总览与核心状态机](docs/database-and-state-machines.md)（7 大表群、5 条 DB 红线、五轨状态机）；[AGENTS.md](AGENTS.md) 将 OpenAI Codex 列入长期参考对象；已新增离线零付费 GitHub CI（[.github/workflows/ci.yml](.github/workflows/ci.yml)），CD 镜像发布暂缓。
- **研发助手主线进展**：
  - 按 [团队研发功能 PRD V0.4](docs/product/团队研发功能PRD.md)、[研发工作台技术设计 V0.6](docs/technical/研发工作台技术设计.md) 与 [产品设计进度](docs/product-design-progress.md) 沿 `main` 推进（先个人研发工作台，再两账号协作试用）。
  - 个人研发闭环已完成隔离验收（简报与方法版本绑定、显式授权自主运行、固定地图与证据回查、同主线对比与采纳、审批实验与固定成果验收）。
  - 已完成三阶段体验与可靠性加固（2026-11-24）：① DSML 协议泄漏有界原地纠偏（最多 1 次，不重放中毒检查点，在原 `ResearchPermit` 剩余额度下干净续跑）与 `submit_research_delivery` 平铺参数加固；② 实时可观测性升级（后端脱敏流式 `reasoning_delta`、会话与研发标签共用 `LiveRunPanel` 可折叠思考过程与持久化工具步骤时间线、自主推进弹窗三档预设＋联网开关＋折叠安全上限）；③ 研发分支归档恢复（`unarchive` 仅清空 `archived_at`，不复活旧许可/旧实验、不自动切写入分支）与离线零付费 GitHub CI。
  - 已接入联合 BM25、模型请求证据、聊天内资料侧栏、三条空白会话试用题、正式提交预览/只读快照、从提交创建分支与后续写入分支选择（写入分支迁移 `0058`、归档迁移 `0059`）。
  - 空白会话可打开「查看已完成案例」。固定记录来自同一个运维问答试点的联合问答、两个研发分支、真实隔离实验和固定成果验收。模拟资料与确定性模型替身的范围已在界面标明。
  - 已取消或已验收的任务均可从固定提交分叉，显式确认后在原任务和会话开启新一轮。旧许可和历史验收保留，下一次运行由真人发消息或重新授予自主许可。
  - 先前的独立 Milvus 对照仅是历史验证。当前应用已接入 PostgreSQL pgvector、联合 BM25 的 RRF 融合以及可选重排。开发部署的业务库已现场核对为 `0061`，混合检索已启用，Harrier 640 维索引档案处于 `ready`。管理入口为「智能体设置 → 检索与向量」。隔离环境已验证真实嵌入、混合命中和 DeepSeek 基于证据作答，完整工具循环由离线回归覆盖。[检索实现](docs/architecture.md#联合词法与向量检索) · [验收](docs/progress.md#2026-11-25应用-rag-接入验收)
  - 已实现会话项目归类。支持项目内新建、重命名、置顶、拖动或菜单移动会话，以及移除项目后将会话退回未分组。[0060 验收](docs/progress.md#2026-11-25会话项目归类及恢复验证)已在隔离环境通过。
  - 智能体设置的技能页可从已验收运维案例生成带证据的方法提案。人工查看差异后可发布或驳回，实际发布沿用原 Skill 管理和运行冻结。隔离环境已验收；正式技能包尚无此发布。[方法提案验收](docs/progress.md#2026-11-25从已验收运维试点形成技能提案)
  - **下一步接续重点**：借鉴 Hermes 自进化体系专项（先补全安全防线、双槽记忆与容量上限、使用遥测、Pin/Lock、缓存稳定策略等设计蓝图再拆期实施）；Playwright MCP 本地浏览器取证、更广的动态容器实验隔离与两账号协作按后续阶段推进。
- **既有未闭环工程项**：2026-10-31 APP-02～APP-05 独立验收仍有未通过项，记录在 [复核报告](docs/delivery/app02-app05-acceptance-20261031.md) 与 `docs/development-issues.md`（`DEV-121`），APP-01/06/07 继续暂缓。

## 二、当前技术栈与运行基线

- **后端**：Python 3.12 / FastAPI / SQLAlchemy / Alembic / LangChain `create_agent` / LangGraph / Deep Agents 0.7.23 / Celery + Redis / PostgreSQL 17（`pg_search`）。
- **前端**：React + TypeScript + Vite + Tailwind CSS v4（保留 SPA）。
- **运行模式**：
  - 开发模式：`compose.dev.yaml` 只启 `dev` / `postgres` / `redis`，容器内用根 `.venv`（`/workspace/.venv/bin/python`）通过 `python scripts/dev.py` 统一启动 API、worker 和 Vite；单独测试/脚本用 `python scripts/dev.py --run ...`。
  - 生产部署：`compose.yaml` 拆分为 `api`、`worker`、`web`（Nginx 静态托管 + 反代）、`postgres`、`redis` 与一次性 `init`。
- **数据与存储分工**：
  - PG 三库隔离：`knowledgeops`（业务 + LangGraph 检查点）、`knowledgeops_accounting`（独立模型账本）、`knowledgeops_test`（测试库）。
  - 工作区文件在 `data/agent-workspace`（`knowledge/documents` 为可编辑视图，`knowledge/versions` 等为不可变正文）；PG 存元数据、版本、引用、草稿、运行与派生索引；Redis 仅作 Celery broker、SSE 实时桥与确定检查点的可丢弃缓存。
- **执行与安全边界**：
  - 当前通用任务使用 worker 内 `LocalShellBackend`（逻辑工作区隔离，不宣称 OS/容器级隔离）；研发实验使用独立交换目录 + 受信固定脚本摘要校验（默认关闭）。
  - 暂不引入多租户、对象存储容器、递归委派或独立记忆队列；MCP 默认服务为空；真实模型开关由 `.env` 的 `MODEL_CALLS_ENABLED` 控制。

## 三、核心文档入口

- 工程规则：[AGENTS.md](AGENTS.md)
- 全部文档导航：[docs/README.md](docs/README.md)
- 数据库主干与核心状态机（改表/改流程必读）：[docs/database-and-state-machines.md](docs/database-and-state-machines.md)
- 完整架构实现说明：[docs/architecture.md](docs/architecture.md)
- 实时进展与问题台账：[docs/progress.md](docs/progress.md)、[docs/development-issues.md](docs/development-issues.md)
