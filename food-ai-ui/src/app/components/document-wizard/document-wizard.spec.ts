import { TestBed, ComponentFixture } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { vi } from 'vitest';
import axe from 'axe-core';
import { DocumentWizard } from './document-wizard';
import { ChatService } from '../../services/chat';
import { ChatResponse } from '../../models/chat';

class ChatStub {
  result = new Subject<ChatResponse>();
  automateDocument = vi.fn(() => this.result);
}

describe('DocumentWizard', () => {
  let fixture: ComponentFixture<DocumentWizard>;
  let chat: ChatStub;
  const response: ChatResponse = {
    session_id: 9,
    status: 'clarification_required',
    response: 'Which format?',
    clarification: {
      question: 'Which format?',
      allow_other: true,
      options: [
        { id: 'word', label: 'Word', description: 'Editable document', recommended: true },
        { id: 'pdf', label: 'PDF', recommended: false },
      ],
    },
  };
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DocumentWizard],
      providers: [{ provide: ChatService, useClass: ChatStub }],
    });
    chat = TestBed.inject(ChatService) as unknown as ChatStub;
    fixture = TestBed.createComponent(DocumentWizard);
    fixture.componentRef.setInput('sessionId', 9);
    fixture.componentRef.setInput('conversationId', 'chat-9');
    fixture.componentRef.setInput('instruction', 'Make me a product brochure');
    fixture.componentRef.setInput('response', response);
    fixture.detectChanges();
  });
  afterEach(() => {
    chat.result.complete();
    fixture.destroy();
  });
  function click(text: string) {
    const root: HTMLElement = fixture.nativeElement;
    const button = Array.from(root.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === text,
    );
    expect(button).toBeDefined();
    button?.click();
    fixture.detectChanges();
  }
  it('renders options and recommendation without answering automatically', async () => {
    const root: HTMLElement = fixture.nativeElement;
    expect(root.textContent).toContain('Recommended');
    expect(root.textContent).toContain('Editable document');
    expect(root.querySelectorAll('input[type=radio]').length).toBe(3);
    expect(chat.automateDocument).not.toHaveBeenCalled();
    const results = await axe.run(root, { rules: { 'color-contrast': { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
  it('Next submits the selected label and blocks duplicate requests while busy', () => {
    fixture.componentInstance.choose('word');
    click('Next');
    fixture.componentInstance.next();
    expect(chat.automateDocument).toHaveBeenCalledExactlyOnceWith(9, 'Word', 'chat-9', false);
    expect(fixture.nativeElement.querySelector('button').disabled).toBe(true);
  });
  it('accepts Other text and retains prior choices in the visible summary', () => {
    fixture.componentRef.setInput('choices', [
      { question: 'Audience?', answer: 'Restaurant owners' },
    ]);
    fixture.detectChanges();
    fixture.componentInstance.chooseOther();
    fixture.componentInstance.other.setValue('Plain text please');
    click('Next');
    expect(chat.automateDocument).toHaveBeenCalledWith(9, 'Plain text please', 'chat-9', false);
    expect(fixture.nativeElement.textContent).toContain('Restaurant owners');
  });
  it('falls back to a normal text answer when structured choices are absent', () => {
    fixture.componentRef.setInput('response', {
      session_id: 9,
      status: 'clarification_required',
      response: 'What title?',
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('input[type=radio]')).toBeNull();
    expect(fixture.nativeElement.querySelector('input[type=text]')).toBeTruthy();
    fixture.componentInstance.other.setValue('Product guide');
    click('Next');
    expect(chat.automateDocument).toHaveBeenCalledWith(9, 'Product guide', 'chat-9', false);
  });
  it('Build sends confirm true with the same last instruction and a review summary', () => {
    fixture.componentRef.setInput('instruction', 'Word');
    fixture.componentRef.setInput('response', {
      session_id: 9,
      status: 'ready_for_review',
      response: 'Review',
      plan_summary: 'Create brochure.docx (DOCX)',
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Create brochure.docx (DOCX)');
    click('Build my document file');
    expect(chat.automateDocument).toHaveBeenCalledExactlyOnceWith(9, 'Word', 'chat-9', true);
    expect(fixture.nativeElement.querySelector('[role=status]')).toBeTruthy();
  });
  it('keeps the card retryable after an HTTP error', () => {
    fixture.componentInstance.choose('pdf');
    click('Next');
    chat.result.error(new Error('Unavailable'));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role=alert]')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('button').disabled).toBe(false);
  });
});
