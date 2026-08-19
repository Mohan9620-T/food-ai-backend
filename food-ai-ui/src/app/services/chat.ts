import { computed, effect, inject, Injectable, PLATFORM_ID, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { isPlatformBrowser } from '@angular/common';
import { Observable } from 'rxjs';
import { ChatConversation, ChatHistoryMessage, ChatMessage, ChatRequest, ChatResponse, PendingChatResponse } from '../models/chat';
import { environment } from '../../environments/environment';
import { AuthService } from './auth';

@Injectable({
  providedIn: 'root'
})
export class ChatService {

  private readonly apiUrl = `${environment.apiUrl}/chat/`;
  private readonly storageKeyPrefix = 'food-ai-chat-conversations';
  private readonly activeConversationStorageKeyPrefix = 'food-ai-active-chat';
  private readonly pendingResponseStorageKeyPrefix = 'food-ai-pending-response';

  private readonly http = inject(HttpClient);
  private readonly authService = inject(AuthService);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));

  private readonly conversationsState = signal<ChatConversation[]>([]);
  private readonly activeConversationIdState = signal<string | null>(null);
  private readonly pendingConversationIdState = signal<string | null>(null);
  private readonly pendingResponseState = signal<PendingChatResponse | null>(null);
  private readonly editingMessageState = signal<{ conversationId: string; index: number; text: string } | null>(null);

  readonly conversations = this.conversationsState.asReadonly();
  readonly activeConversationId = this.activeConversationIdState.asReadonly();
  readonly messages = computed(() => this.getActiveConversation()?.messages ?? []);
  readonly editingMessage = this.editingMessageState.asReadonly();
  readonly hasPausedResponse = computed(() => {
    const pendingResponse = this.pendingResponseState();
    return pendingResponse !== null && this.pendingConversationIdState() === null
      && pendingResponse.conversationId === this.activeConversationIdState();
  });
  readonly isResponding = computed(() => {
    const pendingConversationId = this.pendingConversationIdState();
    return pendingConversationId !== null && pendingConversationId === this.activeConversationIdState();
  });

  constructor() {
    effect(() => this.loadConversations(this.authService.currentUserId()));
  }

  private loadConversations(userId: string | null): void {
    this.pendingConversationIdState.set(null);
    this.pendingResponseState.set(null);
    const conversations = this.readConversations(userId);

    if (conversations.length > 0) {
      this.conversationsState.set(conversations);
      const savedConversationId = this.readActiveConversationId(userId);
      const activeConversationId = conversations.some(({ id }) => id === savedConversationId)
        ? savedConversationId
        : conversations[0].id;
      this.activeConversationIdState.set(activeConversationId);
      this.persistActiveConversationId();
      const pendingResponse = this.readPendingResponse(userId);
      if (pendingResponse && conversations.some(({ id }) => id === pendingResponse.conversationId)) {
        this.pendingResponseState.set(pendingResponse);
        this.pendingConversationIdState.set(pendingResponse.conversationId);
      } else {
        this.pendingResponseState.set(null);
        this.clearPersistedPendingResponse(userId);
      }
    } else {
      this.clearPersistedPendingResponse(userId);
      this.conversationsState.set([]);
      this.activeConversationIdState.set(null);
      this.createConversation();
    }
  }

  sendMessage(data: ChatRequest): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(this.apiUrl, data);
  }

  startResponse(conversationId: string, request: ChatRequest): void {
    this.pendingConversationIdState.set(conversationId);
    this.pendingResponseState.set({ conversationId, request });
    this.persistPendingResponse();
  }

  pauseResponse(conversationId: string): void {
    if (this.pendingConversationIdState() === conversationId) {
      this.pendingConversationIdState.set(null);
    }
  }

  resumeResponse(conversationId: string): void {
    if (this.pendingResponseState()?.conversationId === conversationId) {
      this.pendingConversationIdState.set(conversationId);
    }
  }

  finishResponse(conversationId: string): void {
    if (this.pendingConversationIdState() === conversationId) {
      this.pendingConversationIdState.set(null);
      this.pendingResponseState.set(null);
      this.persistPendingResponse();
    }
  }

  getPendingResponse(): PendingChatResponse | null {
    return this.pendingResponseState();
  }

  createConversation(): void {
    const conversation: ChatConversation = {
      id: crypto.randomUUID(),
      title: 'New chat',
      messages: [],
      updatedAt: Date.now()
    };

    this.conversationsState.update((conversations) => [conversation, ...conversations]);
    this.activeConversationIdState.set(conversation.id);
    this.persistConversations();
    this.persistActiveConversationId();
  }

  selectConversation(conversationId: string): void {
    if (this.conversationsState().some((conversation) => conversation.id === conversationId)) {
      this.editingMessageState.set(null);
      this.activeConversationIdState.set(conversationId);
      this.persistActiveConversationId();
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
        messages: [...conversation.messages, message],
        updatedAt: Date.now()
      };
    }).sort((first, second) => second.updatedAt - first.updatedAt));
    this.persistConversations();
  }

  beginEditingMessage(index: number): void {
    const conversation = this.getActiveConversation();
    const message = conversation?.messages[index];
    if (!conversation || !message || message.sender !== 'user' || this.isResponding()) return;

    this.editingMessageState.set({ conversationId: conversation.id, index, text: message.text });
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
          { sender: 'user' as const, text }
        ],
        updatedAt: Date.now()
      };
    }).sort((first, second) => second.updatedAt - first.updatedAt));

    this.editingMessageState.set(null);
    if (!updated) return null;
    this.persistConversations();
    return editingMessage.conversationId;
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

    this.conversationsState.update((conversations) => conversations.map((conversation) =>
      conversation.id === conversationId ? { ...conversation, title: trimmedTitle } : conversation
    ));
    this.persistConversations();
  }

  deleteConversation(conversationId: string): void {
    const conversations = this.conversationsState().filter((conversation) => conversation.id !== conversationId);
    this.conversationsState.set(conversations);

    if (this.pendingConversationIdState() === conversationId) {
      this.finishResponse(conversationId);
    }

    if (this.activeConversationIdState() === conversationId) {
      this.activeConversationIdState.set(conversations[0]?.id ?? null);
      this.persistActiveConversationId();
    }

    if (conversations.length === 0) this.createConversation();
    else this.persistConversations();
  }

  private getActiveConversation(): ChatConversation | undefined {
    return this.conversationsState().find((item) => item.id === this.activeConversationIdState());
  }

  private toTitle(message: string): string {
    return message.length > 32 ? `${message.slice(0, 32)}…` : message;
  }

  private readConversations(userId: string | null): ChatConversation[] {
    if (!this.isBrowser || !userId) return [];
    try {
      const saved = localStorage.getItem(this.getStorageKey(userId));
      return saved ? JSON.parse(saved) as ChatConversation[] : [];
    } catch {
      return [];
    }
  }

  private persistConversations(): void {
    const userId = this.authService.currentUserId();
    if (this.isBrowser && userId) {
      try {
        localStorage.setItem(this.getStorageKey(userId), JSON.stringify(this.conversationsState()));
      } catch {
        // Keep the in-memory conversation usable if storage is full or unavailable.
      }
    }
  }

  private readActiveConversationId(userId: string | null): string | null {
    if (!this.isBrowser || !userId) return null;
    try {
      return localStorage.getItem(this.getActiveConversationStorageKey(userId));
    } catch {
      return null;
    }
  }

  private persistActiveConversationId(): void {
    const userId = this.authService.currentUserId();
    if (!this.isBrowser || !userId) return;

    const storageKey = this.getActiveConversationStorageKey(userId);
    const conversationId = this.activeConversationIdState();
    try {
      if (conversationId) localStorage.setItem(storageKey, conversationId);
      else localStorage.removeItem(storageKey);
    } catch {
      // Chat remains usable when browser storage is unavailable.
    }
  }

  private readPendingResponse(userId: string | null): PendingChatResponse | null {
    if (!this.isBrowser || !userId) return null;
    try {
      const saved = localStorage.getItem(this.getPendingResponseStorageKey(userId));
      if (!saved) return null;
      const pendingResponse = JSON.parse(saved) as PendingChatResponse;
      return pendingResponse?.conversationId && pendingResponse.request?.message
        ? pendingResponse
        : null;
    } catch {
      return null;
    }
  }

  private persistPendingResponse(): void {
    const userId = this.authService.currentUserId();
    if (!this.isBrowser || !userId) return;

    const storageKey = this.getPendingResponseStorageKey(userId);
    const pendingResponse = this.pendingResponseState();
    try {
      if (pendingResponse) localStorage.setItem(storageKey, JSON.stringify(pendingResponse));
      else localStorage.removeItem(storageKey);
    } catch {
      // Chat remains usable when browser storage is unavailable.
    }
  }

  private clearPersistedPendingResponse(userId: string | null): void {
    if (!this.isBrowser || !userId) return;
    try {
      localStorage.removeItem(this.getPendingResponseStorageKey(userId));
    } catch {
      // Chat remains usable when browser storage is unavailable.
    }
  }

  private getStorageKey(userId: string): string {
    return `${this.storageKeyPrefix}:${userId}`;
  }

  private getActiveConversationStorageKey(userId: string): string {
    return `${this.activeConversationStorageKeyPrefix}:${userId}`;
  }

  private getPendingResponseStorageKey(userId: string): string {
    return `${this.pendingResponseStorageKeyPrefix}:${userId}`;
  }

}
