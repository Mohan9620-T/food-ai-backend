import { afterNextRender, Component, effect, ElementRef, inject, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { finalize, Subscription, timeout } from 'rxjs';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ChatRequest, ChatResponse } from '../../models/chat';

@Component({
  selector: 'app-chat-input',
  imports: [FormsModule],
  templateUrl: './chat-input.html',
  styleUrls: ['./chat-input.css']
})
export class ChatInput {
  // The backend allows Ollama up to 180 seconds, so the browser must wait longer.
  private static readonly responseTimeoutMs = 200_000;
  message = '';
  private readonly messageInput = viewChild<ElementRef<HTMLTextAreaElement>>('messageInput');
  private readonly chatService = inject(ChatService);
  readonly isSending = this.chatService.isResponding;
  readonly editingMessage = this.chatService.editingMessage;
  readonly hasPausedResponse = this.chatService.hasPausedResponse;
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  private responseSubscription: Subscription | null = null;
  private pauseRequested = false;

  constructor() {
    afterNextRender(() => this.resumePendingResponse());
    effect(() => {
      const editingMessage = this.editingMessage();
      if (!editingMessage) return;

      this.message = editingMessage.text;
      queueMicrotask(() => {
        const textarea = this.messageInput()?.nativeElement;
        if (!textarea) return;
        this.resizeTextarea(textarea);
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      });
    });
    effect(() => {
      if (!this.chatService.retryMessage()) return;

      queueMicrotask(() => this.retryPendingMessage());
    });
  }

  resizeInput(event: Event): void {
    this.resizeTextarea(event.target as HTMLTextAreaElement);
  }

  handleKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.repeat) return;

    event.preventDefault();
    this.sendMessage();
  }

  sendMessage(): void {
    if (this.isSending()) return;

    const userMessage = this.message.trim();

    if (!userMessage) {
      return;
    }

    const isEditing = this.editingMessage() !== null;
    const conversationId = isEditing
      ? this.chatService.replaceEditingMessage(userMessage)
      : this.chatService.getActiveConversationId();
    if (!conversationId) return;

    if (!isEditing) {
      this.chatService.addMessage({ sender: 'user', text: userMessage }, conversationId);
    }
    this.message = '';
    const textarea = this.messageInput()?.nativeElement;
    if (textarea) {
      textarea.style.height = '48px';
      textarea.scrollTop = 0;
    }
    const request: ChatRequest = {
      message: userMessage,
      history: this.chatService.getHistory(conversationId),
      referenceHistory: this.chatService.getReferenceHistory(userMessage, conversationId)
    };
    this.chatService.startResponse(conversationId, request);
    this.requestResponse(conversationId, request);
  }

  pauseResponse(): void {
    if (!this.isSending()) return;

    const conversationId = this.chatService.getActiveConversationId();
    if (!conversationId) return;

    this.pauseRequested = true;
    this.chatService.pauseResponse(conversationId);
    this.responseSubscription?.unsubscribe();
    this.responseSubscription = null;
    queueMicrotask(() => this.messageInput()?.nativeElement.focus());
  }

  continueResponse(): void {
    const pendingResponse = this.chatService.getPendingResponse();
    if (!pendingResponse || !this.hasPausedResponse()) return;

    this.chatService.resumeResponse(pendingResponse.conversationId);
    this.requestResponse(pendingResponse.conversationId, pendingResponse.request);
  }

  private resumePendingResponse(): void {
    const pendingResponse = this.chatService.getPendingResponse();
    if (!pendingResponse) return;

    this.requestResponse(pendingResponse.conversationId, pendingResponse.request);
  }

  private retryPendingMessage(): void {
    const retryMessage = this.chatService.consumeRetryMessage();
    if (!retryMessage || this.isSending()) return;

    const request: ChatRequest = {
      message: retryMessage.text,
      history: this.chatService.getHistory(retryMessage.conversationId),
      referenceHistory: this.chatService.getReferenceHistory(
        retryMessage.text,
        retryMessage.conversationId
      )
    };
    this.chatService.startResponse(retryMessage.conversationId, request);
    this.requestResponse(retryMessage.conversationId, request);
  }

  private requestResponse(conversationId: string, request: ChatRequest): void {
    this.pauseRequested = false;
    this.responseSubscription = this.chatService.sendMessage(request).pipe(
      timeout(ChatInput.responseTimeoutMs),
      finalize(() => {
        if (!this.pauseRequested) {
          this.chatService.finishResponse(conversationId);
        }
        this.responseSubscription = null;
        queueMicrotask(() => this.messageInput()?.nativeElement.focus());
      })
    ).subscribe({
      next: (response: ChatResponse) => {
        this.chatService.addMessage({ sender: 'bot', text: response.response }, conversationId);
      },
      error: (err: HttpErrorResponse) => {
        if (err.status === 401) {
          this.authService.logout();
          this.router.navigate(['/login']);
          return;
        }

        if (!this.message.trim()) {
          this.message = request.message;
        }

        this.chatService.addMessage({
          sender: 'bot',
          text: err.status === 0
            ? 'AI service is unavailable. Please start the backend and Ollama, then try again.'
            : 'The AI response timed out. Your message is restored below; please try again.'
        }, conversationId);
      }
    });
  }

  private resizeTextarea(textarea: HTMLTextAreaElement): void {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
  }
}
