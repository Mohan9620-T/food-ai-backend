import { expect, test, type Page } from '@playwright/test';

interface UploadedFile {
  name: string;
  mimeType: string;
  buffer: Buffer;
}

interface GenerationRequest {
  session_id: number | null;
  instruction?: string;
  output_format: 'pdf' | 'docx';
  mode?: 'ai' | 'export';
  source_document_id?: number;
}

interface SpreadsheetOperationRequest {
  session_id: number;
  instruction: string;
}

const documentTypes = [
  ['pdf', 'application/pdf'],
  ['docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  ['txt', 'text/plain'],
  ['csv', 'text/csv'],
  ['xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
] as const;

const imageTypes = [
  ['jpg', 'image/jpeg'],
  ['png', 'image/png'],
  ['webp', 'image/webp'],
  ['gif', 'image/gif'],
] as const;

const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) });

// These tests verify browser transport and interaction. Parser coverage uses real
// document fixtures in the backend suite; no file here reaches a live backend.
function sampleFile(extension: string, mimeType: string): UploadedFile {
  return {
    name: `nutrition.${extension}`,
    mimeType,
    buffer: Buffer.from('Synthetic nutrition source'),
  };
}

async function mockApiAndLogin(
  page: Page,
  options: {
    failFirstUpload?: boolean;
    failFirstGeneration?: boolean;
    unavailableAnalysis?: boolean;
  } = {},
) {
  const state = {
    uploads: [] as string[],
    visionUploads: [] as string[],
    generations: [] as GenerationRequest[],
    spreadsheetOperations: [] as SpreadsheetOperationRequest[],
    downloads: 0,
    unexpected: [] as string[],
  };
  const payload = Buffer.from(
    JSON.stringify({
      sub: '42',
      email: 'document-test@example.com',
      fullname: 'Document Tester',
    }),
  ).toString('base64url');

  await page.route('http://127.0.0.1:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204 });
      return;
    }
    if (path === '/users/login' && request.method() === 'POST') {
      await route.fulfill({
        ...json({
          access_token: `header.${payload}.signature`,
          refresh_token: 'synthetic-refresh-token',
          token_type: 'bearer',
        }),
      });
      return;
    }
    if (
      (path === '/chat/sessions' && request.method() === 'GET') ||
      (path === '/chat/sessions/consolidate' && request.method() === 'POST')
    ) {
      await route.fulfill(json([]));
      return;
    }
    if (path === '/chat/documents' && request.method() === 'POST') {
      const body = request.postDataBuffer()?.toString() ?? '';
      state.uploads.push(body);
      expect(request.headers()['content-type']).toMatch(/^multipart\/form-data; boundary=/);
      expect(body).toContain('name="file"; filename="nutrition.');
      expect(body).toContain('Synthetic nutrition source');
      if (options.failFirstUpload && state.uploads.length === 1) {
        await route.fulfill({
          status: 503,
          ...json({ detail: 'Document processing is temporarily unavailable. Please try again.' }),
        });
        return;
      }
      const filename = /filename="([^"]+)"/.exec(body)?.[1] ?? 'nutrition.txt';
      const skippedAnalysis = /name="analyze"\r\n\r\nfalse/.test(body);
      const categorySplit = filename.endsWith('.xlsx') && /category-wise/i.test(body);
      await route.fulfill(
        json({
          session_id: 101,
          response: categorySplit
            ? `Done — I split the item list from ${filename} category-wise and created a new Excel workbook with a separate sheet for each category.`
            : skippedAnalysis
              ? `Saved ${filename}. AI analysis was skipped.`
              : options.unavailableAnalysis
                ? `Saved ${filename}. Extracted text only; AI analysis is unavailable.`
                : `Imported ${filename}. Nutrition summary is ready.`,
          analysis_status:
            categorySplit || skippedAnalysis
              ? 'skipped'
              : options.unavailableAnalysis
                ? 'unavailable'
                : 'complete',
          attachment: {
            id: categorySplit ? 203 : 201,
            filename: categorySplit ? 'nutrition_category_wise.xlsx' : filename,
            content_type: categorySplit ? documentTypes[4][1] : 'application/octet-stream',
            file_size: 26,
            kind: categorySplit ? 'generated' : 'uploaded',
          },
        }),
      );
      return;
    }
    if (path === '/chat/vision' && request.method() === 'POST') {
      const body = request.postDataBuffer()?.toString() ?? '';
      state.visionUploads.push(body);
      expect(body).toContain('name="image"; filename="nutrition.');
      await route.fulfill(json({ session_id: 101, response: 'Image nutrition summary is ready.' }));
      return;
    }
    if (path === '/chat/documents/spreadsheet' && request.method() === 'POST') {
      const body = request.postDataJSON() as SpreadsheetOperationRequest;
      state.spreadsheetOperations.push(body);
      await route.fulfill(
        json({
          session_id: 101,
          response:
            'Done. I added the requested column filter and generated the updated Excel workbook.',
          analysis_status: 'skipped',
          attachment: {
            id: 204,
            filename: 'nutrition_category_wise_filter_updated.xlsx',
            content_type: documentTypes[4][1],
            file_size: 26,
            kind: 'generated',
          },
        }),
      );
      return;
    }
    if (path === '/chat/documents/generate' && request.method() === 'POST') {
      const body = request.postDataJSON() as GenerationRequest;
      state.generations.push(body);
      if (options.failFirstGeneration && state.generations.length === 1) {
        await route.fulfill({
          status: 503,
          ...json({
            detail:
              'AI document generation is unavailable. Try again, or use Export text without AI to save existing text.',
          }),
        });
        return;
      }
      await route.fulfill(
        json({
          session_id: 101,
          response: 'Your nutrition document is ready.',
          attachment: {
            id: 202,
            filename: `nutrition-summary.${body.output_format}`,
            content_type: body.output_format === 'pdf' ? 'application/pdf' : documentTypes[1][1],
            file_size: 26,
            kind: 'generated',
          },
        }),
      );
      return;
    }
    if (
      (path === '/chat/documents/204/download' ||
        path === '/chat/documents/203/download' ||
        path === '/chat/documents/202/download' ||
        path === '/chat/documents/201/download') &&
      request.method() === 'GET'
    ) {
      state.downloads += 1;
      await route.fulfill({
        contentType: 'application/octet-stream',
        body: 'Synthetic generated document',
      });
      return;
    }
    state.unexpected.push(`${request.method()} ${path}`);
    await route.fulfill({ status: 404, ...json({ detail: 'Unexpected test request' }) });
  });
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill('document-test@example.com');
  await page.getByLabel('Password', { exact: true }).fill('synthetic-password');
  await page.getByRole('button', { name: 'Login', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'How can I help?' })).toBeVisible();
  return state;
}

async function attachFile(page: Page, file: UploadedFile, method: 'picker' | 'drop') {
  if (method === 'picker') {
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Attach image or document', exact: true }).click();
    await (await chooser).setFiles(file);
    return;
  }
  const transfer = await page.evaluateHandle(
    ({ name, mimeType, content }) => {
      const data = new DataTransfer();
      data.items.add(new File([content], name, { type: mimeType }));
      return data;
    },
    { name: file.name, mimeType: file.mimeType, content: file.buffer.toString() },
  );
  try {
    await page.locator('body').dispatchEvent('dragenter', { dataTransfer: transfer });
    await expect(
      page.getByText('Drop an image or document to attach', { exact: true }),
    ).toBeVisible();
    await page.locator('body').dispatchEvent('dragover', { dataTransfer: transfer });
    await page.locator('body').dispatchEvent('drop', { dataTransfer: transfer });
    await expect(
      page.getByText('Drop an image or document to attach', { exact: true }),
    ).not.toBeVisible();
  } finally {
    await transfer.dispose();
  }
}

for (const [extension, mimeType] of documentTypes) {
  for (const method of ['picker', 'drop'] as const) {
    test(`imports ${extension.toUpperCase()} using ${method} and Send`, async ({ page }) => {
      const state = await mockApiAndLogin(page);
      const file = sampleFile(extension, mimeType);
      await attachFile(page, file, method);
      await expect(page.locator('.document-preview')).toContainText(file.name);
      await expect(page.getByRole('button', { name: 'Send message' })).toBeEnabled();
      expect(state.uploads).toHaveLength(0);
      await page.getByRole('button', { name: 'Send message' }).click();
      await expect(
        page.getByText(`Imported ${file.name}. Nutrition summary is ready.`, { exact: true }),
      ).toBeVisible();
      await expect(page.locator('.document-preview')).not.toBeVisible();
      await expect(page.locator('.user-message .document-card')).toContainText(file.name);
      expect(state.uploads).toHaveLength(1);
      expect(state.visionUploads).toHaveLength(0);
      expect(state.unexpected).toEqual([]);
    });
  }
}

for (const [extension, mimeType] of imageTypes) {
  for (const method of ['picker', 'drop'] as const) {
    test(`keeps ${extension.toUpperCase()} image ${method} uploads working`, async ({ page }) => {
      const state = await mockApiAndLogin(page);
      await attachFile(page, sampleFile(extension, mimeType), method);
      await expect(page.getByAltText('Selected image preview')).toBeVisible();
      await page.getByRole('button', { name: 'Send message' }).click();
      await expect(
        page.getByText('Image nutrition summary is ready.', { exact: true }),
      ).toBeVisible();
      await expect(page.getByAltText('Attached image preview')).toBeVisible();
      expect(state.visionUploads).toHaveLength(1);
      expect(state.uploads).toHaveLength(0);
      expect(state.unexpected).toEqual([]);
    });
  }
}

test('uses the normal composer for pasted content and has no document creator control', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page);
  const composer = page.getByRole('textbox', { name: 'Message', exact: true });
  await expect(page.getByRole('button', { name: 'Create document', exact: true })).toHaveCount(0);
  await expect(composer).toHaveAttribute('placeholder', /paste content/i);
  await composer.fill('First pasted line\n\nSecond pasted line');
  await expect(composer).toHaveValue('First pasted line\n\nSecond pasted line');
  expect(state.generations).toHaveLength(0);
  expect(state.unexpected).toEqual([]);
});

test('splits an uploaded Excel item list and downloads the generated workbook', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page);
  await attachFile(page, sampleFile('xlsx', documentTypes[4][1]), 'picker');
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill(
      'I have many categories. Please split the item list category-wise and create a separate sheet for each category, with the corresponding items added to each sheet.',
    );

  await page.getByRole('button', { name: 'Send message' }).click();

  await expect(
    page.getByText(/created a new Excel workbook with a separate sheet for each category/),
  ).toBeVisible();
  const downloadButton = page.getByRole('button', {
    name: 'Download nutrition_category_wise.xlsx',
    exact: true,
  });
  await expect(downloadButton).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await downloadButton.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('nutrition_category_wise.xlsx');
  expect(await download.failure()).toBeNull();
  expect(state.uploads).toHaveLength(1);
  expect(state.generations).toHaveLength(0);
  expect(state.downloads).toBe(1);
  expect(state.unexpected).toEqual([]);
});

test('applies a filter follow-up and shows the chained generated workbook', async ({ page }) => {
  const state = await mockApiAndLogin(page);
  const composer = page.getByRole('textbox', { name: 'Message', exact: true });
  await attachFile(page, sampleFile('xlsx', documentTypes[4][1]), 'picker');
  await composer.fill(
    'Split the item list category-wise and create a separate sheet for each category.',
  );
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(
    page.getByRole('button', { name: 'Download nutrition_category_wise.xlsx' }),
  ).toBeVisible();

  await composer.fill('Please add a filter only to the Category* column.');
  await page.getByRole('button', { name: 'Send message' }).click();

  await expect(page.getByText(/added the requested column filter/)).toBeVisible();
  const downloadButton = page.getByRole('button', {
    name: 'Download nutrition_category_wise_filter_updated.xlsx',
    exact: true,
  });
  await expect(downloadButton).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await downloadButton.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('nutrition_category_wise_filter_updated.xlsx');
  expect(state.spreadsheetOperations).toEqual([
    {
      session_id: 101,
      instruction: 'Please add a filter only to the Category* column.',
    },
  ]);
  expect(state.generations).toHaveLength(0);
  expect(state.downloads).toBe(1);
  expect(state.unexpected).toEqual([]);
});

for (const format of ['pdf', 'docx'] as const) {
  test.skip(`creates and downloads ${format.toUpperCase()} from a new chat`, async ({ page }) => {
    const state = await mockApiAndLogin(page);
    await page.getByRole('button', { name: 'Create document', exact: true }).click();
    await expect(page.getByLabel('Document instructions', { exact: true })).toBeFocused();
    await expect(page.getByRole('alert')).not.toBeVisible();
    await page
      .getByLabel('Document instructions', { exact: true })
      .fill('Create a nutrition summary with headings.');
    await page.getByLabel('File format', { exact: true }).selectOption(format);
    await page
      .getByRole('form', { name: 'Create a document', exact: true })
      .getByRole('button', { name: 'Create document', exact: true })
      .click();
    const filename = `nutrition-summary.${format}`;
    await expect(
      page.getByRole('button', { name: `Download ${filename}`, exact: true }),
    ).toBeVisible();
    expect(state.generations).toEqual([
      {
        session_id: null,
        instruction: 'Create a nutrition summary with headings.',
        output_format: format,
      },
    ]);
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: `Download ${filename}`, exact: true }).click();
    const download = await downloadEvent;
    expect(download.suggestedFilename()).toBe(filename);
    expect(await download.failure()).toBeNull();
    expect(state.downloads).toBe(1);
    expect(state.unexpected).toEqual([]);
  });

  test.skip(`exports literal text to ${format.toUpperCase()} without AI or importing a file`, async ({
    page,
  }) => {
    const state = await mockApiAndLogin(page);
    await page.getByRole('button', { name: 'Create document', exact: true }).click();
    await page.getByLabel('Creation mode', { exact: true }).selectOption('export');
    await expect(page.locator('#document-source-help')).toContainText(
      'does not summarize, rewrite, or use conversation history or attached files',
    );
    const text = '  Nutrition notes\n\nBreakfast: oats and fruit.  ';
    await page.getByLabel('Text to export', { exact: true }).fill(text);
    await page.getByLabel('File format', { exact: true }).selectOption(format);
    await page
      .getByRole('form', { name: 'Create a document', exact: true })
      .getByRole('button', { name: 'Create document', exact: true })
      .click();
    const filename = `nutrition-summary.${format}`;
    await expect(
      page.getByRole('button', { name: `Download ${filename}`, exact: true }),
    ).toBeVisible();
    expect(state.generations).toEqual([
      {
        session_id: null,
        instruction: text,
        output_format: format,
        mode: 'export',
      },
    ]);
    expect(state.uploads).toHaveLength(0);
    expect(state.visionUploads).toHaveLength(0);
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: `Download ${filename}`, exact: true }).click();
    const download = await downloadEvent;
    expect(download.suggestedFilename()).toBe(filename);
    expect(await download.failure()).toBeNull();
    expect(state.unexpected).toEqual([]);
  });

  test.skip(`exports an attached file to ${format.toUpperCase()} without AI or instructions`, async ({
    page,
  }) => {
    const state = await mockApiAndLogin(page);
    await attachFile(page, sampleFile('xlsx', documentTypes[4][1]), 'picker');
    await page
      .getByRole('textbox', { name: 'Message', exact: true })
      .fill('Keep this unsent draft');
    await page.getByRole('button', { name: 'Create document', exact: true }).click();
    await page.getByLabel('Creation mode', { exact: true }).selectOption('export-file');
    await expect(
      page.getByLabel('Draft text (not used for file export)', { exact: true }),
    ).toBeDisabled();
    await expect(page.locator('#document-source-help')).toContainText('nutrition.xlsx');
    await page.getByLabel('File format', { exact: true }).selectOption(format);
    await page
      .getByRole('form', { name: 'Create a document', exact: true })
      .getByRole('button', { name: 'Create document', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: `Download nutrition-summary.${format}`, exact: true }),
    ).toBeVisible();
    expect(state.uploads).toHaveLength(1);
    expect(state.uploads[0]).toMatch(/name="analyze"\r\n\r\nfalse/);
    expect(state.generations).toEqual([
      { session_id: 101, output_format: format, mode: 'export', source_document_id: 201 },
    ]);
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(
      'Keep this unsent draft',
    );
    await expect(page.locator('.document-preview')).not.toBeVisible();
    expect(state.unexpected).toEqual([]);
  });
}

test.skip('stops after an imported file reports unavailable AI and exports its saved source without reuploading', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page, { unavailableAnalysis: true });
  await attachFile(page, sampleFile('pdf', 'application/pdf'), 'drop');
  await page.getByRole('button', { name: 'Create document', exact: true }).click();
  await page
    .getByLabel('Document instructions', { exact: true })
    .fill('Write a report from this PDF');
  const form = page.getByRole('form', { name: 'Create a document', exact: true });
  await form.getByRole('button', { name: 'Create document', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('File saved, but AI analysis is unavailable');
  await expect(page.getByLabel('Document instructions', { exact: true })).toHaveValue(
    'Write a report from this PDF',
  );
  await expect(form.getByRole('button', { name: 'Create document', exact: true })).toBeEnabled();
  await expect(page.locator('.document-preview')).not.toBeVisible();
  await expect(page.locator('.user-message .document-card')).toContainText('nutrition.pdf');
  expect(state.generations).toHaveLength(0);
  await page
    .getByLabel('Creation mode', { exact: true })
    .selectOption({ label: 'Export saved file without AI' });
  await expect(page.locator('#document-source-help')).toContainText('nutrition.pdf');
  await form.getByRole('button', { name: 'Create document', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Download nutrition-summary.pdf', exact: true }),
  ).toBeVisible();
  expect(state.uploads).toHaveLength(1);
  expect(state.generations).toEqual([
    { session_id: 101, output_format: 'pdf', mode: 'export', source_document_id: 201 },
  ]);
  expect(state.unexpected).toEqual([]);
});

test('keeps an imported file downloadable when its AI analysis is unavailable', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page, { unavailableAnalysis: true });
  await attachFile(page, sampleFile('docx', documentTypes[1][1]), 'picker');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(
    page.getByText('Saved nutrition.docx. Extracted text only; AI analysis is unavailable.', {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator('.document-preview')).not.toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download nutrition.docx', exact: true }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('nutrition.docx');
  expect(await download.failure()).toBeNull();
  expect(state.uploads).toHaveLength(1);
  expect(state.generations).toHaveLength(0);
  expect(state.unexpected).toEqual([]);
});

test.skip('keeps the draft after AI failure and lets the user explicitly export it without duplicate content', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page, { failFirstGeneration: true });
  await page.getByRole('button', { name: 'Create document', exact: true }).click();
  const text = 'Breakfast: oats and fruit. Lunch: rice and vegetables.';
  await page.getByLabel('Document instructions', { exact: true }).fill(text);
  const form = page.getByRole('form', { name: 'Create a document', exact: true });
  await form.getByRole('button', { name: 'Create document', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('AI document generation is unavailable');
  await expect(page.getByLabel('Document instructions', { exact: true })).toHaveValue(text);
  await expect(page.getByLabel('Creation mode', { exact: true })).toHaveValue('ai');
  await expect(form.getByRole('button', { name: 'Create document', exact: true })).toBeEnabled();
  await page.getByLabel('Creation mode', { exact: true }).selectOption('export');
  await expect(page.getByRole('alert')).not.toBeVisible();
  await expect(page.getByLabel('Text to export', { exact: true })).toHaveValue(text);
  await form.getByRole('button', { name: 'Create document', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Download nutrition-summary.pdf', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('alert')).not.toBeVisible();
  await expect(page.locator('.user-message')).toHaveCount(1);
  expect(state.generations).toEqual([
    { session_id: null, instruction: text, output_format: 'pdf' },
    { session_id: null, instruction: text, output_format: 'pdf', mode: 'export' },
  ]);
  expect(state.uploads).toHaveLength(0);
  expect(state.visionUploads).toHaveLength(0);
  expect(state.unexpected).toEqual([]);
});

test.skip('keeps attached files when export asks the user to remove them first', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page);
  await attachFile(page, sampleFile('pdf', 'application/pdf'), 'drop');
  await page.getByRole('button', { name: 'Create document', exact: true }).click();
  await page.getByLabel('Creation mode', { exact: true }).selectOption('export');
  await page.getByLabel('Text to export', { exact: true }).fill('Literal notes');
  await page
    .getByRole('form', { name: 'Create a document', exact: true })
    .getByRole('button', { name: 'Create document', exact: true })
    .click();
  await expect(page.getByRole('alert')).toContainText('Remove the attached file');
  await expect(page.locator('.document-preview')).toContainText('nutrition.pdf');
  expect(state.uploads).toHaveLength(0);
  expect(state.generations).toHaveLength(0);
  await page.getByRole('button', { name: 'Remove attached document', exact: true }).click();
  await page
    .getByRole('form', { name: 'Create a document', exact: true })
    .getByRole('button', { name: 'Create document', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Download nutrition-summary.pdf', exact: true }),
  ).toBeVisible();
  expect(state.uploads).toHaveLength(0);
  expect(state.generations).toHaveLength(1);
  expect(state.unexpected).toEqual([]);
});

test.skip('imports a staged document before generating from its returned session', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page);
  await attachFile(page, sampleFile('csv', 'text/csv'), 'drop');
  await page.getByRole('button', { name: 'Create document', exact: true }).click();
  await page
    .getByLabel('Document instructions', { exact: true })
    .fill('Create a report from my imported nutrition data.');
  await page
    .getByRole('form', { name: 'Create a document', exact: true })
    .getByRole('button', { name: 'Create document', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Download nutrition-summary.pdf', exact: true }),
  ).toBeVisible();
  expect(state.uploads).toHaveLength(1);
  expect(state.generations).toEqual([
    {
      session_id: 101,
      instruction: 'Create a report from my imported nutrition data.',
      output_format: 'pdf',
    },
  ]);
  await expect(page.locator('.document-preview')).not.toBeVisible();
  expect(state.unexpected).toEqual([]);
});

test('retains failed uploads and retries Send without duplicating the message', async ({
  page,
}) => {
  const state = await mockApiAndLogin(page, { failFirstUpload: true });
  await attachFile(page, sampleFile('xlsx', documentTypes[4][1]), 'picker');
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Summarize these nutrition totals.');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByRole('alert')).toHaveText(
    'Document processing is temporarily unavailable. Please try again.',
  );
  await expect(page.locator('.document-preview')).toContainText('nutrition.xlsx');
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(
    'Summarize these nutrition totals.',
  );
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(
    page.getByText('Imported nutrition.xlsx. Nutrition summary is ready.', { exact: true }),
  ).toBeVisible();
  await expect(page.locator('.user-message')).toHaveCount(1);
  await expect(page.getByRole('alert')).not.toBeVisible();
  await expect(page.locator('.document-preview')).not.toBeVisible();
  expect(state.uploads).toHaveLength(2);
  expect(state.unexpected).toEqual([]);
});

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test.skip(`document creator is readable and keyboard accessible on ${viewport.name}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const state = await mockApiAndLogin(page);
    await page.getByRole('button', { name: 'Create document', exact: true }).click();
    const form = page.getByRole('form', { name: 'Create a document', exact: true });
    await expect(form).toBeVisible();
    await expect(page.getByLabel('Document instructions', { exact: true })).toBeFocused();
    await page
      .getByLabel('Document instructions', { exact: true })
      .fill('Create a nutrition summary with clear headings and recommendations.');
    await page.keyboard.press('Tab');
    await expect(page.getByLabel('File format', { exact: true })).toBeFocused();
    const bounds = await form.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);
    const screenshot = testInfo.outputPath(`document-creator-${viewport.name}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach(`document-creator-${viewport.name}`, {
      path: screenshot,
      contentType: 'image/png',
    });
    await page.keyboard.press('Tab');
    await expect(form.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(form).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Create document', exact: true })).toBeFocused();
    expect(state.unexpected).toEqual([]);
  });
}
