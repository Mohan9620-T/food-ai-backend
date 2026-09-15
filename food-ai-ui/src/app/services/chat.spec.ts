import { PLATFORM_ID, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable, of } from 'rxjs';
import { vi } from 'vitest';

import { ChatService, ChatStreamError } from './chat';
import { AuthService, TokenResponse } from './auth';
import { ChatConversation } from '../models/chat';

interface TestChatSession {
  id: number;
  title: string;
  updated_at: string;
  messages: Array<{
    id?: number;
    sender: 'user' | 'bot';
    content: string;
    created_at: string;
  }>;
}

class AuthStub {
  readonly currentUserId = signal<string | null>('42');
  getToken(): string | null {
    return 'access-token';
  }
  refreshAccessToken(): Observable<TokenResponse> {
    return of({ access_token: 'new-token', refresh_token: 'refresh-token', token_type: 'bearer' });
  }
}

describe('ChatService session continuity', () => {
  let service: ChatService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useClass: AuthStub },
        { provide: PLATFORM_ID, useValue: 'server' },
      ],
    });
    service = TestBed.inject(ChatService);
    http = TestBed.inject(HttpTestingController);
    TestBed.flushEffects();
    http.expectOne((request) => request.url.endsWith('/chat/sessions')).flush([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    http.verify();
  });

  it('saves an attached PDF before planning Word and keeps the source through Build', () => {
    const id = service.getActiveConversationId()!;
    const instruction = 'can you create the word document on this content';
    service.addMessage({ sender: 'user', text: instruction }, id);
    const results: string[] = [];
    service
      .uploadDocument(
        new File(['pdf bytes'], 'sample-1.pdf', { type: 'application/pdf' }),
        instruction,
        id,
      )
      .subscribe((result) => results.push(result.response));
    const upload = http.expectOne((r) => r.url.endsWith('/chat/documents'));
    expect(upload.request.body.get('analyze')).toBe('false');
    upload.flush({
      session_id: 99,
      response: 'File saved',
      analysis_status: 'skipped',
      attachment: {
        id: 21,
        filename: 'sample-1.pdf',
        kind: 'uploaded',
        content_type: 'application/pdf',
        file_size: 9,
      },
    });
    expect(results).toEqual(['File saved']);
    expect(service.messages()[0].attachment?.id).toBe(21);
    expect(service.processingDocument()).toBe(true);
    const plan = http.expectOne((r) => r.url.endsWith('/automate'));
    expect(plan.request.body).toEqual({
      session_id: 99,
      instruction,
      confirm: false,
      source_document_id: 21,
    });
    plan.flush({
      session_id: 99,
      response: 'Review Word creation',
      plan_summary: 'Review Word creation',
      status: 'ready_for_review',
      attachments: [],
    });
    expect(service.messages().at(-1)?.automation?.sourceDocumentId).toBe(21);
    expect(service.hasPendingAutomation(id)).toBe(true);
    expect(service.processingDocument()).toBe(false);
    service.automateDocument(99, instruction, id, true).subscribe();
    const build = http.expectOne((r) => r.url.endsWith('/automate'));
    expect(build.request.body).toEqual({
      session_id: 99,
      instruction,
      confirm: true,
      source_document_id: 21,
    });
    build.flush({
      session_id: 99,
      response: 'Created Word',
      status: 'done',
      attachments: [
        {
          id: 22,
          filename: 'source.docx',
          kind: 'generated',
          content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          file_size: 100,
        },
      ],
    });
    expect(service.messages().at(-1)?.attachments?.[0].filename).toBe('source.docx');
  });

  it('keeps upload-and-read on the document reading endpoint', () => {
    const id = service.getActiveConversationId()!;
    service
      .uploadDocument(
        new File(['pdf'], 'sample.pdf'),
        'Read this PDF end to end and give me a summary',
        id,
      )
      .subscribe();
    const upload = http.expectOne((r) => r.url.endsWith('/chat/documents'));
    expect(upload.request.body.get('analyze')).toBeNull();
    upload.flush({
      session_id: 99,
      response: 'The full PDF describes Project Cedar',
      analysis_status: 'complete',
    });
    http.expectNone((r) => r.url.endsWith('/automate'));
    expect(service.messages().at(-1)?.text).toContain('Project Cedar');
  });

  it('uses the session_id returned by the first non-streaming response on the second message', () => {
    const conversationId = service.getActiveConversationId()!;
    const request = { message: 'First', history: [], referenceHistory: [] };

    service.sendMessage(request, conversationId).subscribe();
    const first = http.expectOne((candidate) => candidate.urlWithParams.endsWith('/chat/'));
    expect(first.request.params.has('session_id')).toBe(false);
    first.flush({ response: 'First answer', session_id: 73 });

    service.sendMessage({ ...request, message: 'Second' }, conversationId).subscribe();
    const second = http.expectOne((candidate) =>
      candidate.urlWithParams.endsWith('/chat/?session_id=73'),
    );
    expect(second.request.urlWithParams).toContain('session_id=73');
    second.flush({ response: 'Second answer', session_id: 73 });
  });

  it('creates a session before planning a source-free document request', () => {
    const conversationId = service.getActiveConversationId()!;
    service.automateDocument(null, 'Create an Excel file', conversationId).subscribe();
    http
      .expectOne((r) => r.method === 'POST' && r.url.endsWith('/chat/sessions'))
      .flush({ id: 99 });
    const plan = http.expectOne((r) => r.url.endsWith('/chat/documents/automate'));
    expect(plan.request.body).toEqual({
      session_id: 99,
      instruction: 'Create an Excel file',
      confirm: false,
    });
    plan.flush({ session_id: 99, response: 'Which scope?', status: 'clarification_required' });
    expect(service.getActiveSessionId()).toBe(99);
  });

  it('accumulates choices from ordinary answer turns and confirms the same instruction', () => {
    const id = service.getActiveConversationId()!;
    service.automateDocument(99, 'Make a brochure', id).subscribe();
    http
      .expectOne((r) => r.url.endsWith('/automate'))
      .flush({
        session_id: 99,
        status: 'clarification_required',
        response: 'Which format?',
        clarification: {
          question: 'Which format?',
          allow_other: true,
          options: [
            { id: 'word', label: 'Word', recommended: true },
            { id: 'pdf', label: 'PDF', recommended: false },
          ],
        },
      });
    expect(service.hasPendingAutomation(id)).toBe(true);
    service.automateDocument(99, 'Word', id).subscribe();
    const answer = http.expectOne((r) => r.url.endsWith('/automate'));
    expect(answer.request.body.confirm).toBe(false);
    answer.flush({
      session_id: 99,
      status: 'ready_for_review',
      response: 'Review',
      plan_summary: 'Create DOCX',
    });
    expect(service.messages().at(-1)?.automation?.choices).toEqual([
      { question: 'Which format?', answer: 'Word' },
    ]);
    service.automateDocument(99, 'Word', id, true).subscribe();
    const build = http.expectOne((r) => r.url.endsWith('/automate'));
    expect(build.request.body).toEqual({ session_id: 99, instruction: 'Word', confirm: true });
    build.flush({ session_id: 99, status: 'done', response: 'Created' });
    expect(service.hasPendingAutomation(id)).toBe(false);
  });

  it('keeps every generated automation attachment available for download', () => {
    const conversationId = service.getActiveConversationId()!;
    service.automateDocument(73, 'Create Excel and Word reports', conversationId, true).subscribe();
    const request = http.expectOne((candidate) =>
      candidate.url.endsWith('/chat/documents/automate'),
    );
    expect(request.request.body).toEqual({
      session_id: 73,
      instruction: 'Create Excel and Word reports',
      confirm: true,
    });
    request.flush({
      response: 'Done — both files are attached.',
      session_id: 73,
      status: 'done',
      latest_document_id: 12,
      attachments: [
        {
          id: 11,
          filename: 'data.xlsx',
          content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          file_size: 100,
          kind: 'generated',
        },
        {
          id: 12,
          filename: 'report.docx',
          content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          file_size: 200,
          kind: 'generated',
        },
      ],
      steps: [],
    });

    expect(
      service
        .messages()
        .at(-1)
        ?.attachments?.map((item) => item.filename),
    ).toEqual(['data.xlsx', 'report.docx']);
    expect(service.hasDocumentContext(conversationId)).toBe(true);
    expect(service.processingDocument()).toBe(false);
  });

  it('uses the first streaming session_id on the next streaming request', async () => {
    const originalFetch = globalThis.fetch;
    const requestedUrls: string[] = [];
    globalThis.fetch = async (input: RequestInfo | URL) => {
      requestedUrls.push(String(input));
      const sessionId = 91;
      const body = [
        JSON.stringify({ type: 'session', session_id: sessionId }),
        JSON.stringify({ type: 'token', content: 'Answer' }),
        JSON.stringify({ type: 'done' }),
        '',
      ].join('\n');
      return new Response(body, {
        status: 200,
        headers: { 'Content-Type': 'application/x-ndjson' },
      });
    };

    try {
      const conversationId = service.getActiveConversationId()!;
      const request = { message: 'First', history: [], referenceHistory: [] };
      await service.streamMessage(request, conversationId);
      await service.streamMessage({ ...request, message: 'Second' }, conversationId);
      expect(requestedUrls[0]).not.toContain('session_id=');
      expect(requestedUrls[1]).toContain('/chat/stream?session_id=91');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each(['\n', ''])(
    'surfaces a stream error with trailing separator %j and keeps partial text',
    async (separator) => {
      const detail =
        'The response reached its output limit before it finished. Please retry with a shorter request.';
      const body =
        [
          JSON.stringify({ type: 'session', session_id: 91 }),
          JSON.stringify({ type: 'token', content: 'Here is the first part.' }),
          JSON.stringify({ type: 'error', message: detail }),
        ].join('\n') + separator;
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));
      const conversationId = service.getActiveConversationId()!;

      await expect(
        service.streamMessage(
          { message: 'Explain', history: [], referenceHistory: [] },
          conversationId,
        ),
      ).rejects.toThrow(new ChatStreamError(detail, true));

      expect(service.messages().at(-1)?.text).toBe(
        `Here is the first part.\n\n> Response interrupted: ${detail}`,
      );
      expect(service.messages()).toHaveLength(1);
    },
  );

  it('accepts a final done event without a trailing newline across arbitrary network chunks', async () => {
    const body = [
      JSON.stringify({ type: 'session', session_id: 91 }),
      JSON.stringify({ type: 'token', content: 'Hello 😊' }),
      JSON.stringify({ type: 'done' }),
    ].join('\n');
    const bytes = new TextEncoder().encode(body);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream({
            start(controller) {
              // Single-byte chunks also split the emoji's UTF-8 sequence.
              for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
              controller.close();
            },
          }),
        ),
      ),
    );

    await service.streamMessage(
      { message: 'Hi', history: [], referenceHistory: [] },
      service.getActiveConversationId()!,
    );

    expect(service.messages().at(-1)?.text).toBe('Hello 😊');
  });

  it('replaces an empty assistant placeholder with an interruption notice', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            '{"type":"session","session_id":91}\n{"type":"error","message":"The model is unavailable. Please retry."}\n',
          ),
        ),
    );

    await expect(
      service.streamMessage(
        { message: 'Hi', history: [], referenceHistory: [] },
        service.getActiveConversationId()!,
      ),
    ).rejects.toThrow(new ChatStreamError('The model is unavailable. Please retry.', true));

    expect(service.messages().at(-1)?.text).toBe(
      'Response interrupted: The model is unavailable. Please retry.',
    );
    expect(service.messages()).toHaveLength(1);
  });

  it('reports an interrupted stream instead of accepting EOF as a completed response', async () => {
    const body = [
      JSON.stringify({ type: 'session', session_id: 91 }),
      JSON.stringify({ type: 'token', content: 'Unfinished answer' }),
    ].join('\n');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));

    await expect(
      service.streamMessage(
        { message: 'Explain', history: [], referenceHistory: [] },
        service.getActiveConversationId()!,
      ),
    ).rejects.toThrow('The connection ended before the response finished. Please retry.');

    expect(service.messages().at(-1)?.text).toBe(
      'Unfinished answer\n\n> Response interrupted: The connection ended before the response finished. Please retry.',
    );
  });

  it('finishes on done without waiting for the network connection to close', async () => {
    const cancel = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode(
                  '{"type":"session","session_id":91}\n{"type":"token","content":"Done."}\n{"type":"done"}\n',
                ),
              );
            },
            cancel,
          }),
        ),
      ),
    );

    await service.streamMessage(
      { message: 'Hi', history: [], referenceHistory: [] },
      service.getActiveConversationId()!,
    );

    expect(service.messages().at(-1)?.text).toBe('Done.');
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('cancels an idle stream as AbortError while keeping received text', async () => {
    const cancel = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode(
                  '{"type":"session","session_id":91}\n{"type":"token","content":"Partial text"}\n',
                ),
              );
            },
            cancel,
          }),
        ),
      ),
    );
    const stream = service.streamMessage(
      { message: 'Explain', history: [], referenceHistory: [] },
      service.getActiveConversationId()!,
    );
    const rejected = expect(stream).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(service.messages().at(-1)?.text).toBe('Partial text'));

    service.stopStreaming();

    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(service.messages().at(-1)?.text).toBe('Partial text');
  });

  it('attaches an image and sends it to the current backend session', () => {
    const conversationId = service.getActiveConversationId()!;
    const textRequest = { message: 'Start', history: [], referenceHistory: [] };
    service.sendMessage(textRequest, conversationId).subscribe();
    http
      .expectOne((candidate) => candidate.urlWithParams.endsWith('/chat/'))
      .flush({
        response: 'Ready',
        session_id: 64,
      });

    const image = new File([new Uint8Array([1, 2, 3])], 'photo.png', { type: 'image/png' });
    service.sendVisionMessage(image, 'What is shown?', conversationId).subscribe();
    expect(service.analyzingImage()).toBe(true);
    const request = http.expectOne((candidate) => candidate.url.endsWith('/chat/vision'));
    expect(request.request.method).toBe('POST');
    const form = request.request.body as FormData;
    expect((form.get('image') as File).name).toBe('photo.png');
    expect(form.get('message')).toBe('What is shown?');
    expect(form.get('session_id')).toBe('64');
    request.flush({ response: 'A landscape.', session_id: 64 });
    expect(service.analyzingImage()).toBe(false);
    expect(service.messages().at(-1)?.text).toBe('A landscape.');
  });

  it('shows image analysis only in the conversation that started it', () => {
    const imageConversationId = service.getActiveConversationId()!;
    const image = new File([new Uint8Array([1, 2, 3])], 'photo.png', { type: 'image/png' });

    service.sendVisionMessage(image, 'What is shown?', imageConversationId).subscribe();
    const request = http.expectOne((candidate) => candidate.url.endsWith('/chat/vision'));
    expect(service.analyzingImage()).toBe(true);

    service.createConversation();
    expect(service.analyzingImage()).toBe(false);

    service.selectConversation(imageConversationId);
    expect(service.analyzingImage()).toBe(true);

    request.flush({ response: 'A landscape.', session_id: 64 });
    expect(service.analyzingImage()).toBe(false);
  });

  it('uploads a document with the current session and restores attachment metadata', () => {
    const conversationId = service.getActiveConversationId()!;
    service
      .sendMessage({ message: 'Start', history: [], referenceHistory: [] }, conversationId)
      .subscribe();
    http
      .expectOne((candidate) => candidate.urlWithParams.endsWith('/chat/'))
      .flush({ response: 'Ready', session_id: 64 });
    const file = new File(['Calories: 1800'], 'notes.txt', { type: 'text/plain' });
    service.addMessage({ sender: 'user', text: 'Read this' }, conversationId);
    service.uploadDocument(file, 'Read this', conversationId).subscribe();
    expect(service.processingDocument()).toBe(true);
    const request = http.expectOne((candidate) => candidate.url.endsWith('/chat/documents'));
    const form = request.request.body as FormData;
    expect((form.get('file') as File).name).toBe('notes.txt');
    expect(form.get('session_id')).toBe('64');
    request.flush({
      response: 'Summary',
      session_id: 64,
      attachment: {
        id: 7,
        filename: 'notes.txt',
        content_type: 'text/plain',
        file_size: 14,
        kind: 'uploaded',
      },
    });
    expect(service.processingDocument()).toBe(false);
    expect(
      service
        .messages()
        .filter((message) => message.sender === 'user')
        .at(-1)?.attachment?.filename,
    ).toBe('notes.txt');
  });

  it('routes a deterministic spreadsheet follow-up to the spreadsheet endpoint', () => {
    const conversationId = service.getActiveConversationId()!;
    service
      .sendMessage({ message: 'Start', history: [], referenceHistory: [] }, conversationId)
      .subscribe();
    http
      .expectOne((candidate) => candidate.urlWithParams.endsWith('/chat/'))
      .flush({
        response: 'Ready',
        session_id: 64,
      });
    service.addMessage({ sender: 'user', text: 'Add a filter to Category*.' }, conversationId);

    service.updateSpreadsheet(64, 'Add a filter to Category*.', conversationId).subscribe();

    expect(service.processingDocument()).toBe(true);
    const request = http.expectOne((candidate) =>
      candidate.url.endsWith('/chat/documents/spreadsheet'),
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      session_id: 64,
      instruction: 'Add a filter to Category*.',
    });
    request.flush({
      response: 'Done. I added the requested column filter.',
      session_id: 64,
      attachment: {
        id: 9,
        filename: 'items_filter_updated.xlsx',
        content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        file_size: 100,
        kind: 'generated',
      },
    });
    expect(service.processingDocument()).toBe(false);
    expect(service.messages().at(-1)?.attachment?.filename).toBe('items_filter_updated.xlsx');
  });

  it.each([
    'Please add a filter only to the Category* column.',
    'Filter the Category column.',
    'Please enable filtering for the Category* column.',
    'Add a filter to `Item Category`.',
  ])('detects spreadsheet filter request: %s', (message) => {
    expect(service.isSpreadsheetOperationRequest(message)).toBe(true);
  });

  it.each([
    'Transform the Excel and generate a new Excel file.',
    'Create separate rows based on Regular, Easy to Chew, Soft & Bite, Minced & Moist and Pureed.',
    'Expand each dish row based on the five texture categories.',
    'Create one row for every category marked x.',
    'Split each dish into rows based on the five category columns.',
  ])('detects dish category row expansion request: %s', (message) => {
    expect(service.isSpreadsheetOperationRequest(message)).toBe(true);
  });

  it('requests a generated PDF document', () => {
    const conversationId = service.getActiveConversationId()!;
    service.generateDocument(12, 'Doctor summary', 'pdf', conversationId).subscribe();
    const request = http.expectOne((candidate) =>
      candidate.url.endsWith('/chat/documents/generate'),
    );
    expect(request.request.body).toEqual({
      session_id: 12,
      instruction: 'Doctor summary',
      output_format: 'pdf',
    });
    request.flush({
      response: 'Report',
      session_id: 12,
      attachment: {
        id: 8,
        filename: 'nutrition-document.pdf',
        content_type: 'application/pdf',
        file_size: 100,
        kind: 'generated',
      },
    });
    expect(service.messages().at(-1)?.attachment?.kind).toBe('generated');
  });

  it('restores a persisted image URL from backend session history', () => {
    const imageUrl = 'data:image/png;base64,AQID';
    const conversation = (
      service as unknown as {
        fromApiSession: (session: unknown) => { messages: Array<{ imageUrl?: string }> };
      }
    ).fromApiSession({
      id: 12,
      title: 'Image chat',
      updated_at: '2026-09-02T08:00:00Z',
      messages: [
        {
          sender: 'user',
          content: 'What is shown?',
          created_at: '2026-09-02T08:00:00Z',
          image_url: imageUrl,
        },
      ],
    });

    expect(conversation.messages[0].imageUrl).toBe(imageUrl);
  });

  it('allows retrying a stale persisted user message with no active response', () => {
    const conversationId = service.getActiveConversationId()!;
    service.addMessage({ sender: 'user', text: 'Already saved' }, conversationId);

    service.requestMessageRetry(0);

    expect(service.isMessageAwaitingResponse(0)).toBe(false);
    expect(service.consumeRetryMessage()).toEqual({ conversationId, text: 'Already saved' });
    expect(service.messages()).toHaveLength(1);
  });

  it('keeps retry disabled while the matching response request is active', () => {
    const conversationId = service.getActiveConversationId()!;
    const request = { message: 'Still processing', history: [], referenceHistory: [] };
    service.addMessage({ sender: 'user', text: request.message }, conversationId);
    service.startResponse(conversationId, request);

    service.requestMessageRetry(0);

    expect(service.isMessageAwaitingResponse(0)).toBe(true);
    expect(service.consumeRetryMessage()).toBeNull();
  });

  it('deletes a persisted user turn and its assistant response', () => {
    const internal = service as unknown as {
      setLoadedConversations: (conversations: ChatConversation[]) => void;
    };
    internal.setLoadedConversations([
      {
        id: '50',
        sessionId: 50,
        title: 'Delete turn',
        updatedAt: 1,
        messages: [
          { id: 80, sender: 'user', text: 'Remove this' },
          { id: 81, sender: 'bot', text: 'Remove this response' },
          { id: 82, sender: 'user', text: 'Keep this' },
        ],
      },
    ]);

    service.deleteMessage(0);
    const request = http.expectOne((candidate) =>
      candidate.url.endsWith('/chat/sessions/50/messages/80'),
    );
    expect(request.request.method).toBe('DELETE');
    request.flush({ detail: 'Chat turn deleted' });

    expect(service.messages().map((message) => message.text)).toEqual(['Keep this']);
  });

  it('includes standing preferences from another chat without keyword overlap', () => {
    const internal = service as unknown as {
      setLoadedConversations: (conversations: ChatConversation[]) => void;
    };
    internal.setLoadedConversations([
      { id: '50', sessionId: 50, title: 'Motivation', messages: [], updatedAt: 2 },
      {
        id: '49',
        sessionId: 49,
        title: 'Introduction',
        messages: [{ sender: 'user', text: 'Please call me Master in every chat.' }],
        updatedAt: 1,
      },
    ]);

    const context = service.getReferenceHistory('Give me some motivation', '50');

    expect(context).toContainEqual({
      role: 'user',
      content: 'Please call me Master in every chat.',
    });
  });

  it('does not carry a language preference into another chat', () => {
    const internal = service as unknown as {
      setLoadedConversations: (conversations: ChatConversation[]) => void;
    };
    internal.setLoadedConversations([
      { id: '50', sessionId: 50, title: 'Motivation', messages: [], updatedAt: 2 },
      {
        id: '49',
        sessionId: 49,
        title: 'Language preference',
        messages: [{ sender: 'user', text: 'I prefer Tanglish.' }],
        updatedAt: 1,
      },
    ]);

    const context = service.getReferenceHistory('Give me some motivation', '50');

    expect(context).not.toContainEqual({ role: 'user', content: 'I prefer Tanglish.' });
  });

  it('polls only recent pending sessions instead of reloading every conversation', () => {
    vi.useFakeTimers();
    const internal = service as unknown as {
      isBrowser: boolean;
      fromApiSession: (session: TestChatSession) => ChatConversation;
      setLoadedConversations: (conversations: ChatConversation[]) => void;
      schedulePendingHistoryRefresh: () => void;
    };
    internal.isBrowser = true;
    const now = new Date().toISOString();
    internal.setLoadedConversations([
      internal.fromApiSession({
        id: 31,
        title: 'Pending chat',
        updated_at: now,
        messages: [{ sender: 'user', content: 'Hello?', created_at: now }],
      }),
      internal.fromApiSession({
        id: 32,
        title: 'Completed chat',
        updated_at: now,
        messages: [
          { sender: 'user', content: 'Hi', created_at: now },
          { sender: 'bot', content: 'Hello', created_at: now },
        ],
      }),
    ]);

    internal.schedulePendingHistoryRefresh();
    vi.advanceTimersByTime(2000);

    http.expectNone((request) => request.url === 'http://localhost:8000/chat/sessions');
    http.expectNone((request) => request.url.endsWith('/chat/sessions/32'));
    http
      .expectOne((request) => request.url.endsWith('/chat/sessions/31'))
      .flush({
        id: 31,
        title: 'Pending chat',
        updated_at: now,
        messages: [
          { sender: 'user', content: 'Hello?', created_at: now },
          { sender: 'bot', content: 'Hello!', created_at: now },
        ],
      });
    vi.advanceTimersByTime(2000);
    vi.useRealTimers();
  });

  it('restores the selected server chat instead of always opening the first chat', () => {
    const internal = service as unknown as {
      isBrowser: boolean;
      setLoadedConversations: (
        conversations: Array<{
          id: string;
          sessionId: number;
          title: string;
          messages: [];
          updatedAt: number;
        }>,
      ) => void;
    };
    internal.isBrowser = true;
    localStorage.setItem('food-ai-active-session-v2:42', '22');

    internal.setLoadedConversations([
      { id: '11', sessionId: 11, title: 'Chat 1', messages: [], updatedAt: 2 },
      { id: '22', sessionId: 22, title: 'Chat 2', messages: [], updatedAt: 1 },
    ]);

    expect(service.getActiveConversationId()).toBe('22');
    localStorage.removeItem('food-ai-active-session-v2:42');
  });
});
