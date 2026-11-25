"""项目整理使用会话目录事务，与任务运行和资料授权相互独立。"""

from sqlalchemy import select, update

from knowledgeops_agent.app.conversations import fingerprint, touch_directory
from knowledgeops_agent.app.lifecycle.access import require_conversation
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.persistence.models import (
    Conversation,
    ConversationProject,
    OperationReceipt,
)


def _view(row):
    return {"id": row.id, "name": row.name, "pinned": row.pinned}


class ConversationProjects:
    def __init__(self, sessions):
        self.sessions = sessions

    def list(self):
        with self.sessions() as session:
            rows = session.scalars(select(ConversationProject).order_by(
                ConversationProject.pinned.desc(), ConversationProject.created_at, ConversationProject.id,
            ))
            return {"items": [_view(row) for row in rows]}

    def create(self, name: str, request_key: str):
        operation = "conversation-project:create:" + fingerprint(request_key)
        digest = fingerprint(name)
        with self.sessions.begin() as session:
            lock_control(session)
            prior = session.get(OperationReceipt, operation)
            if prior is not None:
                if prior.request_hash != digest:
                    raise ValueError("创建请求已被另一个项目使用。")
                return prior.result
            row = ConversationProject(name=name)
            session.add(row)
            session.flush()
            result = _view(row)
            session.add(OperationReceipt(operation_key=operation, request_hash=digest, result=result))
            return result

    def edit(self, project_id: str, changes: dict):
        with self.sessions.begin() as session:
            lock_control(session)
            row = session.get(ConversationProject, project_id)
            if row is None:
                raise KeyError("项目已不存在。")
            for field, value in changes.items():
                setattr(row, field, value)
            return _view(row)

    def move(self, conversation_id: str, project_id: str | None, expected_revision: int):
        with self.sessions.begin() as session:
            lock_control(session)
            row = require_conversation(session, conversation_id)
            if row.project_id != project_id:
                if row.project_revision != expected_revision:
                    raise ValueError("会话归属已变化，请刷新后再移动。")
                if project_id is not None and session.get(ConversationProject, project_id) is None:
                    raise ValueError("目标项目已移除，请重新选择。")
                row.project_id = project_id
                row.project_revision += 1
                touch_directory(session)
            return {"id": row.id, "project_id": row.project_id, "project_revision": row.project_revision}

    def remove(self, project_id: str):
        with self.sessions.begin() as session:
            lock_control(session)
            row = session.get(ConversationProject, project_id)
            if row is None:
                return
            # 更新全部归属（包括还没有首条消息的新会话），再移除项目本身。
            session.execute(update(Conversation).where(Conversation.project_id == project_id).values(
                project_id=None, project_revision=Conversation.project_revision + 1,
            ))
            session.delete(row)
            touch_directory(session)
