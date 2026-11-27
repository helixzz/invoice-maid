import { test, expect, type Page } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';

const SEED_INVOICE_IDS = ['CATMIX-VAT-001', 'CATMIX-OVRS-001', 'CATMIX-RCPT-001', 'CATMIX-PROF-001', 'CATMIX-OTHR-001'];

async function signIn(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('邮箱').fill(smokeEmail);
  await page.getByLabel('密码', { exact: true }).fill(smokePassword);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/invoices$/);
}

test.describe('Invoice category filter & saved views', () => {
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

    const authHeaders = { Authorization: `Bearer ${access_token}` };
    const existingViews = await request.get(`${backendBaseURL}/api/v1/views`, { headers: authHeaders });
    expect(existingViews.status()).toBe(200);
    const leftovers: { id: number }[] = await existingViews.json();
    for (const leftover of leftovers) {
      const removed = await request.delete(`${backendBaseURL}/api/v1/views/${leftover.id}`, {
        headers: authHeaders,
      });
      expect(removed.status()).toBe(204);
    }
  });

  test('renders badges, narrows rows via chips, and shows by_category stats', async ({ page }) => {
    await signIn(page);

    const dataRows = page.locator('tbody tr[data-test-id^="invoice-row-"]');
    await expect(dataRows).toHaveCount(6);

    for (const invoiceNo of SEED_INVOICE_IDS) {
      const row = page.getByRole('row').filter({ hasText: invoiceNo });
      await expect(row).toBeVisible();
      await expect(row.locator('[data-category]')).toBeVisible();
    }

    // Expand collapsible monthly & category breakdown drawer
    await page.getByRole('button', { name: /展开月度与分类统计/ }).click();
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
    await signIn(page);

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

  test('saved view round-trips q, dates and categories through create/clear/reapply/delete', async ({ page }) => {
    const viewName = 'Receipts and proformas 2026';

    await signIn(page);

    const dataRows = page.locator('tbody tr[data-test-id^="invoice-row-"]');
    await expect(dataRows).toHaveCount(6);

    const viewSelect = page.locator('[data-test-id="saved-view-select"]');
    await expect(viewSelect).toHaveValue('');
    await expect(viewSelect.locator('option')).toHaveCount(1);

    const dateFromInput = page.locator('input[type="date"]').first();
    await dateFromInput.fill('2026-01-01');
    await expect(dataRows).toHaveCount(5);

    const filter = page.locator('[data-test-id="category-filter"]');
    const receiptChip = filter.locator('button[data-chip-value="receipt"]');
    const proformaChip = filter.locator('button[data-chip-value="proforma"]');
    await receiptChip.click();
    await proformaChip.click();
    await expect(dataRows).toHaveCount(2);

    // --- create ---------------------------------------------------------
    const saveViewTrigger = page.getByRole('button', { name: '保存视图' });
    await expect(saveViewTrigger).toBeVisible();
    await saveViewTrigger.click();

    const dialog = page.getByRole('dialog');
    await dialog.getByPlaceholder(/本月未报销/).fill(viewName);

    const createResponsePromise = page.waitForResponse(
      response =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname.endsWith('/views'),
    );
    await dialog.getByRole('button', { name: '保存视图' }).click();
    const createResponse = await createResponsePromise;

    expect(new URL(createResponse.url()).pathname).toBe('/api/v1/views');
    expect(createResponse.status()).toBe(201);

    const requestPayload = JSON.parse(createResponse.request().postData() ?? '{}');
    expect(requestPayload.name).toBe(viewName);
    expect(JSON.parse(requestPayload.filter_json)).toEqual({
      q: '',
      date_from: '2026-01-01',
      date_to: '',
      category: ['receipt', 'proforma'],
      reimbursement_status: [],
    });

    const createdView = await createResponse.json();
    expect(JSON.parse(createdView.filter_json).category).toEqual(['receipt', 'proforma']);

    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(viewSelect).toHaveValue(String(createdView.id));
    await expect(viewSelect.locator('option')).toHaveCount(2);

    // --- clear via All Invoices -----------------------------------------
    await viewSelect.selectOption('');
    await expect(viewSelect).toHaveValue('');
    await expect(dateFromInput).toHaveValue('');
    await expect(receiptChip).toHaveAttribute('aria-pressed', 'false');
    await expect(proformaChip).toHaveAttribute('aria-pressed', 'false');
    await expect(page).not.toHaveURL(/category=/);
    await expect(dataRows).toHaveCount(6);

    // --- reapply ---------------------------------------------------------
    await viewSelect.selectOption(String(createdView.id));
    await expect(receiptChip).toHaveAttribute('aria-pressed', 'true');
    await expect(proformaChip).toHaveAttribute('aria-pressed', 'true');
    await expect(dateFromInput).toHaveValue('2026-01-01');
    await expect(page).toHaveURL(/category=receipt/);
    await expect(page).toHaveURL(/category=proforma/);
    await expect(dataRows).toHaveCount(2);
    await expect(page.getByRole('row').filter({ hasText: 'CATMIX-RCPT-001' })).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: 'CATMIX-PROF-001' })).toBeVisible();

    // --- a manual edit releases the active view label --------------------
    const deleteViewButton = page.getByTitle('删除当前视图');
    await expect(deleteViewButton).toHaveCount(1);

    await page.getByPlaceholder(/搜索销售方/).fill('CATMIX');
    await expect(viewSelect).toHaveValue('');
    await expect(deleteViewButton).toHaveCount(0);

    await page.getByPlaceholder(/搜索销售方/).fill('');
    await expect(viewSelect).toHaveValue('');

    await viewSelect.selectOption(String(createdView.id));
    await expect(viewSelect).toHaveValue(String(createdView.id));
    await expect(deleteViewButton).toHaveCount(1);

    await receiptChip.click();
    await expect(receiptChip).toHaveAttribute('aria-pressed', 'false');
    await expect(viewSelect).toHaveValue('');
    await expect(deleteViewButton).toHaveCount(0);
    await expect(dataRows).toHaveCount(1);
    await expect(page.getByRole('row').filter({ hasText: 'CATMIX-PROF-001' })).toBeVisible();

    await viewSelect.selectOption(String(createdView.id));
    await expect(viewSelect).toHaveValue(String(createdView.id));
    await expect(receiptChip).toHaveAttribute('aria-pressed', 'true');
    await expect(dataRows).toHaveCount(2);

    // --- delete ----------------------------------------------------------
    const deleteResponsePromise = page.waitForResponse(
      response =>
        response.request().method() === 'DELETE' &&
        new URL(response.url()).pathname.endsWith(`/views/${createdView.id}`),
    );
    await page.getByTitle('删除当前视图').click();
    const deleteResponse = await deleteResponsePromise;
    expect(new URL(deleteResponse.url()).pathname).toBe(`/api/v1/views/${createdView.id}`);
    expect(deleteResponse.status()).toBe(204);

    await expect(viewSelect.locator(`option[value="${createdView.id}"]`)).toHaveCount(0);
    await expect(viewSelect.locator('option')).toHaveCount(1);
    await expect(viewSelect).toHaveValue('');
  });

  test('a saved view predating categories applies with no category filter', async ({ page, request }) => {
    const login = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { email: smokeEmail, password: smokePassword },
    });
    expect(login.ok()).toBeTruthy();
    const { access_token } = await login.json();

    const legacy = await request.post(`${backendBaseURL}/api/v1/views`, {
      headers: { Authorization: `Bearer ${access_token}` },
      data: {
        name: 'Legacy 2026 view',
        filter_json: JSON.stringify({ q: '', date_from: '2026-01-01', date_to: '' }),
      },
    });
    expect(legacy.status()).toBe(201);
    const legacyView = await legacy.json();

    await signIn(page);

    const viewSelect = page.locator('[data-test-id="saved-view-select"]');
    await expect(viewSelect.locator(`option[value="${legacyView.id}"]`)).toHaveCount(1);
    await viewSelect.selectOption(String(legacyView.id));

    const dataRows = page.locator('tbody tr[data-test-id^="invoice-row-"]');
    await expect(dataRows).toHaveCount(5);
    await expect(page.locator('input[type="date"]').first()).toHaveValue('2026-01-01');
    await expect(page).not.toHaveURL(/category=/);

    const filter = page.locator('[data-test-id="category-filter"]');
    for (const category of ['vat_invoice', 'overseas_invoice', 'receipt', 'proforma', 'other']) {
      await expect(filter.locator(`button[data-chip-value="${category}"]`)).toHaveAttribute(
        'aria-pressed',
        'false',
      );
    }
  });
});
