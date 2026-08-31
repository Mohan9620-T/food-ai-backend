import { DOCUMENT } from '@angular/common';
import { DomSanitizer } from '@angular/platform-browser';
import { AfterViewChecked, afterNextRender, Component, DestroyRef, effect, ElementRef, inject, SecurityContext, signal, viewChild } from '@angular/core';
import { ChatService } from '../../services/chat';
import { marked } from 'marked';

@Component({
  selector: 'app-chat-window',
  templateUrl: './chat-window.html',
  styleUrls: ['./chat-window.css']
})
export class ChatWindow implements AfterViewChecked {
  private readonly chatService = inject(ChatService);
  private readonly document = inject(DOCUMENT);
  private readonly destroyRef = inject(DestroyRef);
  private readonly sanitizer = inject(DomSanitizer);
  readonly messages = this.chatService.messages;
  readonly isResponding = this.chatService.isResponding;
  private readonly chatContainer = viewChild<ElementRef<HTMLDivElement>>('chatContainer');
  private previousConversationId: string | null | undefined;
  private previousMessageCount = -1;
  private previousContentLength = -1;
  private previousRespondingState = false;
  readonly openMessageMenuIndex = signal<number | null>(null);

  constructor() {
    effect(() => {
      this.chatService.activeConversationId();
      queueMicrotask(() => this.chatContainer()?.nativeElement.focus({ preventScroll: true }));
    });
    afterNextRender(() => {
      const closeMenuOnOutsideClick = (event: PointerEvent): void => {
        const target = event.target;
        if (!(target instanceof Element) || target.closest('.message-actions')) return;
        this.openMessageMenuIndex.set(null);
      };

      this.document.addEventListener('pointerdown', closeMenuOnOutsideClick);
      this.destroyRef.onDestroy(() => {
        this.document.removeEventListener('pointerdown', closeMenuOnOutsideClick);
      });
    });
  }

  ngAfterViewChecked(): void {
    const container = this.chatContainer()?.nativeElement;
    if (!container) return;

    const conversationId = this.chatService.getActiveConversationId();
    const messageCount = this.messages().length;
    const contentLength = this.messages().reduce((total, message) => total + message.text.length, 0);
    const responding = this.isResponding();
    const conversationChanged = conversationId !== this.previousConversationId;
    const contentChanged = messageCount !== this.previousMessageCount
      || contentLength !== this.previousContentLength
      || responding !== this.previousRespondingState;

    if (conversationChanged) {
      this.openMessageMenuIndex.set(null);
      container.scrollTop = container.scrollHeight;
    } else if (contentChanged) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    }

    this.previousConversationId = conversationId;
    this.previousMessageCount = messageCount;
    this.previousContentLength = contentLength;
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

  shouldShowDateSeparator(index: number): boolean {
    const current = this.messages()[index]?.createdAt;
    if (!current) return false;
    const previous = index > 0 ? this.messages()[index - 1]?.createdAt : null;
    return !previous || this.dateKey(current) !== this.dateKey(previous);
  }

  formatMessageDate(value: string | undefined): string {
    if (!value) return '';
    const date = new Date(value);
    const today = new Date();
    if (this.dateKey(value) === this.dateKey(today.toISOString())) return 'Today';
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (this.dateKey(value) === this.dateKey(yesterday.toISOString())) return 'Yesterday';
    const daysAgo = Math.floor((this.localDayStart(today).getTime() - this.localDayStart(date).getTime()) / 86_400_000);
    return daysAgo >= 0 && daysAgo < 7
      ? new Intl.DateTimeFormat(undefined, { weekday: 'long' }).format(date)
      : new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' }).format(date);
  }

  private dateKey(value: string): string {
    const date = new Date(value);
    return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
  }

  private localDayStart(value: Date): Date {
    return new Date(value.getFullYear(), value.getMonth(), value.getDate());
  }

  renderMarkdown(text: string): string {
    const html = marked.parse(text, { async: false, gfm: true, breaks: true }) as string;
    return this.sanitizer.sanitize(SecurityContext.HTML, html) ?? '';
  }
}
