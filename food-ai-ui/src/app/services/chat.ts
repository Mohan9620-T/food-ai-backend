import { computed, inject, Injectable, PLATFORM_ID, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { isPlatformBrowser } from '@angular/common';
import { Observable } from 'rxjs';
import { ChatConversation, ChatHistoryMessage, ChatMessage, ChatRequest, ChatResponse } from '../models/chat';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root'
})
export class ChatService {

  private readonly apiUrl = `${environment.apiUrl}/chat/`;
  private readonly storageKey = 'food-ai-chat-conversations';

  private readonly http = inject(HttpClient);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));

  private readonly conversationsState = signal<ChatConversation[]>([]);
  private readonly activeConversationIdState = signal<string | null>(null);

  readonly conversations = this.conversationsState.asReadonly();
  readonly activeConversationId = this.activeConversationIdState.asReadonly();
  readonly messages = computed(() => this.getActiveConversation()?.messages ?? []);

  constructor() {
    const conversations = this.readConversations();
    if (conversations.length > 0) {
      this.conversationsState.set(conversations);
      this.activeConversationIdState.set(conversations[0].id);
    } else {
      this.createConversation();
    }
  }

  sendMessage(data: ChatRequest): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(this.apiUrl, data);
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
  }

  selectConversation(conversationId: string): void {
    if (this.conversationsState().some((conversation) => conversation.id === conversationId)) {
      this.activeConversationIdState.set(conversationId);
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

    if (this.activeConversationIdState() === conversationId) {
      this.activeConversationIdState.set(conversations[0]?.id ?? null);
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

  private readConversations(): ChatConversation[] {
    if (!this.isBrowser) return [];
    try {
      const saved = localStorage.getItem(this.storageKey);
      return saved ? JSON.parse(saved) as ChatConversation[] : [];
    } catch {
      return [];
    }
  }

  private persistConversations(): void {
    if (this.isBrowser) localStorage.setItem(this.storageKey, JSON.stringify(this.conversationsState()));
  }

}
