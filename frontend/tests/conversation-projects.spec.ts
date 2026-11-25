import { expect, test } from '@playwright/test';

test('groups chats without moving their content or draft', async ({ page, request }, info) => {
  await page.goto('/');
  const sidebar = page.getByRole('region', { name: '最近对话' });
  await sidebar.getByRole('button', { name: '新建项目' }).click();
  const creation = page.getByRole('dialog', { name: '新建项目' });
  const project = '运维试点与历史规程回溯'.repeat(7);
  await creation.getByRole('textbox', { name: '项目名称' }).fill(project);
  await creation.getByRole('button', { name: '创建项目' }).click();
  await expect(sidebar.getByRole('button', { name: project, exact: true })).toBeVisible();
  await sidebar.getByRole('button', { name: '新建项目' }).click();
  await creation.getByRole('textbox', { name: '项目名称' }).fill('值班复盘');
  await creation.getByRole('button', { name: '创建项目' }).click();

  await sidebar.getByRole('button', { name: '项目“值班复盘”的操作' }).click();
  await page.getByRole('link', { name: '在项目中新建会话' }).click();
  const draft = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await draft.fill('检查回滚规则');
  await expect(sidebar.getByRole('link', { name: '检查回滚规则' })).toHaveCount(0);
  await expect(page.getByText('草稿已保存到本浏览器')).toBeVisible();
  await page.reload();
  await expect(draft).toHaveValue('检查回滚规则');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page).toHaveURL(/\/chat\//);
  const url = new URL(page.url());
  await sidebar.getByRole('button', { name: '值班复盘', exact: true }).click();
  const chats = sidebar.getByRole('link', { name: '检查回滚规则' });
  await expect(chats).toBeVisible();
  const originalMessages = await (await request.get('/api/conversations/' + url.pathname.split('/').pop() + '/messages')).json();

  await draft.fill('尚未发送的查看笔记');
  await chats.focus();
  await chats.press('Shift+F10');
  await page.getByRole('menuitem', { name: '移动到项目' }).click();
  const move = page.getByRole('dialog', { name: '移动会话' });
  await move.getByLabel('目标项目').selectOption({ label: project });
  await move.getByRole('button', { name: '移动', exact: true }).click();
  await expect(page.getByText('已移动会话')).toBeVisible();
  await expect(page).toHaveURL(url.pathname);
  await expect(draft).toHaveValue('尚未发送的查看笔记');
  await sidebar.getByRole('button', { name: project, exact: true }).click();
  await expect(chats).toBeVisible();
  await page.screenshot({ path: info.outputPath('project-desktop.png') });

  await chats.dragTo(sidebar.getByRole('button', { name: '值班复盘', exact: true }));
  await expect(sidebar.getByRole('region', { name: '值班复盘内的会话' }).getByRole('link', { name: '检查回滚规则' })).toBeVisible();

  await sidebar.getByRole('button', { name: '项目“值班复盘”的操作' }).click();
  const management = page.getByRole('dialog', { name: '管理项目' });
  await management.getByRole('button', { name: '移除项目', exact: true }).click();
  await page.getByRole('dialog', { name: '移除项目' }).getByRole('button', { name: '确认移除' }).click();
  await expect(sidebar.getByRole('region', { name: '未分组' }).getByRole('link', { name: '检查回滚规则' })).toBeVisible();
  expect(await (await request.get('/api/conversations/' + url.pathname.split('/').pop() + '/messages')).json()).toEqual(originalMessages);
  await expect(draft).toHaveValue('尚未发送的查看笔记');

  await page.setViewportSize({ width: 375, height: 770 });
  await page.getByRole('button', { name: '最近对话', exact: true }).click();
  const mobile = page.getByRole('dialog', { name: '最近对话' });
  await expect(mobile.getByRole('button', { name: project, exact: true })).toBeVisible();
  await expect(mobile.getByRole('link', { name: '检查回滚规则' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('project-narrow.png') });
});

