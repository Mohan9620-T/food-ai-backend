import { afterNextRender, Component, effect, ElementRef, inject, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ChatRequest } from '../../models/chat';

@Component({
  selector: 'app-chat-input',
  imports: [FormsModule],
  templateUrl: './chat-input.html',
  styleUrls: ['./chat-input.css']
})
export class ChatInput {
  message = '';
  private readonly messageInput = viewChild<ElementRef<HTMLTextAreaElement>>('messageInput');
  private readonly chatService = inject(ChatService);
  readonly isSending = this.chatService.isResponding;
  readonly editingMessage = this.chatService.editingMessage;
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  constructor() {
    afterNextRender(() => this.messageInput()?.nativeElement.focus());
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

  stopResponse(): void {
    if (!this.isSending()) return;
    this.chatService.stopStreaming();
    const pending = this.chatService.getPendingResponse();
    if (pending) this.chatService.finishResponse(pending.conversationId);
    queueMicrotask(() => this.messageInput()?.nativeElement.focus());
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

  private async requestResponse(conversationId: string, request: ChatRequest): Promise<void> {
    try {
      await this.chatService.streamMessage(request, conversationId);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (!this.authService.getToken()) {
        this.router.navigate(['/login']);
        return;
      }
      if (!this.message.trim()) this.message = request.message;
      this.chatService.addMessage({
        sender: 'bot',
        text: 'The response could not be streamed. Your message is restored below; please try again.'
      }, this.chatService.getActiveConversationId() ?? conversationId);
    } finally {
      const pending = this.chatService.getPendingResponse();
      if (pending) this.chatService.finishResponse(pending.conversationId);
      queueMicrotask(() => this.messageInput()?.nativeElement.focus());
    }
  }

  private resizeTextarea(textarea: HTMLTextAreaElement): void {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
  }
}
