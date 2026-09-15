import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { provideHttpClient } from '@angular/common/http';

import { ChatWindow } from './chat-window';
import { ChatService } from '../../services/chat';
import { ChatMessage } from '../../models/chat';

class ChatServiceStub {
  getActiveSessionId(): number | null {
    return null;
  }
  readonly messages = signal<ChatMessage[]>([]);
  readonly isResponding = signal(false);
  readonly analyzingImage = signal(false);
  readonly activeConversationId = signal<string | null>('chat-1');
  readonly beginEditingMessage = vi.fn();
  readonly requestMessageRetry = vi.fn();
  readonly deleteMessage = vi.fn();
  readonly downloadDocument = vi.fn();
  isMessageAwaitingResponse(index: number): boolean {
    return this.messages()[index]?.sender === 'user' && !this.messages()[index + 1];
  }
  getActiveConversationId(): string | null {
    return this.activeConversationId();
  }
}

describe('ChatWindow', () => {
  let fixture: ComponentFixture<ChatWindow>;
  let service: ChatServiceStub;

  beforeEach(async () => {
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      value: vi.fn(),
    });
    await TestBed.configureTestingModule({
      imports: [ChatWindow],
      providers: [provideHttpClient(), { provide: ChatService, useClass: ChatServiceStub }],
    }).compileComponents();
    fixture = TestBed.createComponent(ChatWindow);
    service = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    fixture.detectChanges();
  });

  it('renders the empty state when there are no messages', () => {
    expect(fixture.nativeElement.querySelector('h1')?.textContent).toContain('How can I help?');
  });

  it('routes a ready-for-review response to a Build card instead of a plain bubble', () => {
    service.messages.set([
      {
        sender: 'bot',
        text: 'Review',
        automation: {
          response: {
            session_id: 9,
            status: 'ready_for_review',
            response: 'Review',
            plan_summary: 'Create brochure.docx',
          },
          instruction: 'Word',
          choices: [{ question: 'Format?', answer: 'Word' }],
        },
      },
    ]);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    expect(root.querySelector('app-document-wizard')).toBeTruthy();
    expect(root.querySelector('.markdown-body')).toBeNull();
    expect(root.textContent).toContain('Build my document file');
    expect(root.textContent).toContain('Create brochure.docx');
    expect(root.textContent).toContain('Format?');
  });

  it('downloads a saved original attachment even when AI analysis was unavailable', () => {
    service.messages.set([
      {
        sender: 'user',
        text: 'Read this file',
        attachment: {
          id: 31,
          filename: 'notes.pdf',
          content_type: 'application/pdf',
          file_size: 200,
          kind: 'uploaded',
        },
      },
      { sender: 'bot', text: 'File saved. AI analysis is unavailable.' },
    ]);
    fixture.detectChanges();
    const download = fixture.nativeElement.querySelector(
      '.user-message [aria-label="Download notes.pdf"]',
    ) as HTMLButtonElement;
    expect(download).toBeTruthy();
    expect(download.disabled).toBe(false);
    download.click();
    expect(service.downloadDocument).toHaveBeenCalledExactlyOnceWith(31, 'notes.pdf');
  });

  it('shows and downloads a generated category workbook from the assistant response', () => {
    service.messages.set([
      {
        sender: 'bot',
        text: 'Done — I created a separate sheet for each category.',
        attachment: {
          id: 32,
          filename: 'items_category_wise.xlsx',
          content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          file_size: 4096,
          kind: 'generated',
        },
      },
    ]);
    fixture.detectChanges();

    const download = fixture.nativeElement.querySelector(
      '.bot-message-group [aria-label="Download items_category_wise.xlsx"]',
    ) as HTMLButtonElement;
    expect(download).toBeTruthy();
    expect(download.disabled).toBe(false);
    download.click();
    expect(service.downloadDocument).toHaveBeenCalledExactlyOnceWith(
      32,
      'items_category_wise.xlsx',
    );
  });

  it('renders and downloads every file produced by document automation', () => {
    service.messages.set([
      {
        sender: 'bot',
        text: 'Done — I created both requested files.',
        attachments: [
          {
            id: 41,
            filename: 'extracted-data.xlsx',
            content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            file_size: 6400,
            kind: 'generated',
          },
          {
            id: 42,
            filename: 'document-report.docx',
            content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            file_size: 7200,
            kind: 'generated',
          },
        ],
      },
    ]);
    fixture.detectChanges();

    const buttons = Array.from(
      fixture.nativeElement.querySelectorAll('app-document-result button[aria-label^="Download"]'),
    ) as HTMLButtonElement[];
    expect(buttons).toHaveLength(2);
    expect(fixture.nativeElement.textContent).toContain('extracted-data.xlsx');
    expect(fixture.nativeElement.textContent).toContain('document-report.docx');

    buttons[0].click();
    buttons[1].click();
    expect(service.downloadDocument).toHaveBeenNthCalledWith(1, 41, 'extracted-data.xlsx');
    expect(service.downloadDocument).toHaveBeenNthCalledWith(2, 42, 'document-report.docx');
  });

  it('shows icon actions and delegates retry, edit, and delete', () => {
    service.messages.set([
      { sender: 'user', text: 'Try this' },
      { sender: 'bot', text: 'Previous answer' },
    ]);
    fixture.detectChanges();
    (
      fixture.nativeElement.querySelector('[aria-label="Retry message"]') as HTMLButtonElement
    ).click();
    (
      fixture.nativeElement.querySelector('[aria-label="Edit message"]') as HTMLButtonElement
    ).click();
    (
      fixture.nativeElement.querySelector('[aria-label="Delete message"]') as HTMLButtonElement
    ).click();
    expect(service.requestMessageRetry).toHaveBeenCalledWith(0);
    expect(service.beginEditingMessage).toHaveBeenCalledWith(0);
    expect(service.deleteMessage).toHaveBeenCalledWith(0);
  });

  it('disables retry while a restored user message is awaiting its response', () => {
    service.messages.set([{ sender: 'user', text: 'Still processing' }]);
    fixture.detectChanges();

    const pending = fixture.nativeElement.querySelector(
      '[aria-label="Retry message"]',
    ) as HTMLButtonElement;
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

  it('renders heading levels and bold labels in assistant answers', () => {
    service.messages.set([{ sender: 'bot', text: '## Overview\n\n### Details\n\n**Key point:** Content.' }]);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    expect(root.querySelector('.markdown-body h2')?.textContent).toBe('Overview');
    expect(root.querySelector('.markdown-body h3')?.textContent).toBe('Details');
    expect(root.querySelector('.markdown-body strong')?.textContent).toBe('Key point:');
  });

  it('renders paragraphs, emphasis, lists, and links in a labelled assistant response', () => {
    service.messages.set([
      {
        sender: 'bot',
        text: 'I hear you.\n\n**One step at a time.**\n\n- Take a pause.\n- [Read more](https://example.com/help).',
      },
    ]);
    fixture.detectChanges();

    const response = fixture.nativeElement.querySelector(
      'article[aria-label="AI assistant response"]',
    ) as HTMLElement;
    expect(response.querySelector('.assistant-label')?.textContent).toContain('AI assistant');
    expect(response.querySelectorAll('.markdown-body > p').length).toBe(2);
    expect(response.querySelector('strong')?.textContent).toBe('One step at a time.');
    expect(response.querySelectorAll('li').length).toBe(2);
    expect(response.querySelector('a')?.getAttribute('href')).toBe('https://example.com/help');
  });

  it('shows only a preparation status before the first streamed text arrives', () => {
    service.messages.set([
      { sender: 'user', text: 'Hello' },
      { sender: 'bot', text: '' },
    ]);
    service.isResponding.set(true);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelectorAll('[role="status"]').length).toBe(1);
    expect(fixture.nativeElement.querySelector('[role="status"]')?.textContent).toContain(
      'Preparing a response',
    );
    expect(fixture.nativeElement.querySelector('.bot-message-group')).toBeNull();
    expect(fixture.nativeElement.querySelector('.copy-message-button')).toBeNull();
  });

  it('replaces the preparation skeleton with an inline writing status and clears it when finished', () => {
    service.messages.set([
      { sender: 'user', text: 'Hello' },
      { sender: 'bot', text: 'Hello, how can' },
    ]);
    service.isResponding.set(true);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.bot-message')?.textContent).toContain(
      'Hello, how can',
    );
    expect(fixture.nativeElement.querySelector('.response-skeleton')).toBeNull();
    expect(
      fixture.nativeElement.querySelector('.bot-message-group [role="status"]')?.textContent,
    ).toContain('Writing response');

    service.messages.update((messages) => [
      messages[0],
      { sender: 'bot', text: 'Hello, how can I help?' },
    ]);
    service.isResponding.set(false);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[role="status"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('.bot-message')?.textContent).toContain(
      'Hello, how can I help?',
    );
    expect(fixture.nativeElement.textContent).not.toContain('Response completed');
  });

  it('keeps completed replies unchanged while preparing the next response', () => {
    service.messages.set([
      { sender: 'user', text: 'Hello' },
      { sender: 'bot', text: 'Hello there.' },
      { sender: 'user', text: 'One more question' },
    ]);
    service.isResponding.set(true);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.writing-status')).toBeNull();
    expect(fixture.nativeElement.querySelectorAll('.bot-message-group').length).toBe(1);
    expect(fixture.nativeElement.querySelector('[role="status"]')?.textContent).toContain(
      'Preparing a response',
    );
  });

  it('shows image analysis status until a response starts arriving', () => {
    service.messages.set([{ sender: 'user', text: 'Describe the image' }]);
    service.isResponding.set(true);
    service.analyzingImage.set(true);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelectorAll('[role="status"]').length).toBe(1);
    expect(fixture.nativeElement.querySelector('[role="status"]')?.textContent).toContain(
      'Analyzing image',
    );

    service.messages.update((messages) => [
      ...messages,
      { sender: 'bot', text: 'The image shows' },
    ]);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.image-analysis')).toBeNull();
    expect(fixture.nativeElement.querySelectorAll('[role="status"]').length).toBe(1);
    expect(fixture.nativeElement.querySelector('[role="status"]')?.textContent).toContain(
      'Writing response',
    );
  });

  it('copies an assistant response and shows copied feedback', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    service.messages.set([{ sender: 'bot', text: '**Useful answer**' }]);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.copy-message-button') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(writeText).toHaveBeenCalledWith('**Useful answer**');
    expect(fixture.nativeElement.querySelector('.copy-message-button')?.getAttribute('title')).toBe(
      'Copied',
    );
  });

  it('does not show copied feedback when clipboard access fails', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) },
    });
    service.messages.set([{ sender: 'bot', text: 'Private answer' }]);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.copy-message-button') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.copy-message-button')?.getAttribute('title')).toBe(
      'Copy',
    );
  });

  it('opens a large image preview and closes it with Escape', () => {
    service.messages.set([
      {
        sender: 'user',
        text: 'Inspect this',
        imageUrl: 'data:image/png;base64,AQID',
      },
    ]);
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
