"""下线已废弃的通用辅助模型工具 call_model 及其工具组，保留历史修订与运行记录兼容。"""

from copy import deepcopy

from alembic import op
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledgeops_agent.harness.tools.groups import group_view
from knowledgeops_agent.harness.tools.policy import read_policy, save_policy
from knowledgeops_agent.persistence.models import (
    SubagentDefinition,
    SubagentDefinitionRevision,
    ToolGroup,
    ToolGroupRevision,
)

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

RETIRED_TOOL_ID = "builtin:call_model"
RETIRED_TOOL_NAME = "call_model"
RETIRED_GROUP_ID = "auxiliary"


def upgrade():
    with Session(bind=op.get_bind()) as session:
        # 1. 清理工具组中的 builtin:call_model；仅含该工具的 auxiliary 组标记为删除墓碑。
        # 若用户已自定义往 auxiliary 组加入了其他工具，则保留该组及其在策略/子角色中的引用。
        auxiliary_group_active = False
        for group in session.scalars(select(ToolGroup).order_by(ToolGroup.id)):
            if group.deleted:
                continue
            cleaned_ids = [item for item in group.tool_ids if item != RETIRED_TOOL_ID]
            should_delete = group.id == RETIRED_GROUP_ID and not cleaned_ids
            if cleaned_ids != list(group.tool_ids) or should_delete:
                group.tool_ids = cleaned_ids
                if should_delete:
                    group.deleted = True
                group.revision += 1
                session.add(
                    ToolGroupRevision(
                        group_id=group.id,
                        revision=group.revision,
                        content=group_view(group),
                    )
                )
            if group.id == RETIRED_GROUP_ID and not group.deleted:
                auxiliary_group_active = True

        # 2. 清理当前工具策略中的 builtin:call_model 与已删除 auxiliary 组引用，保留全部历史修订。
        current = read_policy(session)
        updated = deepcopy(current)
        policy_changed = False
        tool_epochs = updated.setdefault("tool_epochs", {})
        main_epochs = updated.setdefault("main_epochs", {})
        if RETIRED_TOOL_ID in updated["enabled"]:
            if updated["enabled"].get(RETIRED_TOOL_ID) is True:
                tool_epochs[RETIRED_TOOL_ID] = tool_epochs.get(RETIRED_TOOL_ID, 0) + 1
            updated["enabled"].pop(RETIRED_TOOL_ID, None)
            policy_changed = True
        if updated.get("main_tools") is not None and RETIRED_TOOL_ID in updated["main_tools"]:
            updated["main_tools"] = [
                item for item in updated["main_tools"] if item != RETIRED_TOOL_ID
            ]
            policy_changed = True
        if (
            not auxiliary_group_active
            and updated.get("main_tool_groups") is not None
            and RETIRED_GROUP_ID in updated["main_tool_groups"]
        ):
            updated["main_tool_groups"] = [
                item for item in updated["main_tool_groups"] if item != RETIRED_GROUP_ID
            ]
            policy_changed = True
        if (
            updated.get("resolved_main_tools") is not None
            and RETIRED_TOOL_ID in updated["resolved_main_tools"]
        ):
            updated["resolved_main_tools"] = [
                item for item in updated["resolved_main_tools"] if item != RETIRED_TOOL_ID
            ]
            main_epochs[RETIRED_TOOL_ID] = main_epochs.get(RETIRED_TOOL_ID, 0) + 1
            policy_changed = True
        if policy_changed:
            save_policy(session, current, updated)

        # 3. 清理活跃子角色定义中对已下线工具或已删除工具组的引用，不改写旧修订。
        for role in session.scalars(select(SubagentDefinition).order_by(SubagentDefinition.id)):
            if role.deleted:
                continue
            new_tools = [item for item in role.tools if item != RETIRED_TOOL_NAME]
            new_groups = (
                [item for item in role.tool_groups if item != RETIRED_GROUP_ID]
                if not auxiliary_group_active and role.tool_groups is not None
                else role.tool_groups
            )
            new_resolved = (
                [item for item in role.resolved_tools if item != RETIRED_TOOL_NAME]
                if role.resolved_tools is not None
                else None
            )
            if (
                new_tools != list(role.tools)
                or new_groups != role.tool_groups
                or new_resolved != role.resolved_tools
            ):
                role.tools = new_tools
                role.tool_groups = new_groups
                role.resolved_tools = new_resolved
                role.revision += 1
                role.authorization_epoch += 1
                content = {
                    "id": role.id,
                    "name": role.name,
                    "display_name": role.display_name,
                    "description": role.description,
                    "system_prompt": role.system_prompt,
                    "tools": role.tools,
                    "skills": role.skills,
                    "enabled": role.enabled,
                    "deleted": role.deleted,
                    "revision": role.revision,
                    "authorization_epoch": role.authorization_epoch,
                    "builtin": False,
                }
                if role.resolved_tools is not None:
                    content.update(tool_groups=role.tool_groups, resolved_tools=role.resolved_tools)
                session.add(
                    SubagentDefinitionRevision(
                        definition_id=role.id,
                        revision=role.revision,
                        content=content,
                    )
                )

        session.flush()


def downgrade():
    # 保留不可变修订历史；不通过降级重新启用已废弃的 call_model。
    pass
