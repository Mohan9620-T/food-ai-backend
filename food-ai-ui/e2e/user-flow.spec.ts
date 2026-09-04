import { expect, test } from '@playwright/test';

const json = (body: unknown) => ({
  body: JSON.stringify(body),
  contentType: 'application/json',
});

test('user can sign up, log in, chat, and analyze a food photo', async ({ page }) => {
  const payload = Buffer.from(
    JSON.stringify({
      sub: '42',
      email: 'rajesh@example.com',
      fullname: 'Rajesh',
    }),
  ).toString('base64url');
  const accessToken = `header.${payload}.signature`;

  await page.route('http://127.0.0.1:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === '/users/' && request.method() === 'POST') {
      await route.fulfill({
        status: 201,
        ...json({ id: 42, fullname: 'Rajesh', email: 'rajesh@example.com', email_sent: false }),
      });
      return;
    }
    if (url.pathname === '/users/login' && request.method() === 'POST') {
      await route.fulfill({
        status: 200,
        ...json({
          access_token: accessToken,
          refresh_token: 'refresh-token',
          token_type: 'bearer',
        }),
      });
      return;
    }
    if (url.pathname === '/chat/sessions/consolidate' && request.method() === 'POST') {
      await route.fulfill({ status: 200, ...json([]) });
      return;
    }
    if (url.pathname === '/chat/sessions' && request.method() === 'GET') {
      await route.fulfill({ status: 200, ...json([]) });
      return;
    }
    if (url.pathname === '/chat/stream' && request.method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/x-ndjson',
        body: [
          JSON.stringify({ type: 'session', session_id: 101 }),
          JSON.stringify({ type: 'token', content: 'Hello! I can help with your meal.' }),
          JSON.stringify({ type: 'done' }),
          '',
        ].join('\n'),
      });
      return;
    }
    if (url.pathname === '/chat/vision' && request.method() === 'POST') {
      await route.fulfill({
        status: 200,
        ...json({
          session_id: 101,
          response: 'This photo contains idli. Estimated nutrition: 58 calories per idli.',
        }),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      ...json({ detail: `Unexpected E2E request: ${url.pathname}` }),
    });
  });

  await page.goto('/register');
  await page.getByPlaceholder('Full name').fill('Rajesh');
  await page.getByPlaceholder('Email').fill('rajesh@example.com');
  await page.getByPlaceholder('Password', { exact: true }).fill('secret123');
  await page.getByPlaceholder('Confirm password').fill('secret123');
  await page.getByRole('button', { name: 'Sign Up' }).click();
  await expect(page.getByText('Account created, but email was not sent')).toBeVisible();

  await page.getByRole('link', { name: 'Login' }).click();
  await page.getByLabel('Email').fill('rajesh@example.com');
  await page.getByLabel('Password').fill('secret123');
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(page.getByRole('heading', { name: 'How can I help?' })).toBeVisible();

  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Help me plan a healthy meal');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByText('Hello! I can help with your meal.')).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: 'idli.png',
    mimeType: 'image/png',
    buffer: Buffer.from('mock food photo'),
  });
  await expect(page.getByAltText('Selected image preview')).toBeVisible();
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('What food is this and what is its nutrition?');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByText(/contains idli.*58 calories/i)).toBeVisible();
  await expect(page.getByAltText('Attached image preview')).toBeVisible();
});
