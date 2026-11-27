import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';

test.describe('Invoice Maid Smoke Tests', () => {
  test.beforeEach(async ({ request }) => {
    const response = await request.post(`${backendBaseURL}/api/v1/test-helpers/reset-smoke`);
    if (!response.ok()) {
      throw new Error(`Smoke seed failed: ${response.status()} ${await response.text()}`);
    }
  });

  test('loads login screen', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('h2', { hasText: 'Invoice Maid' })).toBeVisible();
    await expect(page.getByLabel('邮箱')).toBeVisible();
    await expect(page.getByLabel('密码', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '登录' })).toBeVisible();
  });

  test('runs a real backend-backed post-login smoke workflow', async ({ page }) => {
    await page.goto('/login');

    await page.getByLabel('邮箱').fill(smokeEmail);
    await page.getByLabel('密码', { exact: true }).fill(smokePassword);
    await page.getByRole('button', { name: '登录' }).click();

    await expect(page).toHaveURL(/\/invoices$/);
    await expect(page.getByRole('link', { name: '票据台账' })).toBeVisible();

    const invoiceRow = page.getByRole('row').filter({ hasText: 'SMOKE-INV-001' });
    await expect(invoiceRow).toBeVisible();
    await expect(invoiceRow.getByText('Smoke Buyer Ltd')).toBeVisible();
    await expect(invoiceRow.getByText('Smoke Seller LLC')).toBeVisible();

    await invoiceRow.getByRole('button', { name: '详情' }).click();
    await expect(page).toHaveURL(/\/invoices\/\d+$/);
    await expect(page.getByText('SMOKE-INV-001')).toBeVisible();
    await expect(page.getByText('Smoke test office supplies')).toBeVisible();

    await page.getByRole('button', { name: '返回台账' }).click();
    await expect(page).toHaveURL(/\/invoices$/);

    await page.getByRole('link', { name: '系统设置' }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole('heading', { name: '邮箱与来源' })).toBeVisible();

    const accountItem = page.getByRole('listitem').filter({ hasText: 'Smoke Mailbox' });
    await expect(accountItem).toBeVisible();
    await expect(accountItem.getByText('smoke@example.com')).toBeVisible();
    const connectionResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/v1/accounts/1/test-connection') && response.request().method() === 'POST'
    );
    await accountItem.getByRole('button', { name: '测试连接' }).click();
    const connectionResponse = await connectionResponsePromise;
    expect(connectionResponse.ok()).toBeTruthy();
    await expect(accountItem).toBeVisible();

    await page.getByRole('button', { name: '扫描记录' }).first().click();
    await expect(page.getByRole('heading', { name: '扫描记录' })).toBeVisible();

    const seededLogRow = page.getByRole('row').filter({ hasText: 'Smoke Mailbox' }).first();
    await expect(seededLogRow).toBeVisible();
    await expect(seededLogRow.getByText('已完成')).toBeVisible();

    const triggerResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/v1/scan/trigger') && response.request().method() === 'POST'
    );
    await page.getByRole('button', { name: '立即扫描' }).click();
    const triggerResponse = await triggerResponsePromise;
    expect(triggerResponse.ok()).toBeTruthy();
    await expect.poll(async () => await page.getByRole('row').filter({ hasText: 'Smoke Mailbox' }).count()).toBeGreaterThan(1);

    await page.getByRole('button', { name: new RegExp(smokeEmail, 'i') }).click();
    await expect(page.getByRole('menuitem', { name: '退出登录' })).toBeVisible();
  });
});
