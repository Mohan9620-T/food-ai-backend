import { computed, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { vi } from 'vitest';
import { Observable, of, Subject } from 'rxjs';

import { ChatInput } from './chat-input';
import { ChatService, ChatStreamError } from '../../services/chat';
import { ChatRequest, ChatResponse } from '../../models/chat';
import { AuthService } from '../../services/auth';
import { SpeechRecognitionService } from '../../services/speech-recognition';

class ChatServiceStub {
  readonly activeConversationId = signal('conversation-1');
  readonly pendingConversationId = signal<string | null>(null);
  readonly isResponding = computed(() => this.pendingConversationId() === this.activeConversationId());
  readonly editingMessage = signal<null>(null);
  readonly analyzingImage = signal(false);
  readonly processingDocument = signal(false);
  readonly retryMessage = signal<null>(null);
  readonly finishResponse = vi.fn((conversationId: string) => {
    if (this.pendingConversationId() === conversationId) this.pendingConversationId.set(null);
  });
  readonly startResponse = vi.fn((conversationId: string) => this.pendingConversationId.set(conversationId));
  readonly getActiveConversationId = vi.fn(() => this.activeConversationId());
  readonly getActiveSessionId = vi.fn((): number | null => null);
  readonly getHistory = vi.fn(() => []);
  readonly getReferenceHistory = vi.fn(() => []);
  readonly getPendingResponse = vi.fn(() => this.pendingConversationId() ? {
    conversationId: this.pendingConversationId(), request: { message: '', history: [], referenceHistory: [] }
  } : null);
  readonly addMessage = vi.fn();
  readonly stopStreaming = vi.fn();
  readonly streamMessage = vi.fn(() => Promise.resolve());
  readonly uploadDocument = vi.fn((): Observable<ChatResponse> => of({ response: 'Imported', session_id: 12 }));
  readonly generateDocument = vi.fn((): Observable<ChatResponse> => of({ response: 'Created', session_id: 12 }));
}

class SpeechRecognitionServiceStub {
  isSupported = true;
  readonly isListening = signal(false);
  readonly error = signal<string | null>(null);
  readonly start = vi.fn((onInterim: (text: string) => void, onFinal: (text: string) => void) => {
    this.onInterim = onInterim;
    this.onFinal = onFinal;
    this.isListening.set(true);
  });
  readonly stop = vi.fn(() => this.isListening.set(false));
  readonly clearError = vi.fn(() => this.error.set(null));
  onInterim: (text: string) => void = () => undefined;
  onFinal: (text: string) => void = () => undefined;
}

describe('ChatInput image drag and drop', () => {
  let fixture: ComponentFixture<ChatInput>;
  let component: ChatInput;
  let speechService: SpeechRecognitionServiceStub;

  beforeEach(async () => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:test-image')
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn()
    });

    await TestBed.configureTestingModule({
      imports: [ChatInput],
      providers: [
        { provide: ChatService, useClass: ChatServiceStub },
        { provide: AuthService, useValue: { getToken: () => 'token' } },
        { provide: SpeechRecognitionService, useClass: SpeechRecognitionServiceStub },
        { provide: Router, useValue: { navigate: vi.fn() } }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(ChatInput);
    component = fixture.componentInstance;
    speechService = TestBed.inject(SpeechRecognitionService) as unknown as SpeechRecognitionServiceStub;
    fixture.detectChanges();
  });

  function dropEvent(files: File[]): DragEvent {
    return {
      preventDefault: vi.fn(),
      dataTransfer: { types: ['Files'], files, dropEffect: 'none' }
    } as unknown as DragEvent;
  }

  it('renders the message composer and attachment control', () => {
    expect(fixture.nativeElement.querySelector('textarea[aria-label="Message"]')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('button[aria-label="Attach image or document"]')).toBeTruthy();
  });

  it('renders the mic only when speech recognition is supported', () => {
    expect(fixture.nativeElement.querySelector('button[aria-label="Start voice input"]')).toBeTruthy();

    (component as unknown as { speechSupported: boolean }).speechSupported = false;
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.mic-button')).toBeNull();
  });

  it('starts and stops dictation from the mic control', () => {
    let micButton = fixture.nativeElement.querySelector('.mic-button') as HTMLButtonElement;
    expect(micButton.getAttribute('aria-label')).toBe('Start voice input');
    expect(micButton.getAttribute('aria-pressed')).toBe('false');

    component.toggleDictation();
    fixture.detectChanges();
    expect(speechService.start).toHaveBeenCalledOnce();
    expect(component.isListening()).toBe(true);
    micButton = fixture.nativeElement.querySelector('.mic-button') as HTMLButtonElement;
    expect(micButton.getAttribute('aria-label')).toBe('Stop voice input');
    expect(micButton.getAttribute('aria-pressed')).toBe('true');

    component.toggleDictation();
    expect(speechService.stop).toHaveBeenCalledOnce();
    expect(component.isListening()).toBe(false);
  });

  it('appends interim and final speech after existing typed text', () => {
    component.message = 'Already typed';
    component.toggleDictation();

    speechService.onInterim('hello');
    expect(component.message).toBe('Already typed hello');
    expect((fixture.nativeElement.querySelector('textarea') as HTMLTextAreaElement).value)
      .toBe('Already typed hello');

    speechService.onFinal('hello ');
    speechService.onInterim('world');
    expect(component.message).toBe('Already typed hello world');
  });

  it('stops dictation before sending so late speech cannot restore the cleared composer', () => {
    component.message = 'dictated message';
    component.toggleDictation();
    component.sendMessage();

    expect(speechService.stop).toHaveBeenCalledOnce();
    expect(component.message).toBe('');
  });

  it('displays the stream failure in its original conversation without overwriting another composer', async () => {
    const detail = 'The response reached its output limit before it finished. Please retry with a shorter request.';
    const chatService = TestBed.inject(ChatService);
    chatService.streamMessage = vi.fn().mockRejectedValue(new ChatStreamError(detail));
    chatService.getActiveConversationId = () => 'another-conversation';
    chatService.addMessage = vi.fn();
    const request = { message: 'Original question', history: [], referenceHistory: [] };

    await (component as unknown as {
      requestResponse: (conversationId: string, request: ChatRequest) => Promise<void>;
    }).requestResponse('original-conversation', request);

    expect(chatService.addMessage).toHaveBeenCalledWith({ sender: 'bot', text: `Response interrupted: ${detail}` }, 'original-conversation');
    expect(chatService.finishResponse).toHaveBeenCalledExactlyOnceWith('original-conversation');
    expect(component.message).toBe('');
  });

  it('does not append a failure message when the user stops the response', async () => {
    const chatService = TestBed.inject(ChatService);
    chatService.streamMessage = vi.fn().mockRejectedValue(new DOMException('Stopped', 'AbortError'));
    chatService.addMessage = vi.fn();

    await (component as unknown as {
      requestResponse: (conversationId: string, request: ChatRequest) => Promise<void>;
    }).requestResponse('original-conversation', { message: 'Question', history: [], referenceHistory: [] });

    expect(chatService.addMessage).not.toHaveBeenCalled();
    expect(chatService.finishResponse).toHaveBeenCalledExactlyOnceWith('original-conversation');
  });

  it('does not duplicate an interruption notice already attached to the partial response', async () => {
    const chatService = TestBed.inject(ChatService);
    chatService.streamMessage = vi.fn().mockRejectedValue(new ChatStreamError('Please retry.', true));
    chatService.getActiveConversationId = () => 'original-conversation';
    chatService.addMessage = vi.fn();

    await (component as unknown as {
      requestResponse: (conversationId: string, request: ChatRequest) => Promise<void>;
    }).requestResponse('original-conversation', { message: 'Question', history: [], referenceHistory: [] });

    expect(chatService.addMessage).not.toHaveBeenCalled();
    expect(chatService.finishResponse).toHaveBeenCalledExactlyOnceWith('original-conversation');
    expect(component.message).toBe('Question');
  });

  it('attaches a valid image dropped from the file system', () => {
    const image = new File([new Uint8Array([1, 2, 3])], 'meal.png', {
      type: 'image/png'
    });
    const event = dropEvent([image]);

    component.handleDrop(event);

    expect(event.preventDefault).toHaveBeenCalled();
    expect(component.selectedImage).toBe(image);
    expect(component.imagePreviewUrl).toBe('blob:test-image');
    expect(component.imageError).toBeNull();
  });

  it('accepts a supported document dropped from the file system', () => {
    const textFile = new File(['nutrition notes'], 'notes.txt', { type: 'text/plain' });

    component.handleDrop(dropEvent([textFile]));

    expect(component.selectedImage).toBeNull();
    expect(component.selectedDocument).toBe(textFile);
    expect(component.imageError).toBeNull();
  });

  it('rejects a dropped unsupported file', () => {
    const executable = new File(['unsafe'], 'program.exe', { type: 'application/octet-stream' });
    component.handleDrop(dropEvent([executable]));
    expect(component.selectedDocument).toBeNull();
    expect(component.imageError).toContain('Upload an image');
  });

  it('rejects dropping more than one image', () => {
    const first = new File([new Uint8Array([1])], 'one.png', { type: 'image/png' });
    const second = new File([new Uint8Array([2])], 'two.png', { type: 'image/png' });
    component.handleDrop(dropEvent([first, second]));
    expect(component.selectedImage).toBeNull();
    expect(component.imageError).toBe('Drop one image or document at a time.');
  });

  it.each(['PDF', 'DOCX', 'TXT', 'CSV', 'XLSX'])('accepts a %s document without a MIME type through the picker and drop', (extension) => {
    const file = new File(['document bytes'], `notes.${extension}`);
    const picker = fixture.nativeElement.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(picker, 'files', { value: [file], configurable: true });
    picker.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(component.selectedDocument).toBe(file);
    expect(fixture.nativeElement.querySelector('.document-preview').textContent).toContain(file.name);
    expect(picker.value).toBe('');

    component.removeDocument();
    component.handleDrop(dropEvent([file]));
    expect(component.selectedDocument).toBe(file);
    expect(component.imageError).toBeNull();
  });

  it('keeps image and document attachments mutually exclusive', () => {
    const file = new File(['%PDF'], 'notes.pdf', { type: 'application/pdf' });
    const image = new File(['image'], 'meal.png', { type: 'image/png' });
    component.handleDrop(dropEvent([image]));
    component.handleDrop(dropEvent([file]));
    expect(component.selectedImage).toBeNull();
    expect(component.imagePreviewUrl).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-image');
    component.handleDrop(dropEvent([image]));
    expect(component.selectedDocument).toBeNull();
    expect(component.selectedImage).toBe(image);
  });

  it('infers a supported image MIME type when the browser omits it', () => {
    const image = new File(['image'], 'meal.PNG');
    component.handleDrop(dropEvent([image]));
    expect(component.selectedImage?.name).toBe('meal.PNG');
    expect(component.selectedImage?.type).toBe('image/png');
    expect(component.selectedDocument).toBeNull();
    expect(component.imageError).toBeNull();
  });

  it('prevents file navigation while a document is processing', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    chatService.processingDocument.set(true);
    const event = dropEvent([new File(['%PDF'], 'notes.pdf')]);
    component.handleDragEnter(event);
    component.handleDragOver(event);
    component.handleDrop(event);
    expect(event.preventDefault).toHaveBeenCalledTimes(3);
    expect(event.dataTransfer?.dropEffect).toBe('none');
    expect(component.selectedDocument).toBeNull();
  });

  it('retains the file and message after a failed import and retries without a duplicate user message', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const upload = new Subject<ChatResponse>();
    chatService.uploadDocument.mockReturnValueOnce(upload);
    const file = new File(['%PDF'], 'notes.pdf');
    component.handleDrop(dropEvent([file]));
    component.message = 'Read all pages';
    component.sendMessage();
    expect(component.isSending()).toBe(true);
    upload.error(new HttpErrorResponse({ status: 422, error: { detail: 'The PDF is password protected.' } }));
    expect(component.selectedDocument).toBe(file);
    expect(component.message).toBe('Read all pages');
    expect(component.imageError).toBe('The PDF is password protected.');
    expect(component.isSending()).toBe(false);
    component.sendMessage();
    expect(chatService.uploadDocument).toHaveBeenCalledTimes(2);
    expect(chatService.addMessage).toHaveBeenCalledExactlyOnceWith({ sender: 'user', text: 'Read all pages' }, 'conversation-1');
    expect(component.selectedDocument).toBeNull();
    expect(component.message).toBe('');
    expect(component.imageError).toBeNull();
  });

  it('opens a focused creation form in a new chat and submits the selected DOCX format', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const createButton = fixture.nativeElement.querySelector('button[aria-label="Create document"]') as HTMLButtonElement;
    createButton.click();
    fixture.detectChanges();
    const instruction = fixture.nativeElement.querySelector('#document-instruction') as HTMLTextAreaElement;
    expect(document.activeElement).toBe(instruction);
    expect(component.imageError).toBeNull();
    instruction.value = 'Create a weekly meal planner';
    instruction.dispatchEvent(new Event('input'));
    const format = fixture.nativeElement.querySelector('#document-format') as HTMLSelectElement;
    format.value = 'docx';
    format.dispatchEvent(new Event('change'));
    const form = fixture.nativeElement.querySelector('#document-creator') as HTMLFormElement;
    form.dispatchEvent(new Event('submit', { cancelable: true }));
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(null, 'Create a weekly meal planner', 'docx', 'conversation-1');
    expect(component.showDocumentCreator).toBe(false);
    expect(component.isSending()).toBe(false);
  });

  it('imports the staged file before creating a PDF with its resulting session', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const upload = new Subject<ChatResponse>();
    const generation = new Subject<ChatResponse>();
    chatService.uploadDocument.mockReturnValue(upload);
    chatService.generateDocument.mockReturnValue(generation);
    const file = new File(['notes'], 'notes.txt');
    component.handleDrop(dropEvent([file]));
    component.createDocument();
    component.documentForm.controls.instruction.setValue('Create a report from these notes');
    component.submitDocument();
    expect(chatService.generateDocument).not.toHaveBeenCalled();
    expect(component.isSending()).toBe(true);
    upload.next({ response: 'Imported', session_id: 42 });
    upload.complete();
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(42, 'Create a report from these notes', 'pdf', 'conversation-1');
    expect(component.selectedDocument).toBeNull();
    expect(component.isSending()).toBe(true);
    generation.next({ response: 'Created', session_id: 42 });
    generation.complete();
    expect(component.isSending()).toBe(false);
    expect(component.showDocumentCreator).toBe(false);
  });

  it('keeps the creation form and upload available when import fails before generation', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const upload = new Subject<ChatResponse>();
    chatService.uploadDocument.mockReturnValueOnce(upload);
    const file = new File(['%PDF'], 'notes.pdf');
    component.handleDrop(dropEvent([file]));
    component.createDocument();
    component.documentForm.controls.instruction.setValue('Create a summary');
    component.submitDocument();
    upload.error(new HttpErrorResponse({ status: 503, error: { detail: 'Document reader unavailable. Please try again.' } }));
    expect(chatService.generateDocument).not.toHaveBeenCalled();
    expect(component.selectedDocument).toBe(file);
    expect(component.documentForm.controls.instruction.value).toBe('Create a summary');
    expect(component.showDocumentCreator).toBe(true);
    component.submitDocument();
    expect(chatService.addMessage).toHaveBeenCalledTimes(2);
    expect(chatService.generateDocument).toHaveBeenCalledOnce();
  });

  it('cancels a document request and leaves the attachment available for retry', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const upload = new Subject<ChatResponse>();
    chatService.uploadDocument.mockReturnValue(upload);
    const file = new File(['%PDF'], 'notes.pdf');
    component.handleDrop(dropEvent([file]));
    component.sendMessage();
    component.stopResponse();
    expect(upload.observed).toBe(false);
    expect(component.selectedDocument).toBe(file);
    expect(component.imageError).toContain('cancelled');
    expect(component.isSending()).toBe(false);
    expect(chatService.stopStreaming).not.toHaveBeenCalled();
  });

  it('preserves instructions and format after generation fails and retries without duplicating the request message', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const generation = new Subject<ChatResponse>();
    chatService.generateDocument.mockReturnValueOnce(generation);
    component.createDocument();
    component.documentForm.setValue({ instruction: 'Create a report', format: 'docx' });
    component.submitDocument();
    generation.error(new HttpErrorResponse({ status: 503, error: { detail: 'Document generation is unavailable. Please try again.' } }));
    expect(component.showDocumentCreator).toBe(true);
    expect(component.isSending()).toBe(false);
    expect(component.documentForm.getRawValue()).toEqual({ instruction: 'Create a report', format: 'docx' });
    expect(component.imageError).toContain('unavailable');
    component.submitDocument();
    expect(chatService.generateDocument).toHaveBeenCalledTimes(2);
    expect(chatService.addMessage).toHaveBeenCalledExactlyOnceWith({ sender: 'user', text: 'Create a report' }, 'conversation-1');
    expect(component.showDocumentCreator).toBe(false);
  });

  it('cancels generation through the Stop control and keeps the creation instructions', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const generation = new Subject<ChatResponse>();
    chatService.generateDocument.mockReturnValue(generation);
    component.createDocument();
    component.documentForm.controls.instruction.setValue('Create a report');
    component.submitDocument();
    fixture.detectChanges();
    const stop = fixture.nativeElement.querySelector('button[aria-label="Stop generating response"]') as HTMLButtonElement;
    stop.click();
    expect(generation.observed).toBe(false);
    expect(component.isSending()).toBe(false);
    expect(component.documentForm.controls.instruction.value).toBe('Create a report');
    expect(component.showDocumentCreator).toBe(true);
    expect(component.imageError).toContain('cancelled');
  });

  it('keeps upload failures and drafts in their original conversation', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const upload = new Subject<ChatResponse>();
    chatService.uploadDocument.mockReturnValue(upload);
    const file = new File(['%PDF'], 'notes.pdf');
    component.handleDrop(dropEvent([file]));
    component.message = 'Read these notes';
    component.sendMessage();
    chatService.activeConversationId.set('conversation-2');
    component.message = 'Another question';
    upload.error(new HttpErrorResponse({ status: 422, error: { detail: 'Cannot read this file.' } }));
    expect(component.imageError).toBeNull();
    expect(component.selectedDocument).toBeNull();
    expect(component.message).toBe('Another question');
    chatService.activeConversationId.set('conversation-1');
    expect(component.imageError).toBe('Cannot read this file.');
    expect(component.selectedDocument).toBe(file);
    expect(component.message).toBe('Read these notes');
  });

  it('cancels the creation form without sending a request and restores focus', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    component.createDocument();
    component.closeDocumentCreator();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('#document-creator')).toBeNull();
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Create document');
    expect(chatService.generateDocument).not.toHaveBeenCalled();
  });
});
