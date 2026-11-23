import { test, expect } from '@playwright/test';

test('wide chat is centred in the space beside the sidebar; mobile retains navigation', async ({
  page,
}) => {
  for (const width of [1440, 2048, 2560]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '今天想研究什么？' })).toBeVisible();
    await expect(page.getByRole('button', { name: '梳理已有知识' })).toHaveCount(0);
    const sidebar = (await page.locator('.sidebar').boundingBox())!;
    const composer = (await page.locator('.composer').boundingBox())!;
    expect(Math.abs(composer.x + composer.width / 2 - (width + sidebar.width) / 2)).toBeLessThan(2);
    expect(1000 - composer.y - composer.height).toBeLessThan(22);
    await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  }
  await page.screenshot({ path: '../artifacts/verification/layout-wide.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.new-chat')).toBeVisible();
  await expect(page.locator('.sidebar-bottom')).toHaveCount(0);
  await expect(page.locator('.composer-model')).toBeVisible();
  await page.getByRole('button', { name: '最近对话', exact: true }).click();
  await expect(page.locator('.mobile-conversations').getByRole('link').first()).toBeVisible();
  await page.getByRole('button', { name: '关闭最近对话' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '../artifacts/verification/layout-mobile.png', fullPage: true });
});

test('skills are discoverable from the capabilities page without model calls', async ({ page }) => {
  await page.goto('/tools');
  await page.getByRole('button', { name: '管理全部 Skills' }).click();
  await expect(
    page.getByRole('checkbox', { name: '代码文档', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('checkbox', { name: '论文审阅', exact: true }),
  ).toBeVisible();
});

test('the composer prepares an editable knowledge request and attaches materials only after confirmation', async ({ page, request }, info) => {
  await request.post('/api/sources/text', { data: { title: '知识整理界面验收材料', content: '验证选择行为。' } });
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.goto('/');
  const composer = page.getByRole('textbox', { name: '发送给研发助手的消息' });
  await composer.fill('只整理约束条件。');
  const plus = page.getByRole('button', { name: '更多设置与材料', exact: true });
  await plus.click();
  await page.getByRole('button', { name: '整理成知识' }).click();
  await expect(composer).toHaveValue('请将相关讨论或材料整理成知识库文章。\n\n只整理约束条件。');
  await expect(composer).toBeFocused();
  await plus.click();
  await page.getByRole('button', { name: '文件和文件夹' }).click();
  let picker = page.getByRole('dialog', { name: '文件和文件夹' });
  await picker.getByRole('searchbox', { name: '搜索已有材料' }).fill('界面验收');
  await picker.getByRole('checkbox', { name: '知识整理界面验收材料' }).check();
  await picker.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByRole('button', { name: '1 份材料' })).toHaveCount(0);
  await plus.click();
  await page.getByRole('button', { name: '文件和文件夹' }).click();
  picker = page.getByRole('dialog', { name: '文件和文件夹' });
  await picker.getByRole('checkbox', { name: '知识整理界面验收材料' }).check();
  await picker.getByRole('button', { name: '确定（1）' }).click();
  await expect(page.getByRole('button', { name: '1 份材料' })).toBeVisible();
  await expect(page).toHaveURL(/\/\?draft=/);
  await page.screenshot({ path: info.outputPath('knowledge-composer-desktop.png') });
});
