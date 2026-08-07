import { Component } from '@angular/core';

import { Sidebar } from '../../components/sidebar/sidebar';
import { ChatWindow } from '../../components/chat-window/chat-window';
import { ChatInput } from '../../components/chat-input/chat-input';

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
}