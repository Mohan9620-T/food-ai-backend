import { DOCUMENT } from '@angular/common';
import { DomSanitizer } from '@angular/platform-browser';
import {
  AfterViewChecked,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  SecurityContext,
  signal,
  viewChild,
} from '@angular/core';
import { ChatService } from '../../services/chat';
import { marked } from 'marked';
import { DocumentWizard } from '../document-wizard/document-wizard';
import { DocumentResult } from '../document-result/document-result';
import { DocumentAutomationTurn } from '../../models/chat';

@Component({
  selector: 'app-chat-window',
  imports: [DocumentWizard, DocumentResult],
  templateUrl: './chat-window.html',
  styleUrls: ['./chat-window.css'],
  host: { '(document:keydown.escape)': 'closeImagePreviewFromKeyboard()' },
})
export class ChatWindow implements AfterViewChecked {
  private readonly chatService = inject(ChatService);
  private readonly document = inject(DOCUMENT);
  private readonly destroyRef = inject(DestroyRef);
  private readonly sanitizer = inject(DomSanitizer);
  readonly messages = this.chatService.messages;
  readonly activeConversationId = this.chatService.activeConversationId;
  readonly isResponding = this.chatService.isResponding;
  readonly analyzingImage = this.chatService.analyzingImage;
  readonly streamingMessageIndex = computed(() => {
    const messages = this.messages();
    const lastIndex = messages.length - 1;
    const lastMessage = messages[lastIndex];
    return this.isResponding() && lastMessage?.sender === 'bot' && lastMessage.text.trim()
      ? lastIndex
      : -1;
  });
  private readonly chatContainer = viewChild<ElementRef<HTMLDivElement>>('chatContainer');
  private previousConversationId: string | null | undefined;
  private previousMessageCount = -1;
  private previousContentLength = -1;
  private previousRespondingState = false;
  readonly previewImageUrl = signal<string | null>(null);
  readonly copiedMessageIndex = signal<number | null>(null);
  private copyFeedbackTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      this.chatService.activeConversationId();
      queueMicrotask(() => this.chatContainer()?.nativeElement.focus({ preventScroll: true }));
    });
    this.destroyRef.onDestroy(() => {
      if (this.copyFeedbackTimer !== null) clearTimeout(this.copyFeedbackTimer);
    });
  }

  ngAfterViewChecked(): void {
    const container = this.chatContainer()?.nativeElement;
    if (!container) return;

    const conversationId = this.chatService.getActiveConversationId();
    const messageCount = this.messages().length;
    const contentLength = this.messages().reduce(
      (total, message) => total + message.text.length,
      0,
    );
    const responding = this.isResponding();
    const conversationChanged = conversationId !== this.previousConversationId;
    const contentChanged =
      messageCount !== this.previousMessageCount ||
      contentLength !== this.previousContentLength ||
      responding !== this.previousRespondingState;

    if (conversationChanged) {
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
    this.chatService.beginEditingMessage(index);
  }

  retryMessage(index: number): void {
    this.chatService.requestMessageRetry(index);
  }

  deleteMessage(index: number): void {
    this.chatService.deleteMessage(index);
  }

  downloadDocument(id: number, filename: string): void {
    this.chatService.downloadDocument(id, filename);
  }

  wizardTurn(index: number): DocumentAutomationTurn | undefined {
    if (index !== this.messages().length - 1) return undefined;
    const turn = this.messages()[index]?.automation;
    return turn &&
      ['clarification_required', 'ready_for_review'].includes(turn.response.status ?? '')
      ? turn
      : undefined;
  }

  isAwaitingResponse(index: number): boolean {
    return this.chatService.isMessageAwaitingResponse(index);
  }

  openImagePreview(imageUrl: string): void {
    this.previewImageUrl.set(imageUrl);
  }

  closeImagePreview(): void {
    this.previewImageUrl.set(null);
  }

  closeImagePreviewFromBackdrop(event: MouseEvent): void {
    if (event.target === event.currentTarget) this.closeImagePreview();
  }

  closeImagePreviewFromKeyboard(): void {
    this.closeImagePreview();
  }

  async copyMessage(text: string, index: number): Promise<void> {
    try {
      const clipboard = this.document.defaultView?.navigator.clipboard;
      if (!clipboard) throw new Error('Clipboard API unavailable');
      await clipboard.writeText(text);
      this.copiedMessageIndex.set(index);
      if (this.copyFeedbackTimer !== null) clearTimeout(this.copyFeedbackTimer);
      this.copyFeedbackTimer = setTimeout(() => this.copiedMessageIndex.set(null), 2000);
    } catch {
      this.copiedMessageIndex.set(null);
    }
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
    const daysAgo = Math.floor(
      (this.localDayStart(today).getTime() - this.localDayStart(date).getTime()) / 86_400_000,
    );
    return daysAgo >= 0 && daysAgo < 7
      ? new Intl.DateTimeFormat(undefined, { weekday: 'long' }).format(date)
      : new Intl.DateTimeFormat(undefined, {
          day: 'numeric',
          month: 'short',
          year: 'numeric',
        }).format(date);
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
