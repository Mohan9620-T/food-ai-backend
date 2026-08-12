import { Component, ElementRef, inject, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ChatResponse } from '../../models/chat';

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
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  resizeInput(event: Event): void {
    this.resizeTextarea(event.target as HTMLTextAreaElement);
  }

  handleKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;

    event.preventDefault();
    this.sendMessage();
  }

  sendMessage(): void {
    const userMessage = this.message.trim();

    if (!userMessage) {
      return;
    }

    const conversationId = this.chatService.getActiveConversationId();
    if (!conversationId) return;

    this.chatService.addMessage({ sender: 'user', text: userMessage }, conversationId);
    this.message = '';
    const textarea = this.messageInput()?.nativeElement;
    if (textarea) {
      textarea.style.height = '48px';
      textarea.scrollTop = 0;
    }
    this.chatService.startResponse(conversationId);

    this.chatService.sendMessage({
      message: userMessage,
      history: this.chatService.getHistory(conversationId),
      referenceHistory: this.chatService.getReferenceHistory(userMessage, conversationId)
    }).subscribe({
      next: (response: ChatResponse) => {
        this.chatService.addMessage({ sender: 'bot', text: response.response }, conversationId);
        this.chatService.finishResponse(conversationId);
      },
      error: (err: HttpErrorResponse) => {
        this.chatService.finishResponse(conversationId);

        if (err.status === 401) {
          this.authService.logout();
          this.router.navigate(['/login']);
          return;
        }

        this.chatService.addMessage({
          sender: 'bot',
          text: 'Sorry, the response could not be loaded. Please try again.'
        }, conversationId);
      }
    });
  }

  private resizeTextarea(textarea: HTMLTextAreaElement): void {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
  }
}
