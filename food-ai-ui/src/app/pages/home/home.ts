import { Component, inject } from '@angular/core';

import { Sidebar } from '../../components/sidebar/sidebar';
import { ChatWindow } from '../../components/chat-window/chat-window';
import { ChatInput } from '../../components/chat-input/chat-input';
import { ChatService } from '../../services/chat';

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [
    Sidebar,
    ChatWindow,
    ChatInput
  ],
  templateUrl: './home.html',
  styleUrl: './home.css'
})
export class Home {
  private readonly chatService = inject(ChatService);
  readonly migrationNotice = this.chatService.migrationNotice;

  clearMigrationNotice(): void {
    this.chatService.clearMigrationNotice();
  }
}
