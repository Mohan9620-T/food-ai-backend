import { PLATFORM_ID, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable, of } from 'rxjs';
import { vi } from 'vitest';

import { ChatService } from './chat';
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
  getToken(): string | null { return 'access-token'; }
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
        { provide: PLATFORM_ID, useValue: 'server' }
      ]
    });
    service = TestBed.inject(ChatService);
    http = TestBed.inject(HttpTestingController);
    TestBed.flushEffects();
    http.expectOne((request) => request.url.endsWith('/chat/sessions')).flush([]);
  });

  afterEach(() => http.verify());

  it('uses the session_id returned by the first non-streaming response on the second message', () => {
    const conversationId = service.getActiveConversationId()!;
    const request = { message: 'First', history: [], referenceHistory: [] };

    service.sendMessage(request, conversationId).subscribe();
    const first = http.expectOne((candidate) => candidate.urlWithParams.endsWith('/chat/'));
    expect(first.request.params.has('session_id')).toBe(false);
    first.flush({ response: 'First answer', session_id: 73 });

    service.sendMessage({ ...request, message: 'Second' }, conversationId).subscribe();
    const second = http.expectOne((candidate) => candidate.urlWithParams.endsWith('/chat/?session_id=73'));
    expect(second.request.urlWithParams).toContain('session_id=73');
    second.flush({ response: 'Second answer', session_id: 73 });
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
        ''
      ].join('\n');
      return new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } });
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

  it('attaches an image and sends it to the current backend session', () => {
    const conversationId = service.getActiveConversationId()!;
    const textRequest = { message: 'Start', history: [], referenceHistory: [] };
    service.sendMessage(textRequest, conversationId).subscribe();
    http.expectOne((candidate) => candidate.urlWithParams.endsWith('/chat/')).flush({
      response: 'Ready', session_id: 64
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

  it('restores a persisted image URL from backend session history', () => {
    const imageUrl = 'data:image/png;base64,AQID';
    const conversation = (service as unknown as {
      fromApiSession: (session: unknown) => { messages: Array<{ imageUrl?: string }> };
    }).fromApiSession({
      id: 12,
      title: 'Image chat',
      updated_at: '2026-09-02T08:00:00Z',
      messages: [{
        sender: 'user',
        content: 'What is shown?',
        created_at: '2026-09-02T08:00:00Z',
        image_url: imageUrl
      }]
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
    internal.setLoadedConversations([{
      id: '50',
      sessionId: 50,
      title: 'Delete turn',
      updatedAt: 1,
      messages: [
        { id: 80, sender: 'user', text: 'Remove this' },
        { id: 81, sender: 'bot', text: 'Remove this response' },
        { id: 82, sender: 'user', text: 'Keep this' }
      ]
    }]);

    service.deleteMessage(0);
    const request = http.expectOne((candidate) => candidate.url.endsWith('/chat/sessions/50/messages/80'));
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
        updatedAt: 1
      }
    ]);

    const context = service.getReferenceHistory('Give me some motivation', '50');

    expect(context).toContainEqual({
      role: 'user',
      content: 'Please call me Master in every chat.'
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
        updatedAt: 1
      }
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
        messages: [{ sender: 'user', content: 'Hello?', created_at: now }]
      }),
      internal.fromApiSession({
        id: 32,
        title: 'Completed chat',
        updated_at: now,
        messages: [
          { sender: 'user', content: 'Hi', created_at: now },
          { sender: 'bot', content: 'Hello', created_at: now }
        ]
      })
    ]);

    internal.schedulePendingHistoryRefresh();
    vi.advanceTimersByTime(2000);

    http.expectNone((request) => request.url === 'http://localhost:8000/chat/sessions');
    http.expectNone((request) => request.url.endsWith('/chat/sessions/32'));
    http.expectOne((request) => request.url.endsWith('/chat/sessions/31')).flush({
      id: 31,
      title: 'Pending chat',
      updated_at: now,
      messages: [
        { sender: 'user', content: 'Hello?', created_at: now },
        { sender: 'bot', content: 'Hello!', created_at: now }
      ]
    });
    vi.advanceTimersByTime(2000);
    vi.useRealTimers();
  });

  it('restores the selected server chat instead of always opening the first chat', () => {
    const internal = service as unknown as {
      isBrowser: boolean;
      setLoadedConversations: (conversations: Array<{
        id: string;
        sessionId: number;
        title: string;
        messages: [];
        updatedAt: number;
      }>) => void;
    };
    internal.isBrowser = true;
    localStorage.setItem('food-ai-active-session-v2:42', '22');

    internal.setLoadedConversations([
      { id: '11', sessionId: 11, title: 'Chat 1', messages: [], updatedAt: 2 },
      { id: '22', sessionId: 22, title: 'Chat 2', messages: [], updatedAt: 1 }
    ]);

    expect(service.getActiveConversationId()).toBe('22');
    localStorage.removeItem('food-ai-active-session-v2:42');
  });
});
