import { Component, inject } from '@angular/core';
import { ChatService } from '../../services/chat';

@Component({
  selector: 'app-chat-window',
  templateUrl: './chat-window.html',
  styleUrls: ['./chat-window.css']
})
export class ChatWindow {
  readonly messages = inject(ChatService).messages;
}
