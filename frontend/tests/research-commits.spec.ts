import { expect, test } from '@playwright/test';

test('reviews a fixed trial commit and forks it without moving the conversation', async ({ page }) => {
  const chat = await (await page.request.post('/api/conversations', {
    data: { trial_case: 'operations-pilot-v1' },
  })).json();
  const started = await page.request.post(`/api/conversations/${chat.id}/research-task`, {
    data: { operation_id: 'compare-trial', goal: '核对运维问答助手的内部试点材料' },
  });
  expect(started.status()).toBe(201);
  const task = await started.json();
  const mapPath = `/api/research-tasks/${task.id}/map`;
  const added = await page.request.post(mapPath, { data: {
    operation_id: 'commit-trial-node',
    expected_revision_id: task.head.id,
    expected_control_version: task.control_version,
    reason: '建立试点的证据检查项',
    operation: { action: 'save', node: {
      question: '先核对哪个版本的手册？',
      criteria: '对照现行手册的适用时间',
      conclusion: '以现行值班操作手册为依据。',
    } },
  } });
  expect(added.ok()).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/chat/${chat.id}`);
  const composer = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await composer.fill('稍后核对故障复盘');
  await page.getByRole('button', { name: '查看研发简报' }).click();
  await page.getByRole('button', { name: '分支与提交' }).click();
  await expect(page.getByText('此分支还没有正式提交。')).toBeVisible();
  await page.getByRole('button', { name: '预览提交' }).click();
  const preview = page.getByRole('dialog', { name: '预览研发提交' });
  await expect(preview.getByText('值班操作手册', { exact: true })).toBeVisible();
  await preview.getByRole('textbox', { name: '提交说明' }).fill('固定试点基线');
  const current = await (await page.request.get(`/api/research-tasks/${task.id}`)).json();
  const map = await (await page.request.get(mapPath)).json();
  const changed = await page.request.post(mapPath, { data: {
    operation_id: 'commit-trial-child',
    expected_revision_id: current.head.id,
    expected_control_version: current.control_version,
    reason: '预览期间新增适用范围的检查',
    operation: { action: 'save', node: {
      parent_id: map.nodes[0].id, question: '跨班组交接是否适用？', criteria: '检查复盘涉及的班组',
    } },
  } });
  expect(changed.ok()).toBeTruthy();
  await preview.getByRole('button', { name: '确认提交' }).click();
  await expect(preview.getByRole('button', { name: '确认提交' })).toBeDisabled();
  await preview.getByRole('button', { name: '重新预览' }).click();
  await expect(preview.getByRole('textbox', { name: '提交说明' })).toHaveValue('固定试点基线');
  await expect(preview.getByRole('button', { name: '跨班组交接是否适用？' })).toBeVisible();
  await preview.getByRole('button', { name: '确认提交' }).click();

  await expect(page.getByRole('heading', { name: '固定试点基线', exact: true })).toBeVisible();
  await expect(page.getByText('提交快照 · 只读')).toBeVisible();
  await expect(page.getByRole('link', { name: '值班操作手册', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '先核对哪个版本的手册？' }).press('Enter');
  await expect(page.getByText('以现行值班操作手册为依据。')).toBeVisible();
  await page.screenshot({ path: '../artifacts/verification/research-formal-commit-desktop.png', fullPage: true });
  await page.getByRole('button', { name: '从此提交创建分支' }).click();
  const fork = page.getByRole('dialog', { name: '从提交创建分支' });
  await fork.getByRole('textbox', { name: '研发分支名称' }).fill('分支：复核检索排序');
  await fork.getByRole('button', { name: '创建分支', exact: true }).click();
  await expect(page.getByLabel('查看分支')).toHaveValue(/.+/);
  await expect(page.getByLabel('查看分支').locator('option:checked')).toHaveText('分支：复核检索排序');
  await expect(page.getByText('当前会话写入 main')).toBeVisible();
  const branches = await (await page.request.get(`/api/research-tasks/${task.id}/branches`)).json();
  expect(branches.items).toHaveLength(2);
  await page.getByRole('button', { name: '预览提交' }).click();
  await preview.getByRole('textbox', { name: '提交说明' }).fill('继承试点基线');
  await preview.getByRole('button', { name: '确认提交' }).click();
  await expect(page.getByRole('heading', { name: '继承试点基线' })).toBeVisible();
  await page.getByRole('button', { name: '跨分支对比' }).click();
  await expect(page.getByText(`共同起点 · ${branches.items[0].latest_commit_id}`)).toBeVisible();
  await expect(page.getByText('没有差异。')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: '../artifacts/verification/research-formal-comparison-narrow.png', fullPage: true });
  await page.getByRole('button', { name: '将后续消息写入此分支', exact: true }).click();
  const binding = page.getByRole('dialog', { name: '切换后续消息的写入分支' });
  await expect(binding.getByText('分支：复核检索排序', { exact: true })).toBeVisible();
  await binding.getByRole('button', { name: '确认切换', exact: true }).click();
  await expect(page.getByText('当前会话写入 分支：复核检索排序')).toBeVisible();
  await page.getByLabel('查看分支').selectOption({ label: 'main' });
  await expect(page.getByText('当前会话写入 分支：复核检索排序')).toBeVisible();
  await page.getByRole('tab', { name: '会话' }).click();
  await expect(composer).toHaveValue('稍后核对故障复盘');
  await expect(page.getByRole('region', { name: '当前研发任务' })).toContainText('写入 分支：复核检索排序');
  await expect(page).toHaveURL(new RegExp(`/chat/${chat.id}$`));
  await page.goto(`/research/${task.id}`);
  await page.getByRole('button', { name: '分支与提交' }).click();
  await expect(page.getByLabel('查看分支').locator('option:checked')).toHaveText('分支：复核检索排序');
  await page.getByLabel('查看分支').selectOption({ label: 'main' });
  await page.getByRole('button', { name: /固定试点基线/ }).click();
  await expect(page.getByRole('heading', { name: '固定试点基线', exact: true })).toBeVisible();
});

test('a cancelled trial can start a new round and archive the original branch', async ({ page }) => {
  const chat = await (await page.request.post('/api/conversations', {
    data: { trial_case: 'operations-pilot-v1' },
  })).json();
  const task = await (await page.request.post(`/api/conversations/${chat.id}/research-task`, {
    data: { operation_id: 'begin', goal: '评估运维问答助手能否进入试点' },
  })).json();
  const base = `/api/research-tasks/${task.id}`;
  const main = `${base}/branches/${task.main_branch_id}`;
  const preview = await (await page.request.get(main + '/commit-preview')).json();
  const fixed = await page.request.post(main + '/commits', {
    data: { operation_id: 'fixed', preview_hash: preview.preview_hash, message: '冻结试点资料' },
  });
  expect(fixed.status()).toBe(201);
  await page.goto(`/chat/${chat.id}`);
  const composer = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await composer.fill('重新核对交接范围。');
  await page.getByRole('button', { name: '取消任务', exact: true }).click();
  await page.getByRole('button', { name: '确认取消任务', exact: true }).click();
  await expect(page.getByRole('region', { name: '当前研发任务' })).toContainText('已取消');
  await page.getByRole('button', { name: '查看研发简报' }).click();
  await page.getByRole('button', { name: '分支与提交' }).click();
  await page.getByRole('button', { name: /冻结试点资料/ }).click();
  await page.getByRole('button', { name: '从此提交创建分支' }).click();
  const fork = page.getByRole('dialog', { name: '从提交创建分支' });
  await fork.getByRole('textbox', { name: '研发分支名称' }).fill('复核适用范围');
  await fork.getByRole('button', { name: '创建分支', exact: true }).click();
  await page.getByRole('button', { name: '在此分支开始新一轮' }).click();
  await page.getByRole('dialog', { name: '从固定提交开始新一轮' }).getByRole('button', { name: '确认开始新一轮' }).click();
  await expect(page.getByText('当前会话写入 复核适用范围')).toBeVisible();
  await page.getByLabel('查看分支').selectOption({ label: 'main' });
  await page.getByRole('button', { name: '归档此分支' }).click();
  await page.getByRole('dialog', { name: '归档研发分支' }).getByRole('button', { name: '确认归档' }).click();
  await expect(page.getByLabel('查看分支').locator('option:checked')).toHaveText('main（已归档）');
  await expect(page.getByRole('button', { name: '预览提交' })).toBeDisabled();
  await page.getByRole('button', { name: /冻结试点资料/ }).click();
  await expect(page.getByRole('link', { name: '值班操作手册', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '会话', exact: true }).click();
  await expect(composer).toHaveValue('重新核对交接范围。');
});

test('adopts one node from a fixed comparison into another branch', async ({ page }) => {
  const chat = await (await page.request.post('/api/conversations', {
    data: { trial_case: 'operations-pilot-v1' },
  })).json();
  const task = await (await page.request.post(`/api/conversations/${chat.id}/research-task`, {
    data: { operation_id: 'begin', goal: '评估内部试点的检索失败' },
  })).json();
  const base = `/api/research-tasks/${task.id}`;
  const main = `${base}/branches/${task.main_branch_id}`;
  async function commit(path: string, message: string) {
    const preview = await (await page.request.get(path + '/commit-preview')).json();
    const saved = await page.request.post(path + '/commits', {
      data: { operation_id: message, preview_hash: preview.preview_hash, message },
    });
    expect(saved.status()).toBe(201);
    return saved.json();
  }
  const initial = await commit(main, '固定问题范围');
  const target = await (await page.request.post(base + '/branches', {
    data: { operation_id: 'fork', name: '对照路线', source_commit_id: initial.id },
  })).json();
  const targetPath = `${base}/branches/${target.id}`;
  await commit(targetPath, '固定对照范围');
  const saved = await page.request.post(base + '/map', { data: {
    operation_id: 'record-finding', expected_control_version: task.control_version,
    expected_revision_id: task.head.id, reason: '记录当前路线的排查结论',
    operation: { action: 'save', node: {
      question: '现行手册从哪里丢失？', criteria: '核对最终上下文',
      conclusion: '手册在组装上下文时被截断。',
    } },
  } });
  expect(saved.status()).toBe(200);
  await commit(main, '固定截断结论');
  await page.goto(`/chat/${chat.id}`);
  await page.getByRole('button', { name: '查看研发简报' }).click();
  await page.getByRole('button', { name: '分支与提交' }).click();
  await page.getByRole('button', { name: '跨分支对比' }).click();
  await page.getByRole('button', { name: '采纳基线节点', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '采纳固定提交的节点' });
  await dialog.getByLabel('目标分支').selectOption({ label: '对照路线' });
  await dialog.getByLabel('目标上级主题').selectOption({ label: '作为顶层主题' });
  await dialog.getByRole('button', { name: '生成采纳预览' }).click();
  await expect(dialog.getByText('手册在组装上下文时被截断。', { exact: true })).toBeVisible();
  await expect(dialog.getByText('目标地图尚无此节点。')).toBeVisible();
  await dialog.getByLabel('采纳理由').fill('保留这条排查线继续验证');
  const bumped = await page.request.post(targetPath + '/revisions', { data: {
    operation_id: 'bump-while-previewing', expected_control_version: task.control_version,
    expected_revision_id: target.head.id, changes: { brief: { next_action: '先核对交接范围' } }, reason: '更新目标进展',
  } });
  expect(bumped.status()).toBe(201);
  await dialog.getByRole('button', { name: '确认采纳', exact: true }).click();
  await dialog.getByRole('button', { name: '刷新目标进展', exact: true }).click();
  await dialog.getByRole('button', { name: '生成采纳预览' }).click();
  await expect(dialog.getByLabel('采纳理由')).toHaveValue('保留这条排查线继续验证');
  await page.screenshot({ path: '../artifacts/verification/research-cross-adoption-desktop.png' });
  const desktop = page.viewportSize()!;
  await page.setViewportSize({ width: 390, height: 844 });
  const bounds = (await dialog.boundingBox())!;
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '../artifacts/verification/research-cross-adoption-narrow.png' });
  await page.setViewportSize(desktop);
  await dialog.getByRole('button', { name: '确认采纳', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText('已写入「对照路线」的当前进展。')).toBeVisible();
  await expect(page.getByText('当前会话写入 main')).toBeVisible();
  await page.getByLabel('查看分支').selectOption({ label: '对照路线' });
  await expect(page.getByText('当前进展与提交基线')).toBeVisible();
  await expect(page.getByText('待复核 · 新增', { exact: true })).toBeVisible();
});
