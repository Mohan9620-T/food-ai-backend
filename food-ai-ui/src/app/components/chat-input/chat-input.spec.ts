import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { vi } from 'vitest';

import { ChatInput } from './chat-input';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';

class ChatServiceStub {
  readonly isResponding = signal(false);
  readonly editingMessage = signal<null>(null);
  readonly analyzingImage = signal(false);
  readonly retryMessage = signal<null>(null);
}

describe('ChatInput image drag and drop', () => {
  let fixture: ComponentFixture<ChatInput>;
  let component: ChatInput;

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
        { provide: Router, useValue: { navigate: vi.fn() } }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(ChatInput);
    component = fixture.componentInstance;
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
