import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';
const SECOND_USER_EMAIL = 'second-user@smoke.invalid';

test.describe('Admin panel (v0.9.0-alpha.9) — multi-user management', () => {
  let secondUserPassword = '';

  test.beforeEach(async ({ request }) => {
    // reset-smoke is intentionally unauth'd (see test_helpers.py); this
    // restores the admin's bootstrap password so subsequent tests can
    // log in even if the prior test rotated it.
    await request.post(`${backendBaseURL}/api/v1/test-helpers/reset-smoke`);

    const loginResponse = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { email: smokeEmail, password: smokePassword },
    });
    expect(loginResponse.ok()).toBeTruthy();
    const { access_token } = await loginResponse.json();

    await request.post(
      `${backendBaseURL}/api/v1/test-helpers/reset-users-to-admin-only`,
      { headers: { Authorization: `Bearer ${access_token}` } }
    );
    const seedResp = await request.post(
      `${backendBaseURL}/api/v1/test-helpers/seed-second-user`,
      { headers: { Authorization: `Bearer ${access_token}` } }
    );
    expect(seedResp.ok()).toBeTruthy();
    const seed = await seedResp.json();
    secondUserPassword = seed.second_user_password;
  });

  test('admin sees Admin nav link; non-admin does not', async ({ page, context }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/invoices$/);
    await expect(page.getByRole('link', { name: 'Admin' })).toBeVisible();

    const nonAdminContext = await context.browser()!.newContext();
    const nonAdminPage = await nonAdminContext.newPage();
    await nonAdminPage.goto('/login');
    await nonAdminPage.getByLabel('Email').fill(SECOND_USER_EMAIL);
    await nonAdminPage.getByLabel('Password').fill(secondUserPassword);
    await nonAdminPage.getByRole('button', { name: 'Sign in' }).click();
    await expect(nonAdminPage).toHaveURL(/\/invoices$/);
    await expect(nonAdminPage.getByRole('link', { name: 'Admin' })).not.toBeVisible();

    await nonAdminPage.goto('/admin');
    await expect(nonAdminPage).toHaveURL(/\/invoices$/);

    await nonAdminContext.close();
  });

  test('admin list renders both users with correct roles', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await page.getByRole('link', { name: 'Admin' }).click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.getByRole('heading', { name: 'User Management' })).toBeVisible();

    const adminRow = page.getByRole('row').filter({ hasText: smokeEmail });
    await expect(adminRow).toBeVisible();
    await expect(adminRow.getByText('Admin').first()).toBeVisible();
    await expect(adminRow.getByText('(you)')).toBeVisible();

    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });
    await expect(secondRow).toBeVisible();
    await expect(secondRow.getByText(/^User$/)).toBeVisible();
    await expect(secondRow.getByText('Active')).toBeVisible();
  });

  test('admin cannot delete themselves (Delete button disabled on own row)', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await page.getByRole('link', { name: 'Admin' }).click();
    const adminRow = page.getByRole('row').filter({ hasText: smokeEmail });
    const deleteButton = adminRow.getByRole('button', { name: 'Delete' });
    await expect(deleteButton).toBeDisabled();
  });

  test('deactivating a non-admin user flips Status to Inactive', async ({ page }) => {
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await page.getByRole('link', { name: 'Admin' }).click();

    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });
    await expect(secondRow.getByText('Active')).toBeVisible();

    const deactivateWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'PUT'
    );
    await secondRow.getByRole('button', { name: 'Deactivate' }).click();
    const resp = await deactivateWait;
    expect(resp.ok()).toBeTruthy();

    await expect(secondRow.getByText('Inactive')).toBeVisible();
    await expect(secondRow.getByRole('button', { name: 'Activate' })).toBeVisible();
  });

  test('promote then demote returns user to User role', async ({ page }) => {
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await page.getByRole('link', { name: 'Admin' }).click();
    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });

    const promoteWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'PUT'
    );
    await secondRow.getByRole('button', { name: 'Promote' }).click();
    await promoteWait;
    await expect(secondRow.getByText('Admin')).toBeVisible();
    await expect(secondRow.getByRole('button', { name: 'Demote' })).toBeVisible();

    const demoteWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'PUT'
    );
    await secondRow.getByRole('button', { name: 'Demote' }).click();
    await demoteWait;
    await expect(secondRow.getByText(/^User$/)).toBeVisible();
    await expect(secondRow.getByRole('button', { name: 'Promote' })).toBeVisible();
  });

  test('deleting second user removes row and requires confirmation modal', async ({
    page,
  }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await page.getByRole('link', { name: 'Admin' }).click();
    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });
    await expect(secondRow).toBeVisible();

    await secondRow.getByRole('button', { name: 'Delete' }).click();

    const dialog = page.locator('.fixed.inset-0').filter({ hasText: 'Delete user' });
    await expect(dialog.getByRole('heading', { name: 'Delete user' })).toBeVisible();
    await expect(dialog.getByText(SECOND_USER_EMAIL)).toBeVisible();
    await expect(
      dialog.getByText(/This will permanently delete all .* of their invoices/i)
    ).toBeVisible();

    const deleteWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'DELETE'
    );
    await dialog.getByRole('button', { name: 'Delete' }).click();
    await deleteWait;

    await expect(page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL })).toHaveCount(0);
  });
});
