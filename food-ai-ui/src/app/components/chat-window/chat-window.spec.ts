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
  readonly deleteMessage = vi.fn();
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

  it('shows icon actions and delegates retry, edit, and delete', () => {
    service.messages.set([
      { sender: 'user', text: 'Try this' },
      { sender: 'bot', text: 'Previous answer' }
    ]);
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('[aria-label="Retry message"]') as HTMLButtonElement).click();
    (fixture.nativeElement.querySelector('[aria-label="Edit message"]') as HTMLButtonElement).click();
    (fixture.nativeElement.querySelector('[aria-label="Delete message"]') as HTMLButtonElement).click();
    expect(service.requestMessageRetry).toHaveBeenCalledWith(0);
    expect(service.beginEditingMessage).toHaveBeenCalledWith(0);
    expect(service.deleteMessage).toHaveBeenCalledWith(0);
  });

  it('disables retry while a restored user message is awaiting its response', () => {
    service.messages.set([{ sender: 'user', text: 'Still processing' }]);
    fixture.detectChanges();

    const pending = fixture.nativeElement.querySelector('[aria-label="Retry message"]') as HTMLButtonElement;
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

  it('copies an assistant response and shows copied feedback', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText }
    });
    service.messages.set([{ sender: 'bot', text: '**Useful answer**' }]);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.copy-message-button') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(writeText).toHaveBeenCalledWith('**Useful answer**');
    expect(fixture.nativeElement.querySelector('.copy-message-button')?.getAttribute('title')).toBe('Copied');
  });

  it('does not show copied feedback when clipboard access fails', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) }
    });
    service.messages.set([{ sender: 'bot', text: 'Private answer' }]);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.copy-message-button') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.copy-message-button')?.getAttribute('title')).toBe('Copy');
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
