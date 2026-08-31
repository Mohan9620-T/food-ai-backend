import { PLATFORM_ID, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable, of } from 'rxjs';

import { ChatService } from './chat';
import { AuthService, TokenResponse } from './auth';

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
});
