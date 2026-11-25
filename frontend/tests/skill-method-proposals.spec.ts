import { expect, test } from '@playwright/test';

test('reviews a method from the completed operations case before publishing it', async ({ page }) => {
  await page.goto('/skills');
  const skill = page.getByRole('article', { name: '证据核对' });
  await expect(skill).toContainText('修订 1');
  await page.getByRole('button', { name: '方法改进提案' }).click();
  const dialog = page.getByRole('dialog', { name: '方法改进提案' });
  await dialog.getByRole('button', { name: '从已验收案例准备提案' }).click();
  await expect(dialog.getByText('定位检索问答的缺证与误引', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('heading', { name: 'SKILL.md 变更' })).toBeVisible();
  await expect(dialog.getByText('暂缓开放试点。', { exact: false })).toBeVisible();
  await expect(skill).toContainText('修订 1');
  await page.screenshot({ path: '../artifacts/verification/skill-method-proposals-desktop.png' });
  await dialog.getByRole('button', { name: '查看已完成案例' }).click();
  await expect(page.getByRole('dialog', { name: '查看已完成案例' }).getByRole('button', { name: '查看固定回执' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(dialog.getByRole('button', { name: '确认发布修订' })).toBeInViewport();
  await page.screenshot({ path: '../artifacts/verification/skill-method-proposals-narrow.png' });
  await dialog.getByRole('button', { name: '确认发布修订' }).click();
  await expect(dialog.getByText('已发布修订 2', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(skill).toContainText('修订 2');
  await skill.getByRole('button', { name: '查看详情' }).click();
  await expect(page.getByRole('dialog')).toContainText('定位检索问答的缺证与误引');
});
