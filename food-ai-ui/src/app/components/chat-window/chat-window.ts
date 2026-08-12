import { AfterViewChecked, Component, ElementRef, inject, viewChild } from '@angular/core';
import { ChatService } from '../../services/chat';

@Component({
  selector: 'app-chat-window',
  templateUrl: './chat-window.html',
  styleUrls: ['./chat-window.css']
})
export class ChatWindow implements AfterViewChecked {
  private readonly chatService = inject(ChatService);
  readonly messages = this.chatService.messages;
  readonly isResponding = this.chatService.isResponding;
  private readonly scrollAnchor = viewChild<ElementRef<HTMLDivElement>>('scrollAnchor');

  ngAfterViewChecked(): void {
    this.scrollAnchor()?.nativeElement.scrollIntoView({ behavior: 'smooth' });
  }

  formattedLines(text: string): Array<Array<{ text: string; bold: boolean }>> {
    return text.split('\n').map((line) => {
      const segments: Array<{ text: string; bold: boolean }> = [];
      const expression = /\*\*(.+?)\*\*/g;
      let position = 0;
      let match: RegExpExecArray | null;

      while ((match = expression.exec(line)) !== null) {
        if (match.index > position) {
          segments.push({ text: line.slice(position, match.index), bold: false });
        }
        segments.push({ text: match[1], bold: true });
        position = match.index + match[0].length;
      }

      if (position < line.length) {
        segments.push({ text: line.slice(position), bold: false });
      }
      return segments.length > 0 ? segments : [{ text: '\u00a0', bold: false }];
    });
  }
}
