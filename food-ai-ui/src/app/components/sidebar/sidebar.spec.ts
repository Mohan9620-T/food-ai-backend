import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { vi } from 'vitest';

import { Sidebar } from './sidebar';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ThemeService } from '../../services/theme';

class ChatServiceStub {
  readonly conversations = signal([{ id: '1', title: 'Lunch ideas', messages: [], updatedAt: Date.now() }]);
  readonly activeConversationId = signal<string | null>('1');
  readonly loadingSessions = signal(false);
  readonly createConversation = vi.fn();
  readonly selectConversation = vi.fn();
  readonly renameConversation = vi.fn();
  readonly deleteConversation = vi.fn();
}

describe('Sidebar', () => {
  let fixture: ComponentFixture<Sidebar>;
  let chats: ChatServiceStub;
  const navigate = vi.fn();

  beforeEach(async () => {
    navigate.mockClear();
    await TestBed.configureTestingModule({
      imports: [Sidebar],
      providers: [
        { provide: ChatService, useClass: ChatServiceStub },
        { provide: AuthService, useValue: { currentUserName: signal('Rajesh'), currentUserEmail: signal('rajesh@example.com'), currentUserInitials: signal('R'), logout: vi.fn() } },
        { provide: ThemeService, useValue: { isDark: signal(false), toggle: vi.fn() } },
        { provide: Router, useValue: { navigate } }
      ]
    }).compileComponents();
    fixture = TestBed.createComponent(Sidebar);
    chats = TestBed.inject(ChatService) as unknown as ChatServiceStub;
    fixture.detectChanges();
  });

  it('renders saved conversations and user details', () => {
    expect(fixture.nativeElement.textContent).toContain('Lunch ideas');
    expect(fixture.nativeElement.textContent).toContain('Rajesh');
  });

  it('creates a new conversation and navigates home', () => {
    const button = Array.from(fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>)
      .find((item) => item.textContent?.includes('New chat')) as HTMLButtonElement;
    button.click();
    expect(chats.createConversation).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith(['/']);
  });

  it('does not rename a conversation to a blank title', () => {
    fixture.componentInstance.requestRenameConversation('1', 'Lunch ideas');
    fixture.componentInstance.renameTitle = '   ';
    fixture.componentInstance.confirmRenameConversation();
    expect(chats.renameConversation).not.toHaveBeenCalled();
  });
});
