import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

test('research diagnoses two failures with a real isolated experiment and submits fixed evidence', async ({ page }) => {
  test.skip(!process.env.KNOWLEDGEOPS_TEST_EXPERIMENT_EXCHANGE, 'requires the independent fixture runner');
  test.setTimeout(55000);
  const data = readFileSync(new URL('../../tests/fixtures/research_retrieval_case.json', import.meta.url), 'utf8');
  const { id } = await (await page.request.post('/api/conversations', {
    data: { title: '问答故障诊断' },
  })).json();
  const imported = await page.request.post('/api/sources/text', {
    data: { title: '固定问答故障样本', content: data, conversation_id: id },
  });
  expect(imported.status()).toBe(201);
  const source = await imported.json();
  const activated = await page.request.post(`/api/conversations/${id}/research-task`, {
    data: { operation_id: 'experiment-start', goal: '内部文档问答经常答错，是否该加 reranker？', source_ids: [source.id] },
  });
  expect(activated.status()).toBe(201);
  const task = await activated.json();
  const base = `/api/research-tasks/${task.id}`;
  const post = async (request_key: string, content: string) => {
    const result = await page.request.post(`/api/conversations/${id}/messages`, { data: { request_key, content } });
    expect(result.status()).toBe(202);
    return await result.json();
  };
  const first = await post('experiment-explore', '请分析固定样本并设计能判断两类原因的检查。');
  const events = `/api/runs/${first.id}/events`;
  await page.goto(`/chat/${id}`);
  const lastEventId = await page.evaluate((path) => new Promise<string>((resolve, reject) => {
    const stream = new EventSource(path);
    stream.addEventListener('progress', (event: MessageEvent<string>) => {
      stream.close();
      resolve(event.lastEventId);
    }, { once: true });
    stream.addEventListener('error', () => {
      stream.close();
      reject(new Error('progress stream disconnected before its first event'));
    }, { once: true });
  }), events);
  expect(Number(lastEventId)).toBeGreaterThan(0);
  const explore = await page.request.post(`/api/__offline/research-experiment/run/${first.id}?source_id=${source.id}`);
  expect(await explore.json()).toMatchObject({ status: 'completed' });
  const replay = await page.request.get(events, { headers: { 'Last-Event-ID': lastEventId } });
  const eventText = await replay.text();
  const subsequent = [...eventText.matchAll(/^id: (\d+)$/gm)].map((match) => Number(match[1]));
  expect(subsequent.length).toBeGreaterThan(0);
  expect(subsequent.every((sequence) => sequence > Number(lastEventId))).toBe(true);
  expect(eventText).toContain('event: closed');
  const [attempt] = (await (await page.request.get(base + '/experiments')).json()).items;
  expect(attempt.status).toBe('awaiting_approval');
  await page.goto(`/research/${task.id}`);
  await page.getByRole('link', { name: '独立实验' }).click();
  await page.getByRole('button', { name: '审批实验', exact: true }).click();
  const approval = page.getByRole('dialog', { name: '审批实验' });
  await expect(approval.getByText(/observed_context/)).toBeVisible();
  await approval.getByRole('button', { name: '确认批准固定输入' }).click();
  await expect(approval).toHaveCount(0);
  await expect.poll(async () => {
    expect((await page.request.post('/api/__offline/research-experiment/tick')).ok()).toBeTruthy();
    return (await (await page.request.get(base + '/experiments/' + attempt.id)).json()).status;
  }, { timeout: 20000 }).toBe('completed');
  await expect(page.getByText(/missing-from-context · 正确片段未进上下文/)).toBeVisible();
  await expect(page.getByText(/visible-misused · 已有证据被误用/)).toBeVisible();
  await page.getByRole('link', { name: '查看实验回执和全部指标' }).click();
  await expect(page.getByRole('heading', { name: '独立实验结果' })).toBeVisible();
  await expect(page.getByText(/输入 SHA256/)).toBeVisible();

  await page.goBack();
  await page.getByRole('button', { name: '准备复现', exact: true }).click();
  await page.getByRole('dialog', { name: '准备复现' }).getByRole('button', { name: '确认操作' }).click();
  await expect(page.getByRole('dialog', { name: '准备复现' })).toHaveCount(0);
  const listAttempts = async () => (await (await page.request.get(base + '/experiments')).json()).items;
  const [reproduced] = await listAttempts();
  expect(reproduced).toMatchObject({
    status: 'awaiting_approval', reproduced_from: attempt.id,
    input_sha256: attempt.input_sha256, script_sha256: attempt.script_sha256, image: attempt.image,
  });
  expect(reproduced.id).not.toBe(attempt.id);
  await page.getByRole('button', { name: '审批实验', exact: true }).click();
  await approval.getByRole('button', { name: '确认批准固定输入' }).click();
  await expect(approval).toHaveCount(0);
  await expect.poll(async () => {
    expect((await page.request.post('/api/__offline/research-experiment/tick')).ok()).toBeTruthy();
    return (await listAttempts()).find((row: { id: string }) => row.id === reproduced.id)?.status;
  }, { timeout: 20000 }).toBe('completed');
  const [reproduction, original] = await listAttempts();
  expect(reproduction.result).toEqual(original.result);

  const final = await post('experiment-interpret', '核对真实实验结果，修订建议并提交成果。');
  const execution = await page.request.post(
    `/api/__offline/research-experiment/run/${final.id}?source_id=${source.id}&attempt=${attempt.id}`,
  );
  expect(await execution.json()).toMatchObject({ status: 'completed', outcome: { kind: 'research_delivery' } });
  await page.goto(`/research/${task.id}/map`);
  await expect(page.getByText('只对候选中已有正确片段但被截断的案例试点重排；已有证据被回答误用时，优先核对生成约束。')).toBeVisible();
  await expect(page.getByRole('link', { name: '固定问答故障样本' })).toBeVisible();
  await expect(page.getByRole('link', { name: attempt.question })).toBeVisible();
  await page.goto(`/chat/${id}`);
  await page.getByRole('link', { name: '查看研发成果' }).click();
  const review = page.getByRole('dialog', { name: '研发成果' });
  await expect(review.getByText('待验收', { exact: true })).toBeVisible();
  await expect(review.getByText(/夹具只改变排序/)).toBeVisible();
  await review.getByRole('button', { name: '通过验收', exact: true }).click();
  await review.getByRole('textbox', { name: '验收理由' }).fill('已分别回查资料、实验回执和修订建议。');
  await review.getByRole('button', { name: '确认通过' }).click();
  await expect(review.getByText('已通过验收', { exact: true })).toBeVisible();
  expect((await (await page.request.get(base)).json()).status).toBe('completed');
});
