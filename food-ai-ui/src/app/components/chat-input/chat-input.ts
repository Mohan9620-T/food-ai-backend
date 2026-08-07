import { Component, inject, signal } from '@angular/core';
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
  readonly isSending = signal(false);
  private readonly chatService = inject(ChatService);
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  sendMessage(): void {
    const userMessage = this.message.trim();

    if (!userMessage) {
      return;
    }

    const conversationId = this.chatService.getActiveConversationId();
    if (!conversationId) return;

    this.chatService.addMessage({ sender: 'user', text: userMessage }, conversationId);
    this.message = '';
    this.isSending.set(true);

    this.chatService.sendMessage({
      message: userMessage,
      history: this.chatService.getHistory(conversationId),
      referenceHistory: this.chatService.getReferenceHistory(userMessage, conversationId)
    }).subscribe({
      next: (response: ChatResponse) => {
        this.chatService.addMessage({ sender: 'bot', text: response.response }, conversationId);
        this.isSending.set(false);
      },
      error: (err: HttpErrorResponse) => {
        this.isSending.set(false);

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
}