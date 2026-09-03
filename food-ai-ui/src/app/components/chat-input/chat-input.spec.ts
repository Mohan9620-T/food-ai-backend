import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { vi } from 'vitest';

import { ChatInput } from './chat-input';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { SpeechRecognitionService } from '../../services/speech-recognition';

class ChatServiceStub {
  readonly isResponding = signal(false);
  readonly editingMessage = signal<null>(null);
  readonly analyzingImage = signal(false);
  readonly retryMessage = signal<null>(null);
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
    expect(fixture.nativeElement.querySelector('button[aria-label="Attach image"]')).toBeTruthy();
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
    const chatService = TestBed.inject(ChatService) as unknown as ChatServiceStub & {
      getActiveConversationId: () => string;
      addMessage: ReturnType<typeof vi.fn>;
      getHistory: () => [];
      getReferenceHistory: () => [];
      startResponse: ReturnType<typeof vi.fn>;
      streamMessage: () => Promise<void>;
      getPendingResponse: () => null;
    };
    chatService.getActiveConversationId = () => 'conversation-1';
    chatService.addMessage = vi.fn();
    chatService.getHistory = () => [];
    chatService.getReferenceHistory = () => [];
    chatService.startResponse = vi.fn();
    chatService.streamMessage = () => Promise.resolve();
    chatService.getPendingResponse = () => null;

    component.message = 'dictated message';
    component.toggleDictation();
    component.sendMessage();

    expect(speechService.stop).toHaveBeenCalledOnce();
    expect(component.message).toBe('');
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

  it('rejects a dropped non-image file', () => {
    const textFile = new File(['not an image'], 'notes.txt', { type: 'text/plain' });

    component.handleDrop(dropEvent([textFile]));

    expect(component.selectedImage).toBeNull();
    expect(component.imageError).toContain('Unsupported file type');
  });

  it('rejects dropping more than one image', () => {
    const first = new File([new Uint8Array([1])], 'one.png', { type: 'image/png' });
    const second = new File([new Uint8Array([2])], 'two.png', { type: 'image/png' });
    component.handleDrop(dropEvent([first, second]));
    expect(component.selectedImage).toBeNull();
    expect(component.imageError).toBe('Drop one image at a time.');
  });
});
