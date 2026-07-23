import { defineConfig } from '@playwright/test'

const chromeExecutable = process.env.TANG_AGENT_CHROME_EXECUTABLE
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const reuseExistingServer = process.env.TANG_AGENT_E2E_REUSE_SERVER === 'true'

export default defineConfig({
  testDir: './e2e',
  outputDir: process.env.TANG_AGENT_E2E_OUTPUT_DIR
    || '/tmp/tang-agent-lesson-38/playwright',
  fullyParallel: false,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:5174',
    viewport: { width: 1440, height: 900 },
    headless: true,
    launchOptions: {
      executablePath: chromeExecutable,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    },
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5174 --strictPort',
    url: 'http://127.0.0.1:5174',
    reuseExistingServer,
    timeout: 30_000,
  },
})
