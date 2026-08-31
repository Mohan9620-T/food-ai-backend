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

  renderMarkdown(text: string): string {
    const html = marked.parse(text, { async: false, gfm: true, breaks: true }) as string;
    return this.sanitizer.sanitize(SecurityContext.HTML, html) ?? '';
  }
}
