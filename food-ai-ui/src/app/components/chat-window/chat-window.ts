import { AfterViewChecked, Component, ElementRef, inject, viewChild } from '@angular/core';
import { ChatService } from '../../services/chat';

@Component({
  selector: 'app-chat-window',
  templateUrl: './chat-window.html',
  styleUrls: ['./chat-window.css']
})
export class ChatWindow implements AfterViewChecked {
  readonly messages = inject(ChatService).messages;
  private readonly scrollAnchor = viewChild<ElementRef<HTMLDivElement>>('scrollAnchor');

  ngAfterViewChecked(): void {
    this.scrollAnchor()?.nativeElement.scrollIntoView({ behavior: 'smooth' });
  }
}