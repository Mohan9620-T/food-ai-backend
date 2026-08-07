import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ChatService } from '../../services/chat';
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
      error: () => {
        this.chatService.addMessage({
          sender: 'bot',
          text: 'Sorry, the response could not be loaded. Please try again.'
        }, conversationId);
        this.isSending.set(false);
      }
    });
  }
}
