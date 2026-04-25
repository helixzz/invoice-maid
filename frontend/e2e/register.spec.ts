import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';

test.describe('Self-service registration (v0.9.0-alpha.8)', () => {
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
    await expect(page.getByRole('heading', { name: 'Create an account' })).toBeVisible();

    await page.getByLabel('Email').fill(uniqueEmail);
    await page.getByLabel('Password', { exact: true }).fill('register-password-123');
    await page.getByLabel('Confirm Password').fill('register-password-123');
    await page.getByRole('button', { name: 'Sign up' }).click();

    await expect(page).toHaveURL(/\/invoices$/);
    await expect(page.getByRole('link', { name: 'Invoices' })).toBeVisible();

    await expect(page.getByRole('link', { name: 'Admin' })).not.toBeVisible();
  });

  test('password mismatch surfaces an inline error without hitting the backend', async ({ page }) => {
    await page.goto('/register');
    await page.getByLabel('Email').fill('whatever@smoke.invalid');
    await page.getByLabel('Password', { exact: true }).fill('password-one');
    await page.getByLabel('Confirm Password').fill('password-two');
    await page.getByRole('button', { name: 'Sign up' }).click();

    await expect(page.getByText(/passwords do not match/i)).toBeVisible();
    await expect(page).toHaveURL(/\/register$/);
  });
});
