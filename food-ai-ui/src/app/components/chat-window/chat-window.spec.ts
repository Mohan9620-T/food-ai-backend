import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { ChatWindow } from './chat-window';
import { ChatService } from '../../services/chat';

class ChatServiceStub {
  readonly messages = signal<Array<{ sender: 'user' | 'bot'; text: string; createdAt?: string; imageUrl?: string }>>([]);
  readonly isResponding = signal(false);
  readonly analyzingImage = signal(false);
  readonly activeConversationId = signal<string | null>('chat-1');
  readonly beginEditingMessage = vi.fn();
  readonly requestMessageRetry = vi.fn();
  isMessageAwaitingResponse(index: number): boolean {
    return this.messages()[index]?.sender === 'user' && !this.messages()[index + 1];
  }
  getActiveConversationId(): string | null { return this.activeConversationId(); }
}

describe('ChatWindow', () => {
  let fixture: ComponentFixture<ChatWindow>;
  let service: ChatServiceStub;

  beforeEach(async () => {
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      value: vi.fn()
    });
    await TestBed.configureTestingModule({
      imports: [ChatWindow],
      providers: [{ provide: ChatService, useClass: ChatServiceStub }]
    }).compileComponents();
    fixture = TestBed.createComponent(ChatWindow);
    service = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    fixture.detectChanges();
  });

  it('renders the empty state when there are no messages', () => {
    expect(fixture.nativeElement.querySelector('h1')?.textContent).toContain('How can I help?');
  });

  it('opens message actions and delegates retry', () => {
    service.messages.set([
      { sender: 'user', text: 'Try this' },
      { sender: 'bot', text: 'Previous answer' }
    ]);
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('.message-menu-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    const retry = Array.from(fixture.nativeElement.querySelectorAll('[role="menuitem"]') as NodeListOf<HTMLButtonElement>)
      .find((item) => item.textContent?.trim() === 'Retry') as HTMLButtonElement;
    retry.click();
    expect(service.requestMessageRetry).toHaveBeenCalledWith(0);
  });

  it('disables retry while a restored user message is awaiting its response', () => {
    service.messages.set([{ sender: 'user', text: 'Still processing' }]);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.message-menu-button') as HTMLButtonElement).click();
    fixture.detectChanges();

    const pending = Array.from(
      fixture.nativeElement.querySelectorAll('[role="menuitem"]') as NodeListOf<HTMLButtonElement>
    ).find(item => item.textContent?.trim() === 'Response pending');
    expect(pending?.disabled).toBe(true);
    pending?.click();
    expect(service.requestMessageRetry).not.toHaveBeenCalled();
  });

  it('removes unsafe script content from Markdown output', () => {
    const component = fixture.componentInstance;
    const rendered = component.renderMarkdown('Safe<script>alert(1)</script>');
    expect(rendered).toContain('Safe');
    expect(rendered).not.toContain('<script>');
  });

  it('opens a large image preview and closes it with Escape', () => {
    service.messages.set([{
      sender: 'user',
      text: 'Inspect this',
      imageUrl: 'data:image/png;base64,AQID'
    }]);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.message-image-button') as HTMLButtonElement).click();
    fixture.detectChanges();

    const preview = fixture.nativeElement.querySelector('.image-lightbox img') as HTMLImageElement;
    expect(preview.src).toContain('data:image/png;base64,AQID');

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.image-lightbox')).toBeNull();
  });
});
