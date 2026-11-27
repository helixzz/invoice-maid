import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';

test.describe('Self-service registration', () => {
  test.beforeEach(async ({ request }) => {
    await request.post(`${backendBaseURL}/api/v1/test-helpers/reset-smoke`);

    const loginResponse = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { email: smokeEmail, password: smokePassword },
    });
    expect(loginResponse.ok()).toBeTruthy();
    const { access_token } = await loginResponse.json();

    const resp = await request.post(
      `${backendBaseURL}/api/v1/test-helpers/reset-users-to-admin-only`,
      { headers: { Authorization: `Bearer ${access_token}` } }
    );
    expect(resp.ok()).toBeTruthy();
  });

  test('new user can register, lands on /invoices, and shows non-admin UI', async ({ page }) => {
    const uniqueEmail = `e2e-register-${Date.now()}@smoke.invalid`;

    await page.goto('/register');
    await expect(page.getByRole('heading', { name: '注册账号' })).toBeVisible();

    await page.getByLabel('邮箱').fill(uniqueEmail);
    await page.getByLabel('密码', { exact: true }).fill('register-password-123');
    await page.getByLabel('确认密码').fill('register-password-123');
    await page.getByRole('button', { name: '注册' }).click();

    await expect(page).toHaveURL(/\/invoices$/);
    await expect(page.getByRole('link', { name: '票据台账' })).toBeVisible();
    await expect(page.getByRole('link', { name: '用户管理' })).not.toBeVisible();
  });

  test('password mismatch surfaces an inline error without hitting the backend', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabel('邮箱').fill('whatever@smoke.invalid');
    await page.getByLabel('密码', { exact: true }).fill('password-one');
    await page.getByLabel('确认密码').fill('password-two');
    await page.getByRole('button', { name: '注册' }).click();

    await expect(page.getByText(/密码不一致|do not match/i)).toBeVisible();
    await expect(page).toHaveURL(/\/register$/);
  });
});
