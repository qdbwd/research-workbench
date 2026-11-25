import { expect, test } from '@playwright/test';

test('autonomous research starts only after confirming the brief and cumulative limits', async ({ page }) => {
  const { id } = await (await page.request.post('/api/conversations', {
    data: { title: '自主研发授权验收' },
  })).json();
  const task = await (await page.request.post(`/api/conversations/${id}/research-task`, {
    data: { operation_id: 'autonomy-ui', goal: '核对内部问答证据缺失位置' },
  })).json();
  const route = `/api/research-tasks/${task.id}/autonomy`;
  expect(await (await page.request.get(route)).json()).toBeNull();
  await page.goto(`/chat/${id}`);
  await page.getByRole('button', { name: '自主推进', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '自主推进' });
  await expect(dialog.getByText('核对内部问答证据缺失位置')).toBeVisible();
  await dialog.getByText(/查看或调整安全上限/).click();
  await dialog.getByRole('spinbutton', { name: '模型请求' }).fill('3');
  await dialog.getByRole('spinbutton', { name: '活跃执行秒数' }).fill('60');
  expect(await (await page.request.get(route)).json()).toBeNull();
  const accepted = page.waitForResponse(response => response.request().method() === 'POST'
    && response.url().endsWith(route));
  await dialog.getByRole('button', { name: '确认范围和额度，开始执行' }).click();
  const result = await accepted;
  expect(result.ok()).toBeTruthy();
  const permit = (await result.json()).permit;
  expect(permit.budgets).toMatchObject({ model_requests: 3, active_seconds: 60 });
  await expect(dialog.getByRole('link', { name: '查看最近一次运行' })).toHaveAttribute('href', `/runs/${permit.run_id}`);
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await expect(page.getByRole('region', { name: '当前研发任务' })).toContainText('已暂停');
  expect((await (await page.request.get(route)).json()).status).toBe('paused');
});

test('fixed research delivery stays visible after requesting a revision', async ({ page }) => {
  const { id } = await (await page.request.post('/api/conversations', {
    data: { title: '研发验收' },
  })).json();
  const task = await (await page.request.post(`/api/conversations/${id}/research-task`, {
    data: { operation_id: 'delivery-ui', goal: '诊断内部文档回答错误' },
  })).json();
  const run = await (await page.request.post(`/api/conversations/${id}/messages`, {
    data: { content: '请交付诊断报告', request_key: 'delivery-ui-run' },
  })).json();
  const execution = await page.request.post(`/api/__offline/research-delivery/${run.id}`);
  expect((await execution.json()).outcome?.kind).toBe('research_delivery');
  await page.goto(`/chat/${id}`);
  await page.getByRole('link', { name: '查看研发成果' }).click();
  const dialog = page.getByRole('dialog', { name: '研发成果' });
  await expect(dialog.getByText('待验收', { exact: true })).toBeVisible();
  await expect(dialog.getByText('复核候选与最终上下文，再决定是否重排。')).toBeVisible();
  await dialog.getByRole('button', { name: '要求修订', exact: true }).click();
  await dialog.getByRole('textbox', { name: '修订要求' }).fill('补充对生成误用情形的判断');
  await dialog.getByRole('button', { name: '确认要求修订' }).click();
  await expect(dialog.getByText('已要求修订', { exact: true })).toBeVisible();
  await expect(dialog.getByText('正确材料可能在截断时被移除。')).toBeVisible();
  expect(await (await page.request.get(`/api/research-tasks/${task.id}/autonomy`)).json()).toBeNull();
});

test('research map compares fixed versions and adopts one conclusion into a new head', async ({ page }) => {
  const { id } = await (await page.request.post('/api/conversations', {
    data: { title: '地图和版本验收' },
  })).json();
  const task = await (await page.request.post(`/api/conversations/${id}/research-task`, {
    data: { operation_id: 'map-ui', goal: '查清正确候选丢在哪一环' },
  })).json();
  const root = `/api/research-tasks/${task.id}/map`;
  await page.goto(`/research/${task.id}`);
  await page.getByRole('link', { name: '研究地图', exact: true }).click();
  await page.getByRole('button', { name: '新增主题', exact: true }).click();
  const add = page.getByRole('dialog', { name: '新增研究主题' });
  await add.getByLabel('具体问题').fill('正确片段是否被截断？');
  await add.getByLabel('解决标准').fill('逐条核对最终上下文');
  await add.getByLabel('结论', { exact: true }).fill('初步假设是截断。');
  await add.getByLabel('变更理由').fill('记录首个待核对假设');
  await add.getByRole('button', { name: '保存主题' }).click();
  await expect(page.getByRole('heading', { name: '正确片段是否被截断？' })).toBeVisible();
  await page.getByRole('button', { name: '新增主题', exact: true }).click();
  await add.getByLabel('具体问题').fill('核对原始试点材料');
  await add.getByLabel('解决标准').fill('检查手册适用范围');
  await add.getByLabel('变更理由').fill('增加并行的资料核对');
  await add.getByRole('button', { name: '保存主题' }).click();
  await expect(add).toHaveCount(0);
  const canvas = page.getByRole('region', { name: '研发地图', exact: true });
  await expect(canvas.getByText('查清正确候选丢在哪一环', { exact: true })).toBeVisible();
  await canvas.getByRole('button', { name: '正确片段是否被截断？', exact: true }).press('Enter');
  const original = await (await page.request.get(root)).json();
  const edit = page.getByRole('form', { name: '编辑研究主题' });
  await expect(edit).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await edit.getByRole('textbox', { name: '结论', exact: true }).fill('也可能是模型误用。');
  await edit.getByLabel('变更理由').fill('故障有两种可能');
  await canvas.getByRole('button', { name: '核对原始试点材料' }).click();
  await expect(edit.getByLabel('具体问题')).toHaveValue('核对原始试点材料');
  await canvas.getByRole('button', { name: '正确片段是否被截断？', exact: true }).click();
  await expect(edit.getByRole('textbox', { name: '结论', exact: true })).toHaveValue('也可能是模型误用。');
  await edit.getByRole('button', { name: '保存主题' }).click();
  await expect.poll(async () => (await (await page.request.get(root)).json()).revision_id).not.toBe(original.revision_id);
  const changed = await (await page.request.get(root)).json();
  const focusId = original.nodes.find((node: { question: string }) => node.question === '正确片段是否被截断？').id;
  expect(changed.nodes.find((node: { id: string }) => node.id === focusId).conclusion).toBe('也可能是模型误用。');
  await expect(edit.getByRole('textbox', { name: '结论', exact: true })).toHaveValue('也可能是模型误用。');
  await page.screenshot({ path: '../artifacts/verification/research-map-inline-desktop.png', fullPage: true });
  const desktop = page.viewportSize()!;
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  const reasonBox = (await edit.getByLabel('变更理由').boundingBox())!;
  const saveBox = (await edit.getByRole('button', { name: '保存主题' }).boundingBox())!;
  expect(saveBox.y).toBeGreaterThanOrEqual(reasonBox.y + reasonBox.height);
  await page.screenshot({ path: '../artifacts/verification/research-map-inline-narrow.png', fullPage: true });
  await page.setViewportSize(desktop);
  await page.getByRole('button', { name: '版本对比', exact: true }).click();
  await expect(page.getByLabel('起始版本')).toHaveValue(original.revision_id);
  await expect(page.getByLabel('对照版本')).toHaveValue(changed.revision_id);
  await expect(page.getByRole('form', { name: '编辑研究主题' })).toHaveCount(0);
  await page.getByRole('button', { name: '预览采纳历史结论' }).click();
  const adoption = page.getByRole('dialog', { name: '采纳历史结论' });
  await expect(adoption.getByText('初步假设是截断。', { exact: true })).toBeVisible();
  await adoption.getByLabel('采纳理由').fill('保留原始假设继续验证');
  await adoption.getByRole('button', { name: '确认采纳' }).click();
  await expect(adoption).toHaveCount(0);
  await expect(page.getByLabel('对照版本')).toHaveValue(changed.revision_id);
  const latest = await (await page.request.get(root)).json();
  expect(latest.revision_id).not.toEqual(changed.revision_id);
  expect(latest.nodes.find((node: { id: string }) => node.id === focusId))
    .toMatchObject({ conclusion: '初步假设是截断。', status: 'needs_review' });
});

test('research focus can pin product design and preserves its fixed revision', async ({ page }) => {
  const { id } = await (await page.request.post('/api/conversations', {
    data: { title: '产品设计验收' },
  })).json();
  const created = await page.request.post('/api/research-tasks', { data: {
    operation_id: 'focus-ui', conversation_id: id, goal: '厘清 PRD 整理的真正问题',
  } });
  expect(created.status()).toBe(201);
  const task = await created.json();
  await page.goto(`/research/${task.id}`);
  await page.getByRole('button', { name: '调整研究焦点' }).click();
  const dialog = page.getByRole('dialog', { name: '调整研究焦点' });
  await dialog.getByLabel('研究阶段').selectOption('investigation');
  await dialog.getByRole('checkbox', { name: '产品设计' }).check();
  await dialog.getByRole('checkbox', { name: '固定选择，由我调整方法' }).check();
  await dialog.getByLabel('调整理由').fill('先验证讨论接续是否比自动生成文档重要');
  await dialog.getByRole('button', { name: '保存研究焦点' }).click();
  await expect(page.getByText('调研阶段', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '调整研究焦点' }).click();
  await expect(dialog.getByRole('checkbox', { name: '产品设计' })).toBeChecked();
  await expect(dialog.getByRole('checkbox', { name: '固定选择，由我调整方法' })).toBeChecked();
});

test('deleting research with a lost response retries in place and leaves the conversation', async ({ page }) => {
  const { id } = await (await page.request.post('/api/conversations', {
    data: { title: '删除研发验收' },
  })).json();
  const task = await (await page.request.post(`/api/conversations/${id}/research-task`, {
    data: { operation_id: 'delete-browser-task', goal: '保留启动目标的聊天原话' },
  })).json();
  await page.goto('/research');
  await page.getByRole('button', { name: '删除研发任务：保留启动目标的聊天原话' }).click();
  const dialog = page.getByRole('dialog', { name: '删除研发任务？' });
  let first: string;
  await page.route(`**/api/research-tasks/${task.id}/delete`, async (route) => {
    first = route.request().postDataJSON().operation_id;
    expect((await route.fetch()).status()).toBe(200);
    await route.abort('failed');
  }, { times: 1 });
  await dialog.getByRole('button', { name: '删除任务' }).click();
  const retry = page.waitForResponse((response) => response.url().endsWith(`/${task.id}/delete`));
  await dialog.getByRole('button', { name: '重试删除' }).click();
  const response = await retry;
  expect(response.request().postDataJSON().operation_id).toEqual(first!);
  await expect(page.getByRole('link', { name: /保留启动目标的聊天原话/ })).toHaveCount(0);
  await page.goto(`/chat/${id}`);
  await expect(page.getByText('保留启动目标的聊天原话', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '查看研发简报' })).toHaveCount(0);
});

test('existing chat opens a persistent research brief, history and conflict recovery', async ({
  page,
  context,
}) => {
  const created = await page.request.post('/api/conversations', { data: { title: '旧会话验收' } });
  expect(created.status()).toBe(201);
  const { id: conversationId } = await created.json();
  const chatUrl = `/chat/${conversationId}`;
  const queued = await page.request.post(`/api/conversations/${conversationId}/messages`, {
    data: { content: '这条旧聊天应继续显示', request_key: 'research-old-message' },
  });
  expect(queued.status()).toBe(202);

  await page.goto(chatUrl);
  await expect(page.getByText('这条旧聊天应继续显示', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '更多设置与材料' }).click();
  await page.getByRole('button', { name: '研发模式', exact: true }).click();
  await page.getByRole('textbox', { name: '发送给研发助手的消息' }).fill('改善内部文档问答');
  await page.getByRole('button', { name: '开始研发', exact: true }).click();
  await expect(page.getByText('这条旧聊天应继续显示', { exact: true })).toBeVisible();
  const followUp = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await followUp.fill('下一步想核对失败案例。');
  await page.getByRole('button', { name: '查看研发简报', exact: true }).click();
  const preview = page.getByRole('tabpanel', { name: '研发任务' });
  await expect(preview).toContainText('改善内部文档问答');
  await expect(page).toHaveURL(chatUrl);
  await preview.getByRole('button', { name: '研发地图', exact: true }).click();
  await expect(preview.getByRole('heading', { name: '研究地图', exact: true })).toBeVisible();
  await preview.getByRole('button', { name: '分支与提交', exact: true }).click();
  await expect(preview.getByRole('heading', { name: '版本历史', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '会话', exact: true }).click();
  await expect(followUp).toHaveValue('下一步想核对失败案例。');
  await page.getByRole('tab', { name: '研发任务', exact: true }).click();
  await preview.getByRole('button', { name: '简报', exact: true }).click();
  await expect(preview.getByText('探索阶段', { exact: true })).toBeVisible();
  await page.screenshot({ path: '../artifacts/verification/chat-research-tab-desktop.png' });
  await preview.getByRole('link', { name: '打开研发工作台' }).click();
  await expect(page.getByRole('heading', { name: '改善内部文档问答' })).toBeVisible();
  await expect(page.getByText('探索阶段', { exact: true })).toBeVisible();
  const workbench = new URL(page.url()).pathname;

  const competing = await context.newPage();
  await competing.goto(workbench);
  await competing.getByRole('button', { name: '编辑简报' }).click();
  await competing.getByLabel('研究范围').fill('第二个页面的草稿');
  await competing.getByLabel('修订说明').fill('补上范围');

  await page.getByRole('button', { name: '编辑简报' }).click();
  await page.getByLabel('研究范围').fill('仅覆盖工程文档');
  await page.getByLabel('修订说明').fill('先限定资料来源');
  // 丢弃已经提交的第一次响应。用户重试时应使用原操作编号、得到原修订。
  await page.route(
    '**/api/research-tasks/**/revisions',
    async (route) => {
      const upstream = await route.fetch();
      expect(upstream.status()).toBe(201);
      await route.abort('failed');
    },
    { times: 1 },
  );
  await page.getByRole('button', { name: '保存修订' }).click();
  await expect(page.getByRole('button', { name: '重试保存' })).toBeVisible();
  const retried = page.waitForResponse(
    (r) => r.url().endsWith('/revisions') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '重试保存' }).click();
  const response = await retried;
  expect(response.status()).toBe(201);
  const saved = await response.json();
  expect(saved.head.number).toBe(2);
  const repeated = await page.request.post(response.url(), {
    data: response.request().postDataJSON(),
  });
  expect(await repeated.json()).toEqual(saved);

  await competing.getByRole('button', { name: '保存修订' }).click();
  await expect(competing.getByRole('alert')).toContainText('简报已在另一个页面更新');
  await expect(competing.getByLabel('研究范围')).toHaveValue('第二个页面的草稿');
  await competing.getByRole('button', { name: '核对最新版' }).click();
  await expect(competing.getByText('仅覆盖工程文档', { exact: true })).toBeVisible();
  await competing.getByRole('button', { name: '保留草稿并使用最新基线' }).click();
  await expect(competing.getByLabel('研究范围')).toHaveValue('第二个页面的草稿');
  await competing.getByRole('button', { name: '取消编辑' }).click();
  await competing.getByRole('button', { name: '放弃修改' }).click();
  await expect(competing.getByText('仅覆盖工程文档', { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByText('仅覆盖工程文档', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: /版本 1/ }).click();
  await expect(page.getByRole('heading', { name: '固定修订 1' })).toBeVisible();
  await expect(page.getByText('仅覆盖工程文档', { exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: '返回研发简报' }).click();
  await expect(page.getByText('仅覆盖工程文档', { exact: true })).toBeVisible();
  await page.screenshot({
    path: '../artifacts/verification/research-brief-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await expect(page.getByRole('link', { name: '返回会话' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: '../artifacts/verification/research-brief-mobile.png',
    fullPage: true,
  });
});

test('concurrent activation keeps the typed goal and offers the saved brief', async ({ page }) => {
  const created = await page.request.post('/api/conversations', { data: { title: '并发启用' } });
  const { id } = await created.json();
  await page.goto(`/chat/${id}`);
  await page.getByRole('button', { name: '更多设置与材料' }).click();
  await page.getByRole('button', { name: '研发模式', exact: true }).click();
  const input = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await input.fill('当前输入暂时保留');
  const elsewhere = await page.request.post('/api/research-tasks', {
    data: { operation_id: 'another-client', conversation_id: id, goal: '另一个页面的研发目标' },
  });
  expect(elsewhere.status()).toBe(201);
  await page.getByRole('button', { name: '开始研发', exact: true }).click();
  await expect(page.getByRole('region', { name: '待核对的提交' })).toContainText('该会话已启用研发，请打开已有简报。');
  await expect(input).toHaveValue('当前输入暂时保留');
  await page.getByRole('button', { name: '查看研发简报', exact: true }).click();
  await expect(page.getByRole('tabpanel', { name: '研发任务' })).toContainText('另一个页面的研发目标');
});

test('new chat starts research once despite a lost response, then pauses, resumes, and cancels', async ({ page, request }, info) => {
  const goal = '核对接口文档遗漏的约束';
  await page.goto('/?draft=new');
  const input = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  const plus = page.getByRole('button', { name: '更多设置与材料', exact: true });
  await input.fill(goal);
  await plus.click();
  await page.getByRole('button', { name: '研发模式', exact: true }).click();
  await page.getByRole('button', { name: '关闭研发模式' }).click();
  await expect(input).toHaveValue(goal);
  await expect(page.locator('.recent').getByRole('link', { name: goal, exact: true })).toHaveCount(0);
  await plus.click();
  await page.getByRole('button', { name: '研发模式', exact: true }).click();
  let submissions = 0;
  await page.route('**/api/conversations/*/research-task', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    submissions += 1;
    const result = await route.fetch();
    expect(result.status()).toBe(201);
    await route.abort('failed');
  });
  await page.getByRole('button', { name: '开始研发', exact: true }).click();
  await expect(page.getByText('上次提交结果待核对')).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/\/chat\/[a-f0-9-]+$/);
  expect(submissions).toBe(1);
  const conversation = new URL(page.url()).pathname.split('/').at(-1);
  const bar = page.getByRole('region', { name: '当前研发任务' });
  await expect(bar.getByRole('status')).toHaveText('待推进');
  await expect(page.locator('.recent').getByRole('link', { name: goal, exact: true })).toBeVisible();
  await expect(page.locator('.message.user')).toHaveCount(1);
  await bar.getByRole('button', { name: '暂停', exact: true }).click();
  await expect(bar.getByRole('status')).toHaveText('已暂停');
  await page.reload();
  await expect(bar.getByRole('status')).toHaveText('已暂停');
  await bar.getByRole('button', { name: '继续', exact: true }).click();
  await expect(bar.getByRole('status')).toHaveText('待推进');
  await bar.getByRole('button', { name: '取消任务', exact: true }).click();
  await page.getByRole('dialog', { name: '取消研发任务？' }).getByRole('button', { name: '确认取消任务' }).click();
  await expect(bar.getByRole('status')).toHaveText('已取消');
  const task = await (await request.get(`/api/conversations/${conversation}/research-task`)).json();
  expect(task.status).toBe('closed');
  await bar.getByRole('button', { name: '查看研发简报' }).click();
  await expect(page.getByRole('tabpanel', { name: '研发任务' })).toContainText(goal);
  await page.getByRole('button', { name: '关闭研发任务标签' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(bar).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('research-controls-mobile.png') });
});
