import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';

import { ChatService } from '../../services/chat';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule
  ],
  templateUrl: './chat.html',
  styleUrl: './chat.css'
})
export class Chat {

  private chatService = inject(ChatService);

  message = '';

  response = '';

  loading = false;

  sendMessage() {

    if (!this.message.trim()) {
      return;
    }

    this.loading = true;

    this.chatService.sendMessage({
      message: this.message,
      history: [],
      referenceHistory: []
    }).subscribe({

      next: (res) => {

        this.response = res.response;

        this.loading = false;

      },

      error: (err) => {

        console.error(err);

        this.loading = false;

      }

    });

  }

}