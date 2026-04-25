import { test, expect } from '@playwright/test';

const backendBaseURL = 'http://127.0.0.1:8010';
const smokeEmail = 'admin@local';
const smokePassword = 'smoke-admin-password';
const NEW_PASSWORD = 'rotated-admin-password-v2';

test.describe('Change password (v0.9.0-alpha.8) — profile dropdown + session revocation', () => {
  test.beforeEach(async ({ request }) => {
    await request.post(`${backendBaseURL}/api/v1/test-helpers/reset-smoke`);
  });

  test('dropdown opens, Change password updates credentials, success banner shows', async ({
    page,
  }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/invoices$/);

    await page.getByRole('button', { name: new RegExp(smokeEmail, 'i') }).click();
    await expect(page.getByRole('menuitem', { name: 'Change password' })).toBeVisible();
    await page.getByRole('menuitem', { name: 'Change password' }).click();
    await expect(page.getByRole('heading', { name: 'Change password' })).toBeVisible();

    const modal = page.locator('.fixed.inset-0').filter({ hasText: 'Change password' });
    const [currentPwd, newPwd, confirmPwd] = [0, 1, 2].map((i) =>
      modal.locator('input[type="password"]').nth(i)
    );
    await currentPwd.fill(smokePassword);
    await newPwd.fill(NEW_PASSWORD);
    await confirmPwd.fill(NEW_PASSWORD);
    await page.getByRole('button', { name: 'Update password' }).click();

    await expect(
      page.getByText(/Password updated.*Sessions on other devices have been signed out/i)
    ).toBeVisible({ timeout: 5000 });
  });

  test('after rotation, old password fails and new password succeeds', async ({
    page,
    request,
  }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/invoices$/);

    await page.getByRole('button', { name: new RegExp(smokeEmail, 'i') }).click();
    await expect(page.getByRole('menuitem', { name: 'Change password' })).toBeVisible();
    await page.getByRole('menuitem', { name: 'Change password' }).click();
    await expect(page.getByRole('heading', { name: 'Change password' })).toBeVisible();

    const modal = page.locator('.fixed.inset-0').filter({ hasText: 'Change password' });
    await modal.locator('input[type="password"]').nth(0).fill(smokePassword);
    await modal.locator('input[type="password"]').nth(1).fill(NEW_PASSWORD);
    await modal.locator('input[type="password"]').nth(2).fill(NEW_PASSWORD);
    await page.getByRole('button', { name: 'Update password' }).click();
    await expect(page.getByText(/Password updated/i)).toBeVisible({ timeout: 5000 });

    const oldLogin = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { email: smokeEmail, password: smokePassword },
      failOnStatusCode: false,
    });
    expect(oldLogin.status()).toBe(401);

    const newLogin = await request.post(`${backendBaseURL}/api/v1/auth/login`, {
      data: { email: smokeEmail, password: NEW_PASSWORD },
    });
    expect(newLogin.ok()).toBeTruthy();
  });

  test('mismatched new passwords show inline error; no backend call', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await page.getByRole('button', { name: new RegExp(smokeEmail, 'i') }).click();
    await expect(page.getByRole('menuitem', { name: 'Change password' })).toBeVisible();
    await page.getByRole('menuitem', { name: 'Change password' }).click();
    await expect(page.getByRole('heading', { name: 'Change password' })).toBeVisible();

    const modal = page.locator('.fixed.inset-0').filter({ hasText: 'Change password' });
    await modal.locator('input[type="password"]').nth(0).fill(smokePassword);
    await modal.locator('input[type="password"]').nth(1).fill('new-password-alpha');
    await modal.locator('input[type="password"]').nth(2).fill('new-password-beta');
    await page.getByRole('button', { name: 'Update password' }).click();

    await expect(page.getByText(/New passwords do not match/i)).toBeVisible();
  });

  test('new == current password is rejected client-side', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(smokeEmail);
    await page.getByLabel('Password').fill(smokePassword);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await page.getByRole('button', { name: new RegExp(smokeEmail, 'i') }).click();
    await expect(page.getByRole('menuitem', { name: 'Change password' })).toBeVisible();
    await page.getByRole('menuitem', { name: 'Change password' }).click();
    await expect(page.getByRole('heading', { name: 'Change password' })).toBeVisible();

    const modal = page.locator('.fixed.inset-0').filter({ hasText: 'Change password' });
    await modal.locator('input[type="password"]').nth(0).fill(smokePassword);
    await modal.locator('input[type="password"]').nth(1).fill(smokePassword);
    await modal.locator('input[type="password"]').nth(2).fill(smokePassword);
    await page.getByRole('button', { name: 'Update password' }).click();

    await expect(page.getByText(/must differ from current/i)).toBeVisible();
  });
});
