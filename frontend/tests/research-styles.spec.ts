import { expect, test } from '@playwright/test';

test('component preview makes the brief, feedback and modal behavior inspectable', async ({
  page,
}) => {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/dev/ui');
    await expect(page.getByRole('heading', { name: '界面组件预览' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '研发简报样例' })).toBeVisible();
    await expect(page.getByText('比较内部文档检索方案', { exact: true })).toBeVisible();
    const overflow = await page.evaluate(() =>
      [...document.querySelectorAll('main *')]
        .filter((element) => element.getBoundingClientRect().right > window.innerWidth + 1)
        .map((element) => element.tagName + ' ' + element.className)
        .slice(0, 8),
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
      overflow.join('\n'),
    ).toBeLessThanOrEqual(width);
    const trigger = page.getByRole('button', { name: '查看侧抽屉' });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const drawer = page.getByRole('dialog', { name: '材料预览' });
    await expect(drawer).toBeVisible();
    const close = drawer.getByRole('button', { name: '关闭材料预览' });
    const footer = drawer.getByRole('button', { name: '关闭预览', exact: true });
    await close.focus();
    await page.keyboard.press('Shift+Tab');
    await expect(footer).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await page.screenshot({ path: `../artifacts/verification/research-drawer-${width}.png` });
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: `../artifacts/verification/research-ui-${width}.png`,
      fullPage: true,
    });
  }
});
