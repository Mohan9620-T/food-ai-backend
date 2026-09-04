import { computed, effect, inject, Injectable, PLATFORM_ID, signal, untracked } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { isPlatformBrowser } from '@angular/common';
import {
  catchError,
  concatMap,
  finalize,
  forkJoin,
  from,
  map,
  Observable,
  of,
  switchMap,
  tap,
  toArray,
  firstValueFrom
} from 'rxjs';
import {
  ChatConversation,
  ChatHistoryMessage,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  PendingChatResponse
} from '../models/chat';
import { environment } from '../../environments/environment';
import { AuthService } from './auth';

interface ChatSessionSummaryApi {
  id: number;
  title: string;
  updated_at: string;
}

interface ChatMessageApi {
  sender: 'user' | 'bot';
  content: string;
  created_at: string;
  image_url?: string | null;
}

interface ChatSessionDetailApi extends ChatSessionSummaryApi {
  messages: ChatMessageApi[];
}

interface LegacyConversation {
  title: string;
  messages: ChatMessage[];
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly chatUrl = `${environment.apiUrl}/chat/`;
  private readonly sessionsUrl = `${environment.apiUrl}/chat/sessions`;
  private readonly legacyStorageKeyPrefix = 'food-ai-chat-conversations';
  private readonly legacyActiveStorageKeyPrefix = 'food-ai-active-chat';
  private readonly legacyPendingStorageKeyPrefix = 'food-ai-pending-response';
  private readonly activeStorageKeyPrefix = 'food-ai-active-session-v2';
  private readonly migrationFlagPrefix = 'food-ai-migrated-v1';
  private readonly consolidationFlagPrefix = 'food-ai-sessions-consolidated-single-v3';
  private readonly pendingHistoryMaxAgeMs = 15 * 60 * 1000;

  private readonly http = inject(HttpClient);
  private readonly authService = inject(AuthService);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));

  private readonly conversationsState = signal<ChatConversation[]>([]);
  private readonly activeConversationIdState = signal<string | null>(null);
  private readonly pendingConversationIdState = signal<string | null>(null);
  private readonly pendingResponseState = signal<PendingChatResponse | null>(null);
  private readonly editingMessageState = signal<{ conversationId: string; index: number; text: string } | null>(null);
  private readonly retryMessageState = signal<{ conversationId: string; text: string } | null>(null);
  private readonly migrationNoticeState = signal<string | null>(null);
  private readonly loadingSessionsState = signal(false);
  private readonly analyzingImageConversationIdsState = signal<ReadonlySet<string>>(new Set());
  private streamAbortController: AbortController | null = null;
  private pendingHistoryTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingHistoryRefreshes = 0;

  readonly conversations = this.conversationsState.asReadonly();
  readonly activeConversationId = this.activeConversationIdState.asReadonly();
  readonly messages = computed(() => this.getActiveConversation()?.messages ?? []);
  readonly editingMessage = this.editingMessageState.asReadonly();
  readonly retryMessage = this.retryMessageState.asReadonly();
  readonly migrationNotice = this.migrationNoticeState.asReadonly();
  readonly loadingSessions = this.loadingSessionsState.asReadonly();
  readonly analyzingImage = computed(() => {
    const conversationId = this.activeConversationIdState();
    return conversationId !== null && this.analyzingImageConversationIdsState().has(conversationId);
  });
  readonly isResponding = computed(() => {
    const pendingConversationId = this.pendingConversationIdState();
    return pendingConversationId !== null && pendingConversationId === this.activeConversationIdState();
  });

  constructor() {
    effect((onCleanup) => {
      const userId = this.authService.currentUserId();
      this.resetUserState();
      if (!userId) return;
      this.loadingSessionsState.set(true);

      const subscription = this.migrateLegacyConversations(userId).pipe(
        switchMap(() => this.consolidateExistingSessions(userId)),
        switchMap(() => this.fetchConversations()),
        finalize(() => this.loadingSessionsState.set(false))
      ).subscribe({
        next: (conversations) => {
          this.setLoadedConversations(conversations);
          this.schedulePendingHistoryRefresh();
        },
        error: () => {
          this.migrationNoticeState.set('Chats could not be loaded. Please check the backend connection and retry.');
          this.ensureDraftConversation();
        }
      });
      onCleanup(() => subscription.unsubscribe());
    });
  }

  sendMessage(data: ChatRequest, conversationId = this.activeConversationIdState()): Observable<ChatResponse> {
    const sessionId = this.toServerSessionId(conversationId);
    const url = sessionId === null ? this.chatUrl : `${this.chatUrl}?session_id=${sessionId}`;
    return this.http.post<ChatResponse>(url, {
      message: data.message,
      history: data.history,
      reference_history: data.referenceHistory
    }).pipe(tap((response) => {
      if (conversationId) this.acceptResponse(conversationId, response);
    }));
  }

  sendVisionMessage(
    image: File,
    message: string | null,
    conversationId = this.activeConversationIdState()
  ): Observable<ChatResponse> {
    const form = new FormData();
    form.append('image', image, image.name);
    const text = message?.trim();
    if (text) form.append('message', text);
    const sessionId = this.toServerSessionId(conversationId);
    if (sessionId !== null) form.append('session_id', String(sessionId));
    if (conversationId) {
      this.analyzingImageConversationIdsState.update((conversationIds) =>
        new Set(conversationIds).add(conversationId)
      );
    }
    return this.http.post<ChatResponse>(`${environment.apiUrl}/chat/vision`, form).pipe(
      tap((response) => {
        if (conversationId) this.acceptResponse(conversationId, response);
      }),
      finalize(() => {
        if (conversationId) {
          this.analyzingImageConversationIdsState.update((conversationIds) => {
            const updatedIds = new Set(conversationIds);
            updatedIds.delete(conversationId);
            return updatedIds;
          });
        }
      })
    );
  }

  async streamMessage(data: ChatRequest, conversationId: string): Promise<void> {
    this.streamAbortController?.abort();
    const controller = new AbortController();
    this.streamAbortController = controller;

    try {
      let response = await this.openStream(data, conversationId, controller.signal);
      if (response.status === 401 && !controller.signal.aborted) {
        await firstValueFrom(this.authService.refreshAccessToken());
        response = await this.openStream(data, conversationId, controller.signal);
      }
      if (!response.ok || !response.body) {
        throw new Error(`Chat stream failed with status ${response.status}`);
      }
      await this.consumeStream(response.body, conversationId, controller.signal);
    } finally {
      if (this.streamAbortController === controller) this.streamAbortController = null;
    }
  }

  stopStreaming(): void {
    this.streamAbortController?.abort();
  }

  acceptResponse(conversationId: string, response: ChatResponse): void {
    this.conversationsState.update((conversations) => conversations.map((conversation) => {
      if (conversation.id !== conversationId) return conversation;
      return {
        ...conversation,
        sessionId: response.session_id,
        messages: [...conversation.messages, { sender: 'bot' as const, text: response.response, createdAt: new Date().toISOString() }],
        updatedAt: Date.now()
      };
    }).sort((first, second) => second.updatedAt - first.updatedAt));
    this.persistActiveConversation();
  }

  private acceptStreamSession(conversationId: string, sessionId: number): string {
    this.conversationsState.update((conversations) => conversations.map((conversation) =>
      conversation.id === conversationId
        ? {
            ...conversation,
            sessionId,
            messages: [...conversation.messages, { sender: 'bot' as const, text: '', createdAt: new Date().toISOString() }],
            updatedAt: Date.now()
          }
        : conversation
    ));
    this.persistActiveConversation();
    return conversationId;
  }

  private appendStreamChunk(conversationId: string, chunk: string): void {
    this.conversationsState.update((conversations) => conversations.map((conversation) => {
      if (conversation.id !== conversationId) return conversation;
      const messages = [...conversation.messages];
      const lastIndex = messages.length - 1;
      const lastMessage = messages[lastIndex];
      if (lastMessage?.sender === 'bot') {
        messages[lastIndex] = { ...lastMessage, text: lastMessage.text + chunk };
      }
      return { ...conversation, messages, updatedAt: Date.now() };
    }));
  }

  private async openStream(
    data: ChatRequest,
    conversationId: string,
    signal: AbortSignal
  ): Promise<Response> {
    const sessionId = this.toServerSessionId(conversationId);
    const query = sessionId === null ? '' : `?session_id=${sessionId}`;
    const token = this.authService.getToken();
    return fetch(`${environment.apiUrl}/chat/stream${query}`, {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      body: JSON.stringify({
        message: data.message,
        history: data.history,
        reference_history: data.referenceHistory
      })
    });
  }

  private async consumeStream(
    body: ReadableStream<Uint8Array>,
    draftConversationId: string,
    signal: AbortSignal
  ): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let conversationId = draftConversationId;
    try {
      while (!signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line) as {
            type: 'session' | 'token' | 'done';
            session_id?: number;
            content?: string;
          };
          if (event.type === 'session' && event.session_id !== undefined) {
            conversationId = this.acceptStreamSession(conversationId, event.session_id);
          } else if (event.type === 'token' && event.content) {
            this.appendStreamChunk(conversationId, event.content);
          }
        }
      }
    } finally {
      if (signal.aborted) await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  }

  startResponse(conversationId: string, request: ChatRequest): void {
    this.pendingConversationIdState.set(conversationId);
    this.pendingResponseState.set({ conversationId, request });
  }

  finishResponse(conversationId: string): void {
    if (this.pendingResponseState()?.conversationId === conversationId) {
      this.pendingConversationIdState.set(null);
      this.pendingResponseState.set(null);
    }
  }

  getPendingResponse(): PendingChatResponse | null {
    return this.pendingResponseState();
  }

  createConversation(): void {
    const conversation = this.createDraftConversation();
    this.conversationsState.update((conversations) => [conversation, ...conversations]);
    this.activeConversationIdState.set(conversation.id);
    this.persistActiveConversation();
    this.editingMessageState.set(null);
  }

  selectConversation(conversationId: string): void {
    if (this.conversationsState().some((conversation) => conversation.id === conversationId)) {
      this.editingMessageState.set(null);
      this.activeConversationIdState.set(conversationId);
      this.persistActiveConversation();
    }
  }

  addMessage(message: ChatMessage, conversationId = this.activeConversationIdState()): void {
    if (!conversationId) return;
    this.conversationsState.update((conversations) => conversations.map((conversation) => {
      if (conversation.id !== conversationId) return conversation;
      return {
        ...conversation,
        title: conversation.messages.length === 0 && message.sender === 'user'
          ? this.toTitle(message.text)
          : conversation.title,
        messages: [...conversation.messages, { ...message, createdAt: message.createdAt ?? new Date().toISOString() }],
        updatedAt: Date.now()
      };
    }).sort((first, second) => second.updatedAt - first.updatedAt));
  }

  beginEditingMessage(index: number): void {
    const conversation = this.getActiveConversation();
    const message = conversation?.messages[index];
    if (!conversation || !message || message.sender !== 'user' || this.isResponding()) return;
    this.editingMessageState.set({ conversationId: conversation.id, index, text: message.text });
  }

  requestMessageRetry(index: number): void {
    const conversation = this.getActiveConversation();
    const message = conversation?.messages[index];
    if (
      !conversation ||
      !message ||
      message.sender !== 'user' ||
      this.isResponding() ||
      this.isMessageAwaitingResponse(index)
    ) return;
    this.conversationsState.update((conversations) => conversations.map((item) =>
      item.id === conversation.id
        ? { ...item, messages: item.messages.slice(0, index + 1), updatedAt: Date.now() }
        : item
    ).sort((first, second) => second.updatedAt - first.updatedAt));
    this.retryMessageState.set({ conversationId: conversation.id, text: message.text });
  }

  consumeRetryMessage(): { conversationId: string; text: string } | null {
    const retryMessage = this.retryMessageState();
    this.retryMessageState.set(null);
    return retryMessage;
  }

  replaceEditingMessage(text: string): string | null {
    const editingMessage = this.editingMessageState();
    if (!editingMessage) return null;
    let updated = false;
    this.conversationsState.update((conversations) => conversations.map((conversation) => {
      if (conversation.id !== editingMessage.conversationId) return conversation;
      const originalMessage = conversation.messages[editingMessage.index];
      if (!originalMessage || originalMessage.sender !== 'user') return conversation;
      updated = true;
      return {
        ...conversation,
        messages: [
          ...conversation.messages.slice(0, editingMessage.index),
          { sender: 'user' as const, text, createdAt: originalMessage.createdAt }
        ],
        updatedAt: Date.now()
      };
    }).sort((first, second) => second.updatedAt - first.updatedAt));
    this.editingMessageState.set(null);
    return updated ? editingMessage.conversationId : null;
  }

  getActiveConversationId(): string | null {
    return this.activeConversationIdState();
  }

  getHistory(conversationId: string): ChatHistoryMessage[] {
    const conversation = this.conversationsState().find((item) => item.id === conversationId);
    return (conversation?.messages ?? []).map((message) => ({
      role: message.sender === 'user' ? 'user' : 'assistant',
      content: message.text
    }));
  }

  getReferenceHistory(query: string, activeConversationId: string): ChatHistoryMessage[] {
    const keywords = query.toLowerCase().match(/[a-z0-9]+/g)?.filter((word) => word.length > 2) ?? [];
    if (keywords.length === 0) return [];
    return this.conversationsState()
      .filter((conversation) => conversation.id !== activeConversationId)
      .map((conversation) => ({
        conversation,
        score: keywords.reduce((total, keyword) => {
          const text = `${conversation.title} ${conversation.messages.map((message) => message.text).join(' ')}`;
          return total + (text.toLowerCase().includes(keyword) ? 1 : 0);
        }, 0)
      }))
      .filter(({ score }) => score > 0)
      .sort((first, second) => second.score - first.score || second.conversation.updatedAt - first.conversation.updatedAt)
      .slice(0, 2)
      .flatMap(({ conversation }) => conversation.messages.slice(-8))
      .map((message) => ({
        role: message.sender === 'user' ? 'user' : 'assistant',
        content: message.text
      }));
  }

  renameConversation(conversationId: string, title: string): void {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) return;
    const sessionId = this.toServerSessionId(conversationId);
    if (sessionId === null) {
      this.updateConversationTitle(conversationId, trimmedTitle);
      return;
    }
    this.http.put<ChatSessionSummaryApi>(`${this.sessionsUrl}/${sessionId}`, {
      title: trimmedTitle
    }).subscribe({
      next: (session) => this.updateConversationTitle(String(session.id), session.title),
      error: () => this.migrationNoticeState.set('The chat could not be renamed. Please try again.')
    });
  }

  deleteConversation(conversationId: string): void {
    const sessionId = this.toServerSessionId(conversationId);
    if (sessionId === null) {
      this.removeConversation(conversationId);
      return;
    }
    this.http.delete(`${this.sessionsUrl}/${sessionId}`).subscribe({
      next: () => this.removeConversation(conversationId),
      error: () => this.migrationNoticeState.set('The chat could not be deleted. Please try again.')
    });
  }

  clearMigrationNotice(): void {
    this.migrationNoticeState.set(null);
    this.loadingSessionsState.set(false);
  }

  private fetchConversations(): Observable<ChatConversation[]> {
    return this.http.get<ChatSessionSummaryApi[]>(this.sessionsUrl).pipe(
      switchMap((sessions) => sessions.length === 0
        ? of([])
        : forkJoin(sessions.map((session) =>
          this.http.get<ChatSessionDetailApi>(`${this.sessionsUrl}/${session.id}`)
        ))
      ),
      map((sessions) => sessions.map((session) => this.fromApiSession(session)))
    );
  }

  private consolidateExistingSessions(userId: string): Observable<void> {
    if (!this.isBrowser) return of(undefined);
    const flagKey = `${this.consolidationFlagPrefix}:${userId}`;
    if (localStorage.getItem(flagKey)) return of(undefined);
    return this.http.post<ChatSessionSummaryApi[]>(
      `${this.sessionsUrl}/consolidate`,
      {}
    ).pipe(
      tap(() => localStorage.setItem(flagKey, 'true')),
      map(() => undefined),
      catchError(() => {
        this.migrationNoticeState.set(
          'Old chats could not be combined. They are still saved and consolidation will retry next login.'
        );
        return of(undefined);
      })
    );
  }

  private migrateLegacyConversations(userId: string): Observable<void> {
    if (!this.isBrowser) return of(undefined);
    const migrationFlagKey = `${this.migrationFlagPrefix}:${userId}`;
    const conversationsKey = `${this.legacyStorageKeyPrefix}:${userId}`;
    try {
      if (localStorage.getItem(migrationFlagKey)) return of(undefined);
      const saved = localStorage.getItem(conversationsKey);
      if (!saved) {
        localStorage.setItem(migrationFlagKey, 'true');
        return of(undefined);
      }

      const conversations = JSON.parse(saved) as LegacyConversation[];
      if (!Array.isArray(conversations)) throw new Error('Invalid legacy chat data');
      const createdSessionIds: number[] = [];
      return from(conversations).pipe(
        concatMap((conversation) => this.http.post<ChatSessionSummaryApi>(this.sessionsUrl, {
          title: conversation.title || 'New chat'
        }).pipe(
          tap((session) => createdSessionIds.push(session.id)),
          switchMap((session) => this.http.post<ChatSessionDetailApi>(
            `${this.sessionsUrl}/${session.id}/import`,
            {
              messages: (conversation.messages ?? []).map((message) => ({
                sender: message.sender,
                content: message.text
              }))
            }
          ))
        )),
        toArray(),
        tap(() => {
          localStorage.setItem(migrationFlagKey, 'true');
          localStorage.removeItem(conversationsKey);
          localStorage.removeItem(`${this.legacyActiveStorageKeyPrefix}:${userId}`);
          localStorage.removeItem(`${this.legacyPendingStorageKeyPrefix}:${userId}`);
        }),
        map(() => undefined),
        catchError(() => this.rollbackMigratedSessions(createdSessionIds).pipe(
          tap(() => this.setMigrationFailureNotice()),
          map(() => undefined),
          catchError(() => {
            this.setMigrationFailureNotice();
            return of(undefined);
          })
        ))
      );
    } catch {
      this.setMigrationFailureNotice();
      return of(undefined);
    }
  }

  private rollbackMigratedSessions(sessionIds: number[]): Observable<unknown[]> {
    if (sessionIds.length === 0) return of([]);
    return forkJoin(sessionIds.map((sessionId) =>
      this.http.delete(`${this.sessionsUrl}/${sessionId}`).pipe(catchError(() => of(null)))
    ));
  }

  private setMigrationFailureNotice(): void {
    this.migrationNoticeState.set(
      'Your old chats are still saved locally. Migration will retry the next time you log in.'
    );
  }

  private setLoadedConversations(conversations: ChatConversation[]): void {
    this.conversationsState.set(conversations);
    const savedSessionId = this.getSavedActiveSessionId();
    const restored = savedSessionId
      ? conversations.find(conversation => String(conversation.sessionId) === savedSessionId)
      : undefined;
    this.activeConversationIdState.set(restored?.id ?? conversations[0]?.id ?? null);
    this.ensureDraftConversation();
    this.persistActiveConversation();
  }

  private getSavedActiveSessionId(): string | null {
    if (!this.isBrowser) return null;
    const userId = this.authService.currentUserId();
    return userId ? localStorage.getItem(`${this.activeStorageKeyPrefix}:${userId}`) : null;
  }

  private persistActiveConversation(): void {
    if (!this.isBrowser) return;
    const userId = this.authService.currentUserId();
    if (!userId) return;
    const key = `${this.activeStorageKeyPrefix}:${userId}`;
    const active = this.getActiveConversation();
    if (active?.sessionId !== undefined) localStorage.setItem(key, String(active.sessionId));
    else localStorage.removeItem(key);
  }

  isMessageAwaitingResponse(index: number): boolean {
    const conversation = this.getActiveConversation();
    const pendingResponse = this.pendingResponseState();
    return Boolean(
      conversation &&
      pendingResponse?.conversationId === conversation.id &&
      conversation.messages[index]?.sender === 'user' &&
      !conversation.messages[index + 1]
    );
  }

  private schedulePendingHistoryRefresh(): void {
    if (!this.isBrowser || this.pendingHistoryTimer || this.pendingHistoryRefreshes >= 150) {
      return;
    }
    const pendingConversations = this.conversationsState().filter(
      conversation =>
        conversation.sessionId !== undefined &&
        conversation.messages.at(-1)?.sender === 'user' &&
        Date.now() - conversation.updatedAt <= this.pendingHistoryMaxAgeMs
    );
    if (pendingConversations.length === 0) {
      this.pendingHistoryRefreshes = 0;
      return;
    }

    this.pendingHistoryTimer = setTimeout(() => {
      this.pendingHistoryTimer = null;
      this.pendingHistoryRefreshes += 1;
      forkJoin(pendingConversations.map((conversation) =>
        this.http.get<ChatSessionDetailApi>(`${this.sessionsUrl}/${conversation.sessionId}`)
      )).pipe(
        map((sessions) => sessions.map((session) => this.fromApiSession(session)))
      ).subscribe({
        next: refreshedConversations => {
          const refreshedById = new Map(
            refreshedConversations.map((conversation) => [conversation.id, conversation])
          );
          this.conversationsState.update((conversations) => conversations.map(
            conversation => refreshedById.get(conversation.id) ?? conversation
          ));
          this.schedulePendingHistoryRefresh();
        },
        error: () => this.schedulePendingHistoryRefresh()
      });
    }, 2000);
  }

  private ensureDraftConversation(): void {
    if (this.conversationsState().length > 0) return;
    const draft = this.createDraftConversation();
    this.conversationsState.set([draft]);
    this.activeConversationIdState.set(draft.id);
  }

  private createDraftConversation(): ChatConversation {
    return {
      id: `draft:${crypto.randomUUID()}`,
      title: 'New chat',
      messages: [],
      updatedAt: Date.now()
    };
  }

  private fromApiSession(session: ChatSessionDetailApi): ChatConversation {
    return {
      id: String(session.id),
      sessionId: session.id,
      title: session.title,
      messages: session.messages.map((message) => ({
        sender: message.sender,
        text: message.content,
        createdAt: message.created_at,
        imageUrl: message.image_url ?? undefined
      })),
      updatedAt: Date.parse(session.updated_at)
    };
  }

  private resetUserState(): void {
    if (this.pendingHistoryTimer) clearTimeout(this.pendingHistoryTimer);
    this.pendingHistoryTimer = null;
    this.pendingHistoryRefreshes = 0;
    const existingConversations = untracked(() => this.conversationsState());
    for (const conversation of existingConversations) {
      for (const message of conversation.messages) {
        if (message.imageUrl?.startsWith('blob:')) URL.revokeObjectURL(message.imageUrl);
      }
    }
    this.conversationsState.set([]);
    this.activeConversationIdState.set(null);
    this.pendingConversationIdState.set(null);
    this.pendingResponseState.set(null);
    this.editingMessageState.set(null);
    this.retryMessageState.set(null);
    this.migrationNoticeState.set(null);
    this.analyzingImageConversationIdsState.set(new Set());
  }

  private updateConversationTitle(conversationId: string, title: string): void {
    this.conversationsState.update((conversations) => conversations.map((conversation) =>
      conversation.id === conversationId ? { ...conversation, title } : conversation
    ));
  }

  private removeConversation(conversationId: string): void {
    const conversations = this.conversationsState().filter((conversation) => conversation.id !== conversationId);
    this.conversationsState.set(conversations);
    if (this.pendingResponseState()?.conversationId === conversationId) {
      this.pendingConversationIdState.set(null);
      this.pendingResponseState.set(null);
    }
    if (this.activeConversationIdState() === conversationId) {
      this.activeConversationIdState.set(conversations[0]?.id ?? null);
    }
    this.ensureDraftConversation();
    this.persistActiveConversation();
  }

  private getActiveConversation(): ChatConversation | undefined {
    return this.conversationsState().find((item) => item.id === this.activeConversationIdState());
  }

  private toServerSessionId(conversationId: string | null): number | null {
    if (!conversationId) return null;
    const conversation = this.conversationsState().find((item) => item.id === conversationId);
    if (conversation?.sessionId !== undefined) return conversation.sessionId;
    return /^\d+$/.test(conversationId) ? Number(conversationId) : null;
  }

  private toTitle(message: string): string {
    return message.length > 60 ? `${message.slice(0, 57)}...` : message;
  }
}
