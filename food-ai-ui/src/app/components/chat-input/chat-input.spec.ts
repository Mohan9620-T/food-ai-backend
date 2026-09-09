import { computed, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { vi } from 'vitest';
import { Observable, of, Subject } from 'rxjs';

import { ChatInput } from './chat-input';
import { ChatService, ChatStreamError } from '../../services/chat';
import { ChatMessage, ChatRequest, ChatResponse } from '../../models/chat';
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
  readonly messages = signal<ChatMessage[]>([]);
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
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(null, 'Create a weekly meal planner', 'docx', 'conversation-1', 'ai');
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
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(42, 'Create a report from these notes', 'pdf', 'conversation-1', 'ai');
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
    component.documentForm.setValue({ instruction: 'Create a report', format: 'docx', mode: 'ai' });
    component.submitDocument();
    generation.error(new HttpErrorResponse({ status: 503, error: { detail: 'Document generation is unavailable. Please try again.' } }));
    expect(component.showDocumentCreator).toBe(true);
    expect(component.isSending()).toBe(false);
    expect(component.documentForm.getRawValue()).toEqual({ instruction: 'Create a report', format: 'docx', mode: 'ai' });
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

  it('offers an explicit export mode and sends the literal textarea without an import or chat request', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    component.message = '  Header\n\nExact content.  ';
    component.createDocument();
    const mode = fixture.nativeElement.querySelector('#document-mode') as HTMLSelectElement;
    expect(mode.value).toBe('ai');
    expect(mode.labels?.[0].textContent).toBe('Creation mode');
    mode.value = 'export';
    mode.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('#document-source-help').textContent).toContain('does not summarize, rewrite, or use conversation history or attached files');
    const instruction = fixture.nativeElement.querySelector('#document-instruction') as HTMLTextAreaElement;
    expect(instruction.labels?.[0].textContent).toBe('Text to export');
    expect(instruction.value).toBe('  Header\n\nExact content.  ');
    component.submitDocument();
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(null, '  Header\n\nExact content.  ', 'pdf', 'conversation-1', 'export');
    expect(chatService.uploadDocument).not.toHaveBeenCalled();
    expect(chatService.streamMessage).not.toHaveBeenCalled();
    expect(component.isSending()).toBe(false);
    expect(component.documentForm.controls.mode.value).toBe('export');
  });

  it.each(['notes.pdf', 'meal.png'])('does not import or discard %s in export mode', filename => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const file = new File(['source bytes'], filename);
    component.handleDrop(dropEvent([file]));
    component.createDocument();
    component.documentForm.patchValue({ instruction: 'Exact content', mode: 'export' });
    component.submitDocument();
    expect(chatService.uploadDocument).not.toHaveBeenCalled();
    expect(chatService.generateDocument).not.toHaveBeenCalled();
    expect(chatService.addMessage).not.toHaveBeenCalled();
    expect((component.selectedDocument ?? component.selectedImage)?.name).toBe(filename);
    expect(component.imageError).toContain('Remove the attached file');
    expect(component.documentForm.controls.instruction.value).toBe('Exact content');
    expect(component.showDocumentCreator).toBe(true);
    expect(component.isSending()).toBe(false);
  });

  it('retains the export draft and mode after failure and allows retry without duplicate content', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const generation = new Subject<ChatResponse>();
    chatService.generateDocument.mockReturnValueOnce(generation);
    component.createDocument();
    const draft = { instruction: '  Exact content  ', format: 'docx' as const, mode: 'export' as const };
    component.documentForm.setValue(draft);
    component.submitDocument();
    generation.error(new HttpErrorResponse({ status: 503, error: { detail: 'Document renderer unavailable. Please retry.' } }));
    fixture.detectChanges();
    expect(component.documentForm.getRawValue()).toEqual(draft);
    expect(component.isSending()).toBe(false);
    expect(component.showDocumentCreator).toBe(true);
    expect((fixture.nativeElement.querySelector('#document-creator button[type="submit"]') as HTMLButtonElement).disabled).toBe(false);
    expect((fixture.nativeElement.querySelector('#document-mode') as HTMLSelectElement).disabled).toBe(false);
    component.submitDocument();
    expect(chatService.generateDocument).toHaveBeenCalledTimes(2);
    expect(chatService.generateDocument).toHaveBeenLastCalledWith(null, draft.instruction, 'docx', 'conversation-1', 'export');
    expect(chatService.addMessage).toHaveBeenCalledExactlyOnceWith({ sender: 'user', text: draft.instruction }, 'conversation-1');
    expect(component.imageError).toBeNull();
  });

  it('keeps document modes and failed exports scoped to their conversation', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const generation = new Subject<ChatResponse>();
    chatService.generateDocument.mockReturnValueOnce(generation);
    component.createDocument();
    component.documentForm.patchValue({ instruction: 'First conversation text', mode: 'export' });
    component.submitDocument();
    chatService.activeConversationId.set('conversation-2');
    component.createDocument();
    component.documentForm.controls.instruction.setValue('Write a different report');
    generation.error(new HttpErrorResponse({ status: 503, error: { detail: 'Export failed.' } }));
    expect(component.imageError).toBeNull();
    expect(component.documentForm.controls.mode.value).toBe('ai');
    expect(component.documentForm.controls.instruction.value).toBe('Write a different report');
    chatService.activeConversationId.set('conversation-1');
    expect(component.documentForm.controls.mode.value).toBe('export');
    expect(component.documentForm.controls.instruction.value).toBe('First conversation text');
    expect(component.imageError).toBe('Export failed.');
    expect(component.isSending()).toBe(false);
  });

  it('rejects empty or oversized export text without sending a request', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    component.createDocument();
    component.documentForm.patchValue({ mode: 'export', instruction: '   ' });
    component.submitDocument();
    expect(component.imageError).toContain('Enter the text to export');
    component.documentForm.controls.instruction.setValue('a'.repeat(2001));
    component.submitDocument();
    expect(component.imageError).toContain('2,000 characters');
    expect(chatService.generateDocument).not.toHaveBeenCalled();
  });

  it('exports an attached file through import without AI and its saved source ID, leaving unrelated drafts intact', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const attachment = { id: 211, filename: 'notes.pdf', content_type: 'application/pdf', file_size: 20, kind: 'uploaded' as const };
    chatService.uploadDocument.mockReturnValueOnce(of({ response: 'File saved.', session_id: 42, attachment, analysis_status: 'skipped' }));
    const file = new File(['%PDF'], 'notes.pdf');
    component.handleDrop(dropEvent([file]));
    component.message = 'Keep this message draft';
    component.createDocument();
    component.documentForm.patchValue({ mode: 'export-file', format: 'docx', instruction: 'Keep these AI instructions too' });
    fixture.detectChanges();
    expect((fixture.nativeElement.querySelector('#document-instruction') as HTMLTextAreaElement).disabled).toBe(true);
    expect(fixture.nativeElement.querySelector('#document-source-help').textContent).toContain('notes.pdf');
    component.submitDocument();
    expect(chatService.uploadDocument).toHaveBeenCalledExactlyOnceWith(file, null, 'conversation-1', false);
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(42, '', 'docx', 'conversation-1', 'export', 211);
    expect(chatService.streamMessage).not.toHaveBeenCalled();
    expect(component.selectedDocument).toBeNull();
    expect(component.message).toBe('Keep this message draft');
    expect(component.documentForm.controls.instruction.value).toBe('Keep these AI instructions too');
    expect(component.isSending()).toBe(false);
  });

  it('exports a file with no instructions and enables the retained draft when switching back to AI', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const attachment = { id: 212, filename: 'notes.txt', content_type: 'text/plain', file_size: 20, kind: 'uploaded' as const };
    chatService.uploadDocument.mockReturnValueOnce(of({ response: 'File saved.', session_id: 42, attachment, analysis_status: 'skipped' }));
    component.handleDrop(dropEvent([new File(['notes'], 'notes.txt')]));
    component.createDocument();
    component.documentForm.controls.mode.setValue('export-file');
    component.submitDocument();
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(42, '', 'pdf', 'conversation-1', 'export', 212);
    component.createDocument();
    expect(document.activeElement?.id).toBe('document-mode');
    component.documentForm.controls.mode.setValue('ai');
    expect(component.documentForm.controls.instruction.enabled).toBe(true);
    expect(component.documentForm.invalid).toBe(true);
  });

  it('clears the previous AI timeout when the user selects a no-AI recovery mode', () => {
    component.createDocument();
    component.imageError = 'Document AI generation timed out.';
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.image-error').textContent).toContain('timed out');

    component.documentForm.controls.mode.setValue('export');
    fixture.detectChanges();

    expect(component.imageError).toBeNull();
    expect(fixture.nativeElement.querySelector('.image-error')).toBeNull();
  });

  it('stops the AI generation chain after a saved upload reports unavailable analysis, then exports the saved file without reupload', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const attachment = { id: 213, filename: 'notes.xlsx', content_type: 'application/octet-stream', file_size: 20, kind: 'uploaded' as const };
    chatService.uploadDocument.mockReturnValueOnce(of({ response: 'File saved. AI unavailable.', session_id: 42, attachment, analysis_status: 'unavailable' }));
    component.handleDrop(dropEvent([new File(['notes'], 'notes.xlsx')]));
    component.createDocument();
    component.documentForm.controls.instruction.setValue('Write a report from this file');
    component.submitDocument();
    expect(chatService.generateDocument).not.toHaveBeenCalled();
    expect(component.isSending()).toBe(false);
    expect(component.selectedDocument).toBeNull();
    expect(component.exportSourceDocument).toEqual(attachment);
    expect(component.imageError).toContain('File saved, but AI analysis is unavailable');
    expect(component.showDocumentCreator).toBe(true);
    expect(component.documentForm.controls.instruction.value).toBe('Write a report from this file');
    expect(component.documentForm.controls.mode.value).toBe('ai');
    chatService.getActiveSessionId.mockReturnValue(42);
    component.documentForm.controls.mode.setValue('export-file');
    component.submitDocument();
    expect(chatService.uploadDocument).toHaveBeenCalledTimes(1);
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(42, '', 'pdf', 'conversation-1', 'export', 213);
    expect(component.imageError).toBeNull();
  });

  it('retries failed file exports from the saved source without duplicate uploads or generation messages', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const attachment = { id: 214, filename: 'notes.csv', content_type: 'text/csv', file_size: 20, kind: 'uploaded' as const };
    chatService.uploadDocument.mockReturnValueOnce(of({ response: 'File saved.', session_id: 42, attachment, analysis_status: 'skipped' }));
    const generation = new Subject<ChatResponse>();
    chatService.generateDocument.mockReturnValueOnce(generation);
    component.handleDrop(dropEvent([new File(['notes'], 'notes.csv')]));
    component.createDocument();
    component.documentForm.controls.mode.setValue('export-file');
    component.submitDocument();
    generation.error(new HttpErrorResponse({ status: 503, error: { detail: 'Renderer unavailable.' } }));
    expect(component.exportSourceDocument).toEqual(attachment);
    expect(component.documentForm.controls.mode.value).toBe('export-file');
    expect(component.isSending()).toBe(false);
    component.submitDocument();
    expect(chatService.uploadDocument).toHaveBeenCalledTimes(1);
    expect(chatService.generateDocument).toHaveBeenCalledTimes(2);
    expect(chatService.addMessage).toHaveBeenCalledTimes(2);
  });

  it('finds an uploaded source in current persisted history and does not use it in another chat', () => {
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    const attachment = { id: 215, filename: 'restored.pdf', content_type: 'application/pdf', file_size: 20, kind: 'uploaded' as const };
    chatService.messages.set([{ sender: 'user', text: 'Old import', attachment }]);
    chatService.getActiveSessionId.mockReturnValue(42);
    component.createDocument();
    component.documentForm.controls.mode.setValue('export-file');
    component.submitDocument();
    expect(chatService.uploadDocument).not.toHaveBeenCalled();
    expect(chatService.generateDocument).toHaveBeenCalledExactlyOnceWith(42, '', 'pdf', 'conversation-1', 'export', 215);
    chatService.activeConversationId.set('conversation-2');
    chatService.messages.set([]);
    component.createDocument();
    component.documentForm.controls.mode.setValue('export-file');
    component.submitDocument();
    expect(component.imageError).toContain('import one into this conversation');
    expect(chatService.generateDocument).toHaveBeenCalledTimes(1);
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
