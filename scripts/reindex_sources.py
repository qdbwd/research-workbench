"""容器内重建材料的派生片段索引。"""

import json

from knowledgeops_agent.core.config import get_settings
from knowledgeops_agent.persistence.maintenance import managed_main
from knowledgeops_agent.persistence.models import make_sessions
from knowledgeops_agent.retrieval.index import rebuild_source_index
from knowledgeops_agent.retrieval.search import INDEX_CONTRACT


def main():
    settings = get_settings()
    sessions = make_sessions(settings.database_url, workspace_root=settings.workspace_root)
    try:
        print(json.dumps({
            **rebuild_source_index(sessions), "index_contract": INDEX_CONTRACT, "model_calls": 0,
        }))
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    managed_main(main)
