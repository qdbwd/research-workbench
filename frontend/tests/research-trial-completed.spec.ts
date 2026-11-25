import { readFileSync, writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import type { Evidence } from '../src/shared/types/types';
import type { ResearchMap } from '../src/features/research/mapApi';

// 保存固定摘录以便演示随版本发布，隔离库的 URL 不可用作产品里的阅读入口。
function portableMap(map: ResearchMap) {
  return { ...map, nodes: map.nodes.map(node => ({
    ...node, evidence: node.evidence.map(item => ({ ...item, url: null })),
  })) };
}

test('records the completed operations pilot from real fixed commits and a sandbox receipt', async ({ page, request }) => {
  test.skip(!process.env.KNOWLEDGEOPS_TEST_EXPERIMENT_EXCHANGE, 'requires the independent fixture runner');
  test.setTimeout(90_000);

  async function post(path: string, data?: Record<string, unknown>, status = 200) {
    const response = await request.post(path, { data });
    expect(response.status(), await response.text()).toBe(status);
    return response.json();
  }

  async function get(path: string) {
    const response = await request.get(path);
    expect(response.status(), await response.text()).toBe(200);
    return response.json();
  }

  async function commit(branchPath: string, operation_id: string, message: string) {
    const preview = await get(branchPath + '/commit-preview');
    return post(branchPath + '/commits', { operation_id, message, preview_hash: preview.preview_hash }, 201);
  }

  const catalog = await get('/api/research-trial');
  const chat = await post('/api/conversations', { trial_case: catalog.key }, 201);
  const chatPath = '/api/conversations/' + chat.id;
  const question = '按最新规程，紧急变更需要谁批准？结合值班操作手册说明上线后先看什么。';
  const first = await post(chatPath + '/messages', { request_key: 'pilot-question', content: question }, 202);
  const answer = await post('/api/__offline/research-trial/run/' + first.id);
  expect(answer.status, JSON.stringify(answer.outcome)).toBe('completed');
  expect(answer.outcome.reference_citations).toHaveLength(2);
  const evidence: Evidence[] = await Promise.all(answer.outcome.reference_citations.map((id: string) => get('/api/evidence/' + id)));
  expect(evidence.map(item => item.title).sort()).toEqual(['生产紧急变更规程', '值班操作手册'].sort());
  expect(evidence.find(item => item.title === '生产紧急变更规程')?.version).toBe(2);

  const data = readFileSync(new URL('../../tests/fixtures/operations_pilot_replay.json', import.meta.url), 'utf8');
  const source = await post('/api/sources/text', {
    title: '试点问答失败记录的实验输入', content: data, conversation_id: chat.id,
  }, 201);
  const task = await post(chatPath + '/research-task', {
    operation_id: 'pilot-research', goal: catalog.title,
  }, 201);
  const base = '/api/research-tasks/' + task.id;
  const main = base + '/branches/' + task.main_branch_id;
  await post(main + '/revisions', {
    operation_id: 'pilot-brief', expected_revision_id: task.head.id, expected_control_version: task.control_version,
    changes: { brief: {
      scope: '用现行规程、值班手册和三条失败记录，核对上线前问答的证据路径。',
      acceptance_criteria: '逐条定位失败阶段，复查对应实验回执，并给出能否试点及开放条件。',
    } },
    reason: '确认本次研发的范围和验收要求。',
  }, 201);
  const baseline = await commit(main, 'pilot-baseline', '固定试点资料与问题');
  const branch = await post(base + '/branches', {
    operation_id: 'pilot-fork', source_commit_id: baseline.id, name: '保留原排序',
  }, 201);
  const fork = await commit(base + '/branches/' + branch.id, 'pilot-fork-baseline', '保留排查前的固定状态');

  const explore = await post(chatPath + '/messages', {
    request_key: 'pilot-explore', content: '检查三条试点失败记录，设计只改变候选排序的对照实验。',
  }, 202);
  const testRun = '/api/__offline/research-experiment/run/';
  const result = await post(testRun + explore.id + '?source_id=' + source.id);
  expect(result.status, JSON.stringify(result)).toBe('completed');
  const [attempt] = (await get(base + '/experiments')).items;
  expect(attempt.status).toBe('awaiting_approval');
  const pending = await get(base);
  const attemptPath = base + '/experiments/' + attempt.id;
  await post(attemptPath, {
    operation_id: 'pilot-approve', action: 'approve', reason: '固定输入和脚本均已核对。',
    expected_revision_id: pending.head.id, expected_control_version: pending.control_version,
  });
  await expect.poll(async () => {
    await post('/api/__offline/research-experiment/tick');
    return (await get(attemptPath)).status;
  }, { timeout: 30_000 }).toBe('completed');

  const final = await post(chatPath + '/messages', {
    request_key: 'pilot-report', content: '阅读实际实验结果，更新研究地图并提交试点建议。',
  }, 202);
  const submitted = await post(testRun + final.id + '?source_id=' + source.id + '&attempt=' + attempt.id);
  expect(submitted).toMatchObject({ status: 'completed', outcome: { kind: 'research_delivery' } });
  const [delivery] = (await get(base + '/deliveries')).items;
  const beforeReview = await get(base);
  expect(beforeReview.status).toBe('awaiting_acceptance');
  await post(base + '/deliveries/' + delivery.id + '/review', {
    operation_id: 'pilot-accept', action: 'accept', source_revision_id: delivery.source_revision_id,
    expected_control_version: beforeReview.control_version, reason: '已核对三个失败样本、固定回执和试点前置条件。',
  });
  const current = await get(base);
  expect(current.status).toBe('completed');
  const fixed = await commit(main, 'pilot-final', '固定实验结果与已验收的试点建议');
  const difference = await get(base + '/commit-comparison?before=' + fork.id + '&after=' + fixed.id);
  expect(difference.common_commit_id).toBe(baseline.id);
  expect(difference.assets.map((row: { kind: string }) => row.kind)).toEqual(['experiments', 'deliveries']);

  const { snapshot, ...revision } = fixed;
  const { experiments: [experiment], deliveries: [report] } = snapshot;
  expect(experiment.status).toBe('completed');
  expect(report.status).toBe('accepted');
  expect(snapshot.map.nodes).toHaveLength(1);

  const followup = await post(base + '/branches', {
    operation_id: 'pilot-reopen', source_commit_id: fixed.id, name: '复核开放条件',
  }, 201);
  await page.goto('/chat/' + chat.id);
  await page.getByRole('button', { name: '查看研发简报' }).click();
  await page.getByRole('button', { name: '分支与提交' }).click();
  await page.getByLabel('查看分支').selectOption(followup.id);
  await page.getByRole('button', { name: '在此分支开始新一轮' }).click();
  const restart = page.getByRole('dialog', { name: '从固定提交开始新一轮' });
  await restart.getByRole('button', { name: '确认开始新一轮' }).click();
  await expect(restart).toHaveCount(0);
  await expect(page.getByText('当前会话写入 复核开放条件')).toBeVisible();
  expect(await get(base)).toMatchObject({ id: task.id, status: 'open', write_branch_id: followup.id });

  const displayEvidence = evidence.map(({ id, text, title, version, start, end, knowledge_version_id }) => ({
    id, text, title, version, start, end, kind: knowledge_version_id ? 'knowledge' : 'material',
  }));
  writeFileSync('../artifacts/verification/operations-pilot-completed.json', JSON.stringify({
    schema_version: 1,
    key: catalog.key,
    title: catalog.title,
    capture: { method: 'deterministic_model_real_runner', answer_run: first.id, research_run: final.id },
    answer: { question, text: answer.outcome.answer, evidence: displayEvidence },
    task: { status: current.status, brief: snapshot.revision.state.brief },
    comparison: {
      common_commit_id: baseline.id,
      before: { id: fork.id, message: fork.message, branch: branch.name },
      after: { id: revision.id, message: revision.message, branch: 'main', snapshot_hash: revision.snapshot_hash },
      asset_kinds: difference.assets.map((row: { kind: string }) => row.kind),
      map: { ...difference.map, before: portableMap(difference.map.before), after: portableMap(difference.map.after) },
    },
    experiment,
    report,
  }, null, 2) + '\n');
});


test('opens the read-only completed case and preserves the unsent chat draft', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?draft=new');
  const composer = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await composer.fill('这条消息还没有发送。');
  const writes: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && request.method() !== 'GET') writes.push(path);
  });
  const endpoint = '**/api/research-trial/completed';
  await page.route(endpoint, route => route.fulfill({ status: 503, json: { detail: '案例暂不可用' } }), { times: 1 });
  await page.getByRole('button', { name: '查看已完成案例' }).click();
  const dialog = page.getByRole('dialog', { name: '查看已完成案例' });
  await expect(dialog.getByText('案例暂不可用', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: '重试', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: '研发运维问答助手能否进入内部试点' })).toBeVisible();
  await expect(dialog.getByText('逐条定位失败阶段，复查对应实验回执，并给出能否试点及开放条件。')).toBeVisible();
  await expect(dialog.getByText('暂缓开放试点。', { exact: false }).first()).toBeVisible();
  const citation = dialog.getByRole('link', { name: '生产紧急变更规程', exact: true });
  await expect(citation.locator('svg')).toBeVisible();
  await citation.click();
  await expect(dialog.locator('[id^="evidence-"]:target')).toContainText('当班负责人事前批准');
  await expect(dialog.getByText('值班操作手册', { exact: true }).first()).toBeVisible();
  await dialog.getByRole('button', { name: /^三条试点问答/ }).click();
  await expect(dialog.getByText(/共同起点/)).toBeVisible();
  await expect(dialog.getByText(/正确证据已在上下文/)).toBeVisible();
  await dialog.getByRole('button', { name: '查看固定回执' }).click();
  await expect(page.getByRole('dialog', { name: '查看固定回执' }).getByText('输入 SHA256')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.screenshot({ path: '../artifacts/verification/research-pilot-completed-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: '../artifacts/verification/research-pilot-completed-narrow.png' });
  await page.keyboard.press('Escape');
  await expect(composer).toHaveValue('这条消息还没有发送。');
  expect(writes).toEqual([]);
});

