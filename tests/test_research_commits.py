"""正式提交只由用户产生，使用真实 API、隔离 PG 与固定资料。"""

from test_api_worker import client as api_client
from test_api_worker import services as api_services

client = api_client
services = api_services


def test_cancel_fork_restart_and_archive_retains_history(client, services):
    from sqlalchemy import select

    from knowledgeops_agent.app.lifecycle.asset_references import reachable_assets

    chat = client.post('/api/conversations', json={'trial_case': 'operations-pilot-v1'}).json()
    conversation = f"/api/conversations/{chat['id']}"
    task = client.post(conversation + '/research-task', json={
        'operation_id': 'begin', 'goal': '验证内部试点的恢复路径',
    }).json()
    base = f"/api/research-tasks/{task['id']}"
    main = f"{base}/branches/{task['main_branch_id']}"
    preview = client.get(main + '/commit-preview').json()
    blocked = client.post(main + '/archive', json={
        'operation_id': 'protect-writing', 'expected_revision_id': task['head']['id'],
    })
    assert blocked.status_code == 409 and blocked.json()['code'] == 'branch_in_use'
    saved = client.post(main + '/commits', json={
        'operation_id': 'save', 'message': '冻结试点依据', 'preview_hash': preview['preview_hash'],
    })
    assert saved.status_code == 201, saved.text
    commit = saved.json()
    previous = client.post(conversation + '/messages', json={
        'request_key': 'old-run', 'content': '先调查原始候选。',
    }).json()
    stopped = client.post(base + '/control', json={
        'operation_id': 'stop', 'expected_control_version': 1, 'action': 'cancel',
    }).json()
    assert stopped['status'] == 'closed'
    old_messages = client.get(conversation + '/messages').json()
    fork = client.post(base + '/branches', json={
        'operation_id': 'fork', 'source_commit_id': commit['id'], 'name': '复核范围后继续',
    }).json()
    restart_url = f"{base}/branches/{fork['id']}/restart"
    body = {'operation_id': 'restart', 'expected_control_version': stopped['control_version']}
    started = client.post(restart_url, json=body)
    assert started.status_code == 200, started.text
    assert started.json()['status'] == 'open'
    assert started.json()['write_branch_id'] == fork['id']
    assert client.post(restart_url, json=body).json() == started.json()
    assert client.get(conversation + '/messages').json() == old_messages
    assert services.jobs.get_run(previous['id'])['status'] == 'cancelled'
    assert services.jobs.claim('no-implicit-restart') is None

    current = client.get(base + '/branches').json()['items'][0]
    archive_request = {'operation_id': 'archive', 'expected_revision_id': current['head']['id']}
    archived = client.post(main + '/archive', json=archive_request)
    assert archived.status_code == 200, archived.text
    assert archived.json()['archived_at']
    assert client.post(main + '/archive', json=archive_request).json() == archived.json()
    assert client.get(main + '/commits/' + commit['id']).json() == commit
    assert client.get(main + '/commits').json()['items'][0]['id'] == commit['id']
    blocked = client.post(base + '/write-branch', json={
        'operation_id': 'return', 'branch_id': task['main_branch_id'],
        'expected_write_version': started.json()['write_version'],
    })
    assert blocked.status_code == 409 and blocked.json()['code'] == 'branch_archived'
    assert client.get(main + '/commit-preview').status_code == 409
    copied = client.post(base + '/branches', json={
        'operation_id': 'fork-archived', 'source_commit_id': commit['id'], 'name': '另一条验证路线',
    })
    assert copied.status_code == 201 and copied.json()['head']['state'] == commit['snapshot']['revision']['state']
    with services.sessions() as session:
        assets = reachable_assets(session)
        pinned = commit['snapshot']['material_refs'][0]['source_version_id']
        assert session.scalar(select(assets.c.node).where(assets.c.node == f'source_version:{pinned}'))
    sent = client.post(conversation + '/messages', json={
        'request_key': 'new-run', 'content': '从固定的试点范围重新核对。',
    }).json()
    assert sent['input']['research']['branch_id'] == fork['id']
    assert sent['id'] != previous['id']


def test_write_branch_switch_preserves_admitted_run_and_routes_next_message(client, services):
    from knowledgeops_agent.persistence.models import Run
    from knowledgeops_agent.research.execution import active_binding

    chat = client.post('/api/conversations', json={'trial_case': 'operations-pilot-v1'}).json()
    conversation = f"/api/conversations/{chat['id']}"
    task = client.post(conversation + '/research-task', json={
        'operation_id': 'begin', 'goal': '验证内部试点的两条研究路线',
    }).json()
    base = f"/api/research-tasks/{task['id']}"
    main = f"{base}/branches/{task['main_branch_id']}"
    preview = client.get(main + '/commit-preview').json()
    commit = client.post(main + '/commits', json={
        'operation_id': 'fixed', 'message': '固定试点范围', 'preview_hash': preview['preview_hash'],
    }).json()
    fork = client.post(base + '/branches', json={
        'operation_id': 'fork', 'name': '复核候选', 'source_commit_id': commit['id'],
    }).json()
    original = client.post(conversation + '/messages', json={
        'request_key': 'message-old', 'content': '主线先核对证据来源。',
    })
    assert original.status_code == 202, original.text
    original_id = original.json()['id']
    command = {'operation_id': 'select-fork', 'expected_write_version': 1, 'branch_id': fork['id']}
    changed = client.post(base + '/write-branch', json=command)
    assert changed.status_code == 200, changed.text
    assert changed.json()['write_branch_id'] == fork['id']
    assert changed.json()['head']['id'] == fork['head']['id']
    assert changed.json()['control_version'] == task['control_version']
    assert client.post(base + '/write-branch', json=command).json() == changed.json()
    assert client.get(conversation + '/research-task').json() == changed.json()
    stale = client.post(base + '/write-branch', json={**command, 'operation_id': 'stale', 'branch_id': task['main_branch_id']})
    assert stale.status_code == 409
    assert stale.json()['code'] == 'write_branch_conflict'
    with services.sessions() as session:
        old = session.get(Run, original_id).input_data['research']
        assert active_binding(session, old).id == task['main_branch_id']
    blocked_archive = client.post(main + '/archive', json={
        'operation_id': 'archive-while-queued', 'expected_revision_id': old['research_revision_id'],
    })
    assert blocked_archive.status_code == 409 and blocked_archive.json()['code'] == 'branch_busy'

    next_message = client.post(conversation + '/messages', json={
        'request_key': 'message-new', 'content': '分支里核对候选排序。',
    })
    assert next_message.status_code == 202, next_message.text
    with services.sessions() as session:
        run = session.get(Run, next_message.json()['id'])
        assert active_binding(session, run.input_data['research']).id == fork['id']
        assert session.get(Run, original_id).input_data['research'] == old
    repeated = client.post(conversation + '/messages', json={
        'request_key': 'message-old', 'content': '主线先核对证据来源。',
    })
    assert repeated.json()['id'] == original_id
    for action, version, status in [('pause', 1, 'paused'), ('resume', 2, 'open')]:
        response = client.post(base + '/control', json={
            'action': action, 'operation_id': action, 'expected_control_version': version,
        })
        assert response.status_code == 200, response.text
        assert response.json()['status'] == status
    resumed = services.jobs.get_run(original_id)
    assert resumed['status'] == 'queued'
    assert resumed['checkpoint_id'] == original.json()['checkpoint_id']
    assert resumed['input']['research'] == {**old, 'control_version': 3}


def test_commit_preview_blocks_stale_progress_and_can_fork_frozen_case(client):
    chat = client.post('/api/conversations', json={'trial_case': 'operations-pilot-v1'}).json()
    task = client.post(f"/api/conversations/{chat['id']}/research-task", json={
        'operation_id': 'begin', 'goal': '研发运维问答助手能否进入内部试点',
    }).json()
    base = f"/api/research-tasks/{task['id']}"
    branch = f"{base}/branches/{task['main_branch_id']}"
    assert client.get(branch + '/commits').json() == {'items': []}
    preview = client.get(branch + '/commit-preview').json()
    assert {item['title'] for item in preview['snapshot']['material_refs']} == {
        '值班操作手册', '服务探针说明', '试点前故障复盘', '试点问答失败记录',
    }
    changed = client.post(branch + '/revisions', json={
        'operation_id': 'edit', 'expected_revision_id': task['head']['id'], 'expected_control_version': 1,
        'changes': {'brief': {'next_action': '核对规程来源'}}, 'reason': '记录下一步',
    })
    assert changed.status_code == 201, changed.text
    body = {'operation_id': 'save', 'message': '固定基线', 'preview_hash': preview['preview_hash']}
    assert client.post(branch + '/commits', json=body).json()['code'] == 'commit_conflict'
    preview = client.get(branch + '/commit-preview').json()
    body['preview_hash'] = preview['preview_hash']
    accepted = client.post(branch + '/commits', json=body)
    assert accepted.status_code == 201, accepted.text
    commit = accepted.json()
    assert client.post(branch + '/commits', json=body).json() == commit
    assert commit['number'] == 1
    assert commit['snapshot']['revision']['state']['brief']['next_action'] == '核对规程来源'

    created = client.post(base + '/branches', json={
        'operation_id': 'fork', 'source_commit_id': commit['id'], 'name': '重排对照',
    })
    assert created.status_code == 201, created.text
    fork = created.json()
    assert fork['head']['state'] == commit['snapshot']['revision']['state']
    assert client.get(base).json()['head']['id'] == commit['revision_id']
    fork_url = f"{base}/branches/{fork['id']}"
    fork_preview = client.get(fork_url + '/commit-preview').json()
    chosen = client.post(fork_url + '/commits', json={
        'operation_id': 'fork-save', 'message': '保留对照起点', 'preview_hash': fork_preview['preview_hash'],
    }).json()
    diff = client.get(f"{base}/commit-comparison", params={'before': commit['id'], 'after': chosen['id']}).json()
    assert diff['common_commit_id'] == commit['id']
    assert diff['map']['changes'] == []
    assert client.get(branch + '/commits/' + commit['id']).json() == commit


def test_unarchive_branch_restores_active_without_reviving_permits_or_switching_writer(client, services):
    """解除归档仅清空 archived_at：不复活旧 revoked 授权，不抢占当前 writing_branch_id，且支持幂等与并发校验。"""
    from knowledgeops_agent.persistence.models import ResearchPermit

    chat = client.post('/api/conversations', json={'trial_case': 'operations-pilot-v1'}).json()
    task = client.post(f"/api/conversations/{chat['id']}/research-task", json={
        'operation_id': 'begin-unarchive', 'goal': '验证分支归档恢复边界',
    }).json()
    base = f"/api/research-tasks/{task['id']}"
    main_id = task['main_branch_id']
    main_url = f"{base}/branches/{main_id}"

    # 1. 创建提交并 Fork 出新分支，把写入切到新分支
    preview = client.get(main_url + '/commit-preview').json()
    commit = client.post(main_url + '/commits', json={
        'operation_id': 'commit-1', 'message': '基线提交', 'preview_hash': preview['preview_hash'],
    }).json()
    forked = client.post(base + '/branches', json={
        'operation_id': 'fork-alt', 'source_commit_id': commit['id'], 'name': 'alt-branch',
    }).json()

    # 在 main 分支上先创建一条已撤回的旧 Permit（模拟归档前存在的授权）
    with services.sessions.begin() as session:
        session.add(ResearchPermit(
            id='permit-old-main',
            task_id=task['id'],
            branch_id=main_id,
            scope_revision_id=task['head']['id'],
            status='active',
            budgets={'model_requests': 5, 'tool_calls': 10, 'searches': 0, 'active_seconds': 60},
            usage={'model_requests': 0, 'tool_calls': 0, 'searches': 0, 'active_ms': 0},
        ))

    switched = client.post(base + '/write-branch', json={
        'operation_id': 'switch-to-alt', 'branch_id': forked['id'], 'expected_write_version': task['write_version'],
    })
    assert switched.status_code == 200, switched.text
    current_task = switched.json()
    assert current_task['write_branch_id'] == forked['id']

    # 2. 归档 main 分支 -> 旧 Permit 被撤回
    archived = client.post(main_url + '/archive', json={
        'operation_id': 'archive-main', 'expected_revision_id': task['head']['id'],
    })
    assert archived.status_code == 200, archived.text
    assert archived.json()['archived_at'] is not None
    with services.sessions() as session:
        assert session.get(ResearchPermit, 'permit-old-main').status == 'revoked'

    # 3. 过期版本恢复 -> 409 Conflict
    stale = client.post(main_url + '/unarchive', json={
        'operation_id': 'unarchive-stale', 'expected_revision_id': '00000000-0000-4000-8000-000000000000',
    })
    assert stale.status_code == 409 and stale.json()['code'] == 'branch_conflict'

    # 4. 正常恢复 + 幂等重放
    restored = client.post(main_url + '/unarchive', json={
        'operation_id': 'unarchive-main', 'expected_revision_id': task['head']['id'],
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()['archived_at'] is None
    assert client.post(main_url + '/unarchive', json={
        'operation_id': 'unarchive-main', 'expected_revision_id': task['head']['id'],
    }).json() == restored.json()

    # 5. 验证 3 条恢复边界：旧 Permit 仍为 revoked、写入分支仍为 alt-branch、恢复后可再次切回写入
    with services.sessions() as session:
        assert session.get(ResearchPermit, 'permit-old-main').status == 'revoked'
    after_task = client.get(base).json()
    assert after_task['write_branch_id'] == forked['id']

    switched_back = client.post(base + '/write-branch', json={
        'operation_id': 'switch-back-main', 'branch_id': main_id, 'expected_write_version': after_task['write_version'],
    })
    assert switched_back.status_code == 200, switched_back.text
    assert switched_back.json()['write_branch_id'] == main_id
