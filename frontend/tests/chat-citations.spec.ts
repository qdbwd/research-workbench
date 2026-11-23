import { expect, test } from '@playwright/test';

test('opens multiple fixed references while preserving chat on desktop and narrow screens', async ({ page }) => {
  const seed = await page.request.post('/api/__offline/chat-citation-seed');
  expect(seed.ok()).toBeTruthy();
  const { conversation_id: conversation } = await seed.json();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/chat/${conversation}`);
  const input = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await input.fill('提交前我还要补充一段。');
  const first = page.getByRole('link', { name: '紧急变更规程', exact: true });
  const second = page.getByRole('link', { name: /^值班操作手册/ });
  const inline = await first.evaluate((link) => {
    const icon = link.querySelector('svg')!.getBoundingClientRect();
    const text = document.createRange();
    text.selectNode(link.lastChild!);
    const label = text.getBoundingClientRect();
    return icon.right <= label.left && icon.top < label.bottom && icon.bottom > label.top;
  });
  expect(inline).toBe(true);

  await first.click();
  const viewer = page.getByRole('region', { name: '资料查看器' });
  await expect(viewer.locator('mark')).toHaveText('当班负责人必须事前批准。');
  await expect(viewer.getByText('审批结果须登记。', { exact: false })).toBeVisible();
  expect((await viewer.boundingBox())!.x).toBeGreaterThan((await first.boundingBox())!.x);
  await second.click();
  await expect(viewer.locator('mark')).toHaveText('核对错误率和业务探针。');
  await expect(viewer.getByRole('tab')).toHaveCount(2);
  await page.keyboard.press('ArrowLeft');
  await expect(viewer.getByRole('tab', { name: '紧急变更规程' })).toBeFocused();
  await expect(viewer.locator('mark')).toHaveText('当班负责人必须事前批准。');
  await expect(input).toHaveValue('提交前我还要补充一段。');
  await page.screenshot({ path: '../artifacts/verification/chat-citations-desktop.png' });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(viewer.getByRole('button', { name: '返回会话' })).toBeVisible();
  const narrow = (await viewer.boundingBox())!;
  expect(narrow.x + narrow.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '../artifacts/verification/chat-citations-narrow.png' });
  await viewer.getByRole('button', { name: '返回会话' }).click();
  await expect(viewer).toHaveCount(0);
  await expect(first).toBeFocused();
  await expect(input).toHaveValue('提交前我还要补充一段。');
  await expect(page).toHaveURL(new RegExp(`/chat/${conversation}$`));
  await first.press('Enter');
  await expect(viewer.getByRole('tab')).toHaveCount(2);
  await viewer.getByRole('button', { name: /^关闭 值班操作手册/ }).click();
  await expect(viewer.getByRole('tab')).toHaveCount(1);
  await viewer.getByRole('button', { name: '关闭 紧急变更规程' }).click();
  await expect(first).toBeFocused();
});
