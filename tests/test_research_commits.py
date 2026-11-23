"""正式提交只由用户产生，使用真实 API、隔离 PG 与固定资料。"""

from test_api_worker import client as api_client
from test_api_worker import services as api_services

client = api_client
services = api_services


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
    assert changed.status_code == 200, changed.text
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
