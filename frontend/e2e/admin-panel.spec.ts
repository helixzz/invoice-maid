import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';
const SECOND_USER_EMAIL = 'second-user@smoke.invalid';

test.describe('Admin panel — multi-user management', () => {
  let secondUserPassword = '';

  test.beforeEach(async ({ request }) => {
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
    await page.getByLabel('邮箱').fill(smokeEmail);
    await page.getByLabel('密码', { exact: true }).fill(smokePassword);
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page).toHaveURL(/\/invoices$/);
    await expect(page.getByRole('link', { name: '用户管理' })).toBeVisible();

    const nonAdminContext = await context.browser()!.newContext();
    const nonAdminPage = await nonAdminContext.newPage();
    await nonAdminPage.goto('/login');
    await nonAdminPage.getByLabel('邮箱').fill(SECOND_USER_EMAIL);
    await nonAdminPage.getByLabel('密码', { exact: true }).fill(secondUserPassword);
    await nonAdminPage.getByRole('button', { name: '登录' }).click();
    await expect(nonAdminPage).toHaveURL(/\/invoices$/);
    await expect(nonAdminPage.getByRole('link', { name: '用户管理' })).not.toBeVisible();

    await nonAdminPage.goto('/admin');
    await expect(nonAdminPage).toHaveURL(/\/invoices$/);

    await nonAdminContext.close();
  });

  test('admin list renders both users with correct roles', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('邮箱').fill(smokeEmail);
    await page.getByLabel('密码', { exact: true }).fill(smokePassword);
    await page.getByRole('button', { name: '登录' }).click();

    await page.getByRole('link', { name: '用户管理' }).click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.getByRole('heading', { name: '用户管理' }).first()).toBeVisible();

    const adminRow = page.getByRole('row').filter({ hasText: smokeEmail });
    await expect(adminRow).toBeVisible();
    await expect(adminRow.getByText('管理员').first()).toBeVisible();
    await expect(adminRow.getByText('当前')).toBeVisible();

    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });
    await expect(secondRow).toBeVisible();
    await expect(secondRow.getByText('成员', { exact: true })).toBeVisible();
    await expect(secondRow.getByText('启用', { exact: true })).toBeVisible();
  });

  test('admin cannot delete themselves (Delete button disabled on own row)', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('邮箱').fill(smokeEmail);
    await page.getByLabel('密码', { exact: true }).fill(smokePassword);
    await page.getByRole('button', { name: '登录' }).click();

    await page.getByRole('link', { name: '用户管理' }).click();
    const adminRow = page.getByRole('row').filter({ hasText: smokeEmail });
    const deleteButton = adminRow.getByRole('button', { name: '删除' });
    await expect(deleteButton).toBeDisabled();
  });

  test('deactivating a non-admin user flips Status to Inactive', async ({ page }) => {
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto('/login');
    await page.getByLabel('邮箱').fill(smokeEmail);
    await page.getByLabel('密码', { exact: true }).fill(smokePassword);
    await page.getByRole('button', { name: '登录' }).click();

    await page.getByRole('link', { name: '用户管理' }).click();

    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });
    await expect(secondRow.getByText('启用', { exact: true })).toBeVisible();

    const deactivateWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'PUT'
    );
    await secondRow.getByRole('button', { name: '停用' }).click();
    const resp = await deactivateWait;
    expect(resp.ok()).toBeTruthy();

    await expect(secondRow.getByText('已停用')).toBeVisible();
    await expect(secondRow.getByRole('button', { name: '启用' })).toBeVisible();
  });

  test('promote then demote returns user to member role', async ({ page }) => {
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto('/login');
    await page.getByLabel('邮箱').fill(smokeEmail);
    await page.getByLabel('密码', { exact: true }).fill(smokePassword);
    await page.getByRole('button', { name: '登录' }).click();

    await page.getByRole('link', { name: '用户管理' }).click();
    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });

    const promoteWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'PUT'
    );
    await secondRow.getByRole('button', { name: '设为管理' }).click();
    await promoteWait;
    await expect(secondRow.getByText('管理员')).toBeVisible();
    await expect(secondRow.getByRole('button', { name: '取消管理' })).toBeVisible();

    const demoteWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'PUT'
    );
    await secondRow.getByRole('button', { name: '取消管理' }).click();
    await demoteWait;
    await expect(secondRow.getByText('成员', { exact: true })).toBeVisible();
    await expect(secondRow.getByRole('button', { name: '设为管理' })).toBeVisible();
  });

  test('deleting second user removes row and requires confirmation modal', async ({
    page,
  }) => {
    await page.goto('/login');
    await page.getByLabel('邮箱').fill(smokeEmail);
    await page.getByLabel('密码', { exact: true }).fill(smokePassword);
    await page.getByRole('button', { name: '登录' }).click();

    await page.getByRole('link', { name: '用户管理' }).click();
    const secondRow = page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL });
    await expect(secondRow).toBeVisible();

    await secondRow.getByRole('button', { name: '删除' }).click();

    const dialog = page.locator('.fixed.inset-0').filter({ hasText: '删除用户' });
    await expect(dialog.getByRole('heading', { name: '删除用户' })).toBeVisible();
    await expect(dialog.getByText(SECOND_USER_EMAIL)).toBeVisible();

    const deleteWait = page.waitForResponse(
      (r) => r.url().includes('/api/v1/admin/users/') && r.request().method() === 'DELETE'
    );
    await dialog.getByRole('button', { name: '确认删除' }).click();
    await deleteWait;

    await expect(page.getByRole('row').filter({ hasText: SECOND_USER_EMAIL })).toHaveCount(0);
  });
});
