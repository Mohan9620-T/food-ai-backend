import { afterNextRender, Component, DestroyRef, effect, ElementRef, inject, viewChild } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ChatRequest } from '../../models/chat';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-chat-input',
  imports: [FormsModule],
  templateUrl: './chat-input.html',
  styleUrls: ['./chat-input.css']
})
export class ChatInput {
  message = '';
  private readonly messageInput = viewChild<ElementRef<HTMLTextAreaElement>>('messageInput');
  private readonly destroyRef = inject(DestroyRef);
  private readonly chatService = inject(ChatService);
  readonly isSending = this.chatService.isResponding;
  readonly editingMessage = this.chatService.editingMessage;
  readonly analyzingImage = this.chatService.analyzingImage;
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  selectedImage: File | null = null;
  imagePreviewUrl: string | null = null;
  imageError: string | null = null;
  private visionSubscription: Subscription | null = null;

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
    this.destroyRef.onDestroy(() => {
      this.visionSubscription?.unsubscribe();
      if (this.imagePreviewUrl) URL.revokeObjectURL(this.imagePreviewUrl);
    });
  }

  selectImage(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    input.value = '';
    if (!file) return;
    this.imageError = null;
    if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) {
      this.imageError = 'Unsupported file type. Upload a JPEG, PNG, WebP, or GIF image.';
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      this.imageError = 'Image is too large. Maximum size is 8 MB.';
      return;
    }
    this.removeImage();
    this.selectedImage = file;
    this.imagePreviewUrl = URL.createObjectURL(file);
  }

  removeImage(): void {
    if (this.imagePreviewUrl) URL.revokeObjectURL(this.imagePreviewUrl);
    this.selectedImage = null;
    this.imagePreviewUrl = null;
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

    if (!userMessage && !this.selectedImage) {
      return;
    }

    const isEditing = this.editingMessage() !== null;
    const conversationId = isEditing
      ? this.chatService.replaceEditingMessage(userMessage)
      : this.chatService.getActiveConversationId();
    if (!conversationId) return;

    const image = this.selectedImage;
    const previewUrl = this.imagePreviewUrl;
    if (!isEditing) {
      this.chatService.addMessage({ sender: 'user', text: userMessage || '[Image]', imageUrl: previewUrl ?? undefined }, conversationId);
    }
    this.selectedImage = null;
    this.imagePreviewUrl = null;
    this.imageError = null;
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
    if (image) this.requestVisionResponse(conversationId, image, userMessage || null);
    else this.requestResponse(conversationId, request);
  }

  stopResponse(): void {
    if (!this.isSending()) return;
    if (this.analyzingImage()) {
      this.visionSubscription?.unsubscribe();
      this.visionSubscription = null;
      this.imageError = 'Image analysis was cancelled.';
    } else {
      this.chatService.stopStreaming();
    }
    const pending = this.chatService.getPendingResponse();
    if (pending) this.chatService.finishResponse(pending.conversationId);
    queueMicrotask(() => this.messageInput()?.nativeElement.focus());
  }

  private requestVisionResponse(conversationId: string, image: File, message: string | null): void {
    this.visionSubscription = this.chatService.sendVisionMessage(image, message, conversationId).subscribe({
      error: (error: HttpErrorResponse) => {
        const detail = typeof error.error?.detail === 'string' ? error.error.detail : null;
        this.imageError = error.status === 503 ? detail ?? 'Vision model unavailable. Please start Ollama and try again.' : detail ?? 'The image could not be analyzed. Please try again.';
        this.finishVisionResponse(conversationId);
      },
      complete: () => this.finishVisionResponse(conversationId)
    });
  }

  private finishVisionResponse(conversationId: string): void {
    this.visionSubscription = null;
    this.chatService.finishResponse(conversationId);
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
