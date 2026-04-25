import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig, devices } from '@playwright/test';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const smokeRoot = path.join(__dirname, '.playwright');
const smokeDatabasePath = path.join(smokeRoot, 'smoke.db');
const smokeStoragePath = path.join(smokeRoot, 'storage');
const backendPython = path.join(__dirname, '../backend/.venv/bin/python');
const backendPort = 8010;
const backendBaseURL = `http://127.0.0.1:${backendPort}`;
const frontendBaseURL = 'http://127.0.0.1:5173';
const smokePassword = 'smoke-admin-password';
const smokePasswordHash = '$2b$12$NS3mSTtiaTMbgb9FylwLauVF2iUiBioUHuQEECtksz6Jp0kJgDjXO';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  use: {
    baseURL: frontendBaseURL,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: `sh -c 'rm -rf "${smokeRoot}" && mkdir -p "${smokeStoragePath}" && "${backendPython}" -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}'`,
      cwd: path.join(__dirname, '../backend'),
      url: `${backendBaseURL}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120 * 1000,
      env: {
        ...process.env,
        DATABASE_URL: `sqlite+aiosqlite:///${smokeDatabasePath}`,
        STORAGE_PATH: smokeStoragePath,
        ADMIN_PASSWORD_HASH: smokePasswordHash,
        JWT_SECRET: 'smoke-jwt-secret',
        JWT_EXPIRE_MINUTES: '30',
        LLM_BASE_URL: 'https://llm.invalid/v1',
        LLM_API_KEY: 'smoke-key',
        LLM_MODEL: 'smoke-model',
        LLM_EMBED_MODEL: 'smoke-embed-model',
        EMBED_DIM: '3',
        SCAN_INTERVAL_MINUTES: '1440',
        SQLITE_VEC_ENABLED: 'false',
        ENABLE_TEST_HELPERS: 'true',
        ALLOW_REGISTRATION: 'true',
      },
    },
    {
      command: `sh -c 'VITE_BACKEND_BASE_URL=${backendBaseURL} npm run dev -- --host 127.0.0.1 --port 5173'`,
      cwd: __dirname,
      url: frontendBaseURL,
      reuseExistingServer: false,
      timeout: 120 * 1000,
      env: process.env,
    },
  ],
});
