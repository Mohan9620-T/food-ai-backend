import { Component, inject, signal } from '@angular/core';
import { ChatService } from '../../services/chat';

@Component({
  selector: 'app-sidebar',
  templateUrl: './sidebar.html',
  styleUrl: './sidebar.css'
})
export class Sidebar {
  private readonly chatService = inject(ChatService);
  readonly conversations = this.chatService.conversations;
  readonly activeConversationId = this.chatService.activeConversationId;
  readonly openMenuId = signal<string | null>(null);

  createConversation(): void {
    this.chatService.createConversation();
  }

  selectConversation(conversationId: string): void {
    this.chatService.selectConversation(conversationId);
    this.openMenuId.set(null);
  }

  toggleMenu(conversationId: string): void {
    this.openMenuId.update((openMenuId) => openMenuId === conversationId ? null : conversationId);
  }

  renameConversation(conversationId: string, currentTitle: string): void {
    const title = window.prompt('Rename chat', currentTitle);
    if (title !== null) this.chatService.renameConversation(conversationId, title);
    this.openMenuId.set(null);
  }

  deleteConversation(conversationId: string): void {
    if (window.confirm('Delete this chat permanently?')) {
      this.chatService.deleteConversation(conversationId);
    }
    this.openMenuId.set(null);
  }
}
