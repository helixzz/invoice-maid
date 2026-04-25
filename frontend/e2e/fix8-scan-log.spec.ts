import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';

test.describe('Fix 8 — per-email scan summary aggregation (v1.0.0)', () => {
  test.beforeEach(async ({ request }) => {
    await request.post(`${backendBaseURL}/api/v1/test-helpers/reset-smoke`);

    const loginResponse = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { email: smokeEmail, password: smokePassword },
    });
    expect(loginResponse.ok()).toBeTruthy();
    const { access_token } = await loginResponse.json();

    const seedResponse = await request.post(
      `${backendBaseURL}/api/v1/test-helpers/seed-fix8-scenario`,
      {
        headers: { Authorization: `Bearer ${access_token}` },
      }
    );
    if (!seedResponse.ok()) {
      throw new Error(
        `Fix 8 seed failed: ${seedResponse.status()} ${await seedResponse.text()}`
      );
    }
    const seed = await seedResponse.json();
    expect(seed.extraction_log_ids).toHaveLength(6);
  });

  test('renders 2 email cards (not 6 rows) with Chinese summary banner', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/invoices$/);

    await page.getByRole('link', { name: 'Settings' }).click();
    await expect(page).toHaveURL(/\/settings$/);

    await page.getByRole('button', { name: 'Scan Operations' }).click();
    await expect(page.getByRole('heading', { name: 'Recent Scans' })).toBeVisible();

    const seededLogRow = page.getByRole('row').filter({ hasText: 'Smoke Mailbox' }).first();
    await expect(seededLogRow).toBeVisible();
    const extractionsResponseWait = page.waitForResponse(
      (r) =>
        r.url().includes('/api/v1/scan/logs/') &&
        r.url().includes('/extractions') &&
        r.request().method() === 'GET'
    );
    await seededLogRow.click();
    await extractionsResponseWait;

    await expect(page.getByText('Summary (by email)')).toBeVisible();

    await expect(page.getByText('2 封邮件')).toBeVisible();
    await expect(page.getByText('保存 1 张新发票')).toBeVisible();
    await expect(page.getByText('去重 1 封')).toBeVisible();
  });

  test('duplicate badge has the explicit "correctly deduped" tooltip', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('button', { name: 'Scan Operations' }).click();

    const seededLogRow = page.getByRole('row').filter({ hasText: 'Smoke Mailbox' }).first();
    const extractionsResponseWait = page.waitForResponse(
      (r) =>
        r.url().includes('/api/v1/scan/logs/') &&
        r.url().includes('/extractions') &&
        r.request().method() === 'GET'
    );
    await seededLogRow.click();
    await extractionsResponseWait;
    await expect(page.getByText('Summary (by email)')).toBeVisible();

    const duplicateBadge = page.locator('[title*="correctly deduped"]').first();
    await expect(duplicateBadge).toBeVisible();
    const tooltip = await duplicateBadge.getAttribute('title');
    expect(tooltip).toMatch(/correctly deduped|no action needed/i);
  });

  test('expanded panel shows the saved invoice_no on the saved-email card', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('button', { name: 'Scan Operations' }).click();

    const seededLogRow = page.getByRole('row').filter({ hasText: 'Smoke Mailbox' }).first();
    const extractionsResponseWait = page.waitForResponse(
      (r) =>
        r.url().includes('/api/v1/scan/logs/') &&
        r.url().includes('/extractions') &&
        r.request().method() === 'GET'
    );
    await seededLogRow.click();
    await extractionsResponseWait;
    await expect(page.getByText('Summary (by email)')).toBeVisible();

    await expect(page.getByText('FIX8-SAMS-CLUB-001').first()).toBeVisible();
  });
});
