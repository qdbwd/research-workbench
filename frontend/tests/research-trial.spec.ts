import { expect, test } from '@playwright/test';

test('starts a trial from the empty chat and opens real cross-lane citations', async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?draft=new');
  const libraryBefore = await (await page.request.get('/api/sources')).json();
  const knowledgeBefore = await (await page.request.get('/api/knowledge')).json();
  const chatsBefore = await (await page.request.get('/api/conversations')).json();
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST') writes.push(new URL(request.url()).pathname);
  });
  const trial = page.getByRole('region', { name: '研发试用' });
  await expect(trial.getByRole('button', { name: /试用带引用的问答|诊断一条错误回答|评估能否进入内部试点/ })).toHaveCount(3);
  await trial.getByRole('button', { name: '试用带引用的问答' }).click();
  await expect(trial.getByText('生产紧急变更规程')).toBeVisible();
  await expect(trial.getByText('试点问答失败记录')).toBeVisible();
  const input = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await expect(input).toHaveValue(/按最新规程/);
  await input.press('End');
  await input.pressSequentially('请核对版本。');
  expect(writes).toEqual([]);
  expect(await (await page.request.get('/api/conversations')).json()).toEqual(chatsBefore);
  await page.screenshot({ path: '../artifacts/verification/trial-question-desktop.png' });

  const queued = page.waitForResponse((response) => response.request().method() === 'POST'
    && /\/api\/conversations\/[^/]+\/messages$/.test(response.url()));
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const accepted = await queued;
  expect(accepted.status()).toBe(202);
  const run = await accepted.json();
  await expect(page).toHaveURL(`/chat/${run.conversation_id}`);
  await expect(trial).toHaveCount(0);
  expect(await (await page.request.get('/api/sources')).json()).toEqual(libraryBefore);
  expect(await (await page.request.get('/api/knowledge')).json()).toEqual(knowledgeBefore);

  const result = await page.request.post(`/api/__offline/research-trial/run/${run.id}`);
  const answer = await result.json();
  expect(answer.status, JSON.stringify(answer)).toBe('completed');
  expect(answer.outcome.reference_citations).toHaveLength(2);
  await page.getByRole('link', { name: '生产紧急变更规程', exact: true }).click();
  const viewer = page.getByRole('region', { name: '资料查看器' });
  await expect(viewer.getByText('版本 2', { exact: false })).toBeVisible();
  await expect(viewer.locator('mark')).toContainText('当班负责人事前批准');
  await page.getByRole('link', { name: '值班操作手册', exact: true }).click();
  await expect(viewer.locator('mark')).toContainText('错误率');
  await page.screenshot({ path: '../artifacts/verification/trial-answer-desktop.png' });
});

test('starts research on a fresh copy and fixes the trial materials in its brief', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?draft=new');
  const trial = page.getByRole('region', { name: '研发试用' });
  await trial.getByRole('button', { name: '评估能否进入内部试点' }).click();
  await expect(page.getByRole('textbox', { name: '发送给研发助手的消息' })).toHaveValue(/开放研发运维问答助手/);
  await page.screenshot({ path: '../artifacts/verification/trial-start-narrow.png' });
  const started = page.waitForResponse((response) => response.request().method() === 'POST'
    && /\/api\/conversations\/[^/]+\/research-task$/.test(response.url()));
  await page.getByRole('button', { name: '开始研发', exact: true }).click();
  const response = await started;
  expect(response.status()).toBe(201);
  const task = await response.json();
  expect(task.head.state.material_refs.map((material: { title: string }) => material.title).sort()).toEqual([
    '值班操作手册', '服务探针说明', '试点前故障复盘', '试点问答失败记录',
  ].sort());
  await expect(page).toHaveURL(`/chat/${task.home_conversation_id}`);
  await page.getByRole('button', { name: '查看研发简报' }).click();
  await expect(page.getByRole('tabpanel', { name: '研发任务' })).toContainText('比较改进方案');
});
