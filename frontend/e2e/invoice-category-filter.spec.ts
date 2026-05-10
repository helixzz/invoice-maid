import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';

// seed-invoice-category-mix emits exactly one invoice per category
// (5 total); UNIQUE(user_id, invoice_no) blocks re-seeding to multiply.
const SEED_INVOICE_IDS = ['CATMIX-VAT-001', 'CATMIX-OVRS-001', 'CATMIX-RCPT-001', 'CATMIX-PROF-001', 'CATMIX-OTHR-001'];

test.describe('Invoice category filter (v1.2.0 A11)', () => {
  test.beforeEach(async ({ request }) => {
    const reset = await request.post(`${backendBaseURL}/api/v1/test-helpers/reset-smoke`);
    if (!reset.ok()) {
      throw new Error(`reset-smoke failed: ${reset.status()} ${await reset.text()}`);
    }

    const login = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { email: smokeEmail, password: smokePassword },
    });
    expect(login.ok()).toBeTruthy();
    const { access_token } = await login.json();

    const seed = await request.post(
      `${backendBaseURL}/api/v1/test-helpers/seed-invoice-category-mix`,
      { headers: { Authorization: `Bearer ${access_token}` } },
    );
    expect(seed.ok()).toBeTruthy();
    const body = await seed.json();
    expect(body.invoice_ids).toHaveLength(5);
    expect(body.categories).toEqual(['vat_invoice', 'overseas_invoice', 'receipt', 'proforma', 'other']);
  });

  test('renders badges, narrows rows via chips, and shows by_category stats', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/invoices$/);

    // 6 rows total: 5 category-mix seeds + 1 smoke invoice from reset-smoke
    const dataRows = page.locator('tbody tr[data-test-id^="invoice-row-"]');
    await expect(dataRows).toHaveCount(6);

    for (const invoiceNo of SEED_INVOICE_IDS) {
      const row = page.getByRole('row').filter({ hasText: invoiceNo });
      await expect(row).toBeVisible();
      await expect(row.locator('[data-category]')).toBeVisible();
    }

    const statsPanel = page.locator('[data-test-id="stats-by-category"]');
    await expect(statsPanel).toBeVisible();
    for (const category of ['vat_invoice', 'overseas_invoice', 'receipt', 'proforma', 'other']) {
      await expect(statsPanel.locator(`[data-test-id="stats-category-${category}"]`)).toBeVisible();
    }

    const filter = page.locator('[data-test-id="category-filter"]');
    await expect(filter).toBeVisible();

    const overseasChip = filter.locator('button[data-chip-value="overseas_invoice"]');
    const vatChip = filter.locator('button[data-chip-value="vat_invoice"]');

    await overseasChip.click();
    await expect(overseasChip).toHaveAttribute('aria-pressed', 'true');
    await expect(page).toHaveURL(/category=overseas_invoice/);
    await expect(dataRows).toHaveCount(1);
    await expect(page.getByRole('row').filter({ hasText: 'CATMIX-OVRS-001' })).toBeVisible();

    await vatChip.click();
    await expect(vatChip).toHaveAttribute('aria-pressed', 'true');
    await expect(dataRows).toHaveCount(3);
    await expect(page.getByRole('row').filter({ hasText: 'CATMIX-OVRS-001' })).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: 'CATMIX-VAT-001' })).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: 'SMOKE-INV-001' })).toBeVisible();

    await overseasChip.click();
    await vatChip.click();
    await expect(overseasChip).toHaveAttribute('aria-pressed', 'false');
    await expect(vatChip).toHaveAttribute('aria-pressed', 'false');
    await expect(page).not.toHaveURL(/category=/);
    await expect(dataRows).toHaveCount(6);
  });

  test('category query param survives a page reload', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/invoices$/);

    await page.goto('/invoices?category=receipt');
    const dataRows = page.locator('tbody tr[data-test-id^="invoice-row-"]');
    await expect(dataRows).toHaveCount(1);
    await expect(page.getByRole('row').filter({ hasText: 'CATMIX-RCPT-001' })).toBeVisible();

    const receiptChip = page.locator('[data-test-id="category-filter"] button[data-chip-value="receipt"]');
    await expect(receiptChip).toHaveAttribute('aria-pressed', 'true');

    await page.reload();
    await expect(dataRows).toHaveCount(1);
    await expect(receiptChip).toHaveAttribute('aria-pressed', 'true');
  });
});
