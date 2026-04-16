import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokePassword = 'smoke-admin-password';

test.describe('Invoice Maid Smoke Tests', () => {
  test.beforeEach(async ({ request }) => {
    const loginResponse = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { password: smokePassword },
    });
    expect(loginResponse.ok()).toBeTruthy();
    const { access_token } = await loginResponse.json();

    const response = await request.post(`${backendBaseURL}/api/v1/test-helpers/reset-smoke`, {
      headers: {
        Authorization: `Bearer ${access_token}`,
      },
    });
    if (!response.ok()) {
      throw new Error(`Smoke seed failed: ${response.status()} ${await response.text()}`);
    }
  });

  test('loads login screen', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('h2', { hasText: 'Invoice Maid' })).toBeVisible();
    await expect(page.getByLabel('Password')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  });

  test('runs a real backend-backed post-login smoke workflow', async ({ page }) => {
    await page.goto('/login');

    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await expect(page).toHaveURL(/\/invoices$/);
    await expect(page.getByRole('link', { name: 'Invoices' })).toBeVisible();

    const invoiceRow = page.getByRole('row').filter({ hasText: 'SMOKE-INV-001' });
    await expect(invoiceRow).toBeVisible();
    await expect(invoiceRow.getByText('Smoke Buyer Ltd')).toBeVisible();
    await expect(invoiceRow.getByText('Smoke Seller LLC')).toBeVisible();

    await invoiceRow.getByRole('button', { name: 'View' }).click();
    await expect(page).toHaveURL(/\/invoices\/\d+$/);
    await expect(page.getByText('SMOKE-INV-001')).toBeVisible();
    await expect(page.getByText('Smoke test office supplies')).toBeVisible();

    await page.getByRole('button', { name: 'Back to Invoices' }).click();
    await expect(page).toHaveURL(/\/invoices$/);

    await page.getByRole('link', { name: 'Settings' }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole('heading', { name: 'Configured Accounts' })).toBeVisible();

    const accountItem = page.getByRole('listitem').filter({ hasText: 'Smoke Mailbox' });
    await expect(accountItem).toBeVisible();
    await expect(accountItem.getByText('smoke@example.com')).toBeVisible();
    const connectionResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/v1/accounts/1/test-connection') && response.request().method() === 'POST'
    );
    await accountItem.getByRole('button', { name: 'Test Connection' }).click();
    const connectionResponse = await connectionResponsePromise;
    expect(connectionResponse.ok()).toBeTruthy();
    await expect(accountItem).toBeVisible();

    await page.getByRole('button', { name: 'Scan Operations' }).click();
    await expect(page.getByRole('heading', { name: 'Recent Scans' })).toBeVisible();

    const seededLogRow = page.getByRole('row').filter({ hasText: 'Smoke Mailbox' }).first();
    await expect(seededLogRow).toBeVisible();
    await expect(seededLogRow.getByText('Success')).toBeVisible();

    const triggerResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/v1/scan/trigger') && response.request().method() === 'POST'
    );
    await page.getByRole('button', { name: 'Scan Now' }).click();
    const triggerResponse = await triggerResponsePromise;
    expect(triggerResponse.ok()).toBeTruthy();
    await expect.poll(async () => await page.getByRole('row').filter({ hasText: 'Smoke Mailbox' }).count()).toBeGreaterThan(1);

    await expect(page.getByRole('button', { name: 'Logout' })).toBeVisible();
  });
});
