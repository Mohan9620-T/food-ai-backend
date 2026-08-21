import { AfterViewChecked, Component, ElementRef, inject, signal, viewChild } from '@angular/core';
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
  private readonly chatContainer = viewChild<ElementRef<HTMLDivElement>>('chatContainer');
  private previousConversationId: string | null | undefined;
  private previousMessageCount = -1;
  private previousRespondingState = false;
  readonly openMessageMenuIndex = signal<number | null>(null);

  ngAfterViewChecked(): void {
    const container = this.chatContainer()?.nativeElement;
    if (!container) return;

    const conversationId = this.chatService.getActiveConversationId();
    const messageCount = this.messages().length;
    const responding = this.isResponding();
    const conversationChanged = conversationId !== this.previousConversationId;
    const contentChanged = messageCount !== this.previousMessageCount
      || responding !== this.previousRespondingState;

    if (conversationChanged) {
      container.scrollTop = container.scrollHeight;
    } else if (contentChanged) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    }

    this.previousConversationId = conversationId;
    this.previousMessageCount = messageCount;
    this.previousRespondingState = responding;
  }

  editMessage(index: number): void {
    this.openMessageMenuIndex.set(null);
    this.chatService.beginEditingMessage(index);
  }

  retryMessage(index: number): void {
    this.openMessageMenuIndex.set(null);
    this.chatService.requestMessageRetry(index);
  }

  toggleMessageMenu(index: number): void {
    this.openMessageMenuIndex.update((openIndex) => openIndex === index ? null : index);
  }

  formattedLines(text: string): Array<Array<{ text: string; bold: boolean }>> {
    return this.toDisplayLines(text).map((line) => {
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

  private toDisplayLines(text: string): string[] {
    const displayLines: string[] = [];
    let insideCodeBlock = false;

    for (const sourceLine of text.replace(/\\n/g, '\n').split(/\r?\n/)) {
      const line = sourceLine.trimEnd();

      if (line.trimStart().startsWith('```')) {
        insideCodeBlock = !insideCodeBlock;
        displayLines.push(line);
        continue;
      }

      if (insideCodeBlock || !line.trim() || /^\s*(?:[-*+] |\d+[.)] )/.test(line)) {
        displayLines.push(line);
        continue;
      }

      const sentences = line.match(/.*?(?:[.!?](?=\s|$)|$)/g)
        ?.map((sentence) => sentence.trim())
        .filter(Boolean) ?? [];
      displayLines.push(...(sentences.length > 0 ? sentences : [line]));
    }

    return displayLines;
  }
}
