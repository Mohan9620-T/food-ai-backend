import { Component, ElementRef, HostListener, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ChatService } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ThemeService } from '../../services/theme';

@Component({
  selector: 'app-sidebar',
  imports: [FormsModule],
  templateUrl: './sidebar.html',
  styleUrl: './sidebar.css'
})
export class Sidebar {
  private readonly chatService = inject(ChatService);
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  private readonly host = inject(ElementRef<HTMLElement>);
  readonly themeService = inject(ThemeService);

  readonly conversations = this.chatService.conversations;
  readonly activeConversationId = this.chatService.activeConversationId;
  readonly loadingSessions = this.chatService.loadingSessions;
  readonly openMenuId = signal<string | null>(null);
  readonly profileMenuOpen = signal(false);
  readonly pendingRenameId = signal<string | null>(null);
  readonly pendingDelete = signal<{ id: string; title: string } | null>(null);
  readonly mobileOpen = signal(false);
  renameTitle = '';
  readonly userName = this.authService.currentUserName;
  readonly userEmail = this.authService.currentUserEmail;
  readonly userInitials = this.authService.currentUserInitials;

  createConversation(): void {
    this.chatService.createConversation();
    this.mobileOpen.set(false);
    this.router.navigate(['/']);
  }

  openChat(): void {
    this.mobileOpen.set(false);
    this.router.navigate(['/']);
  }

  selectConversation(conversationId: string): void {
    this.chatService.selectConversation(conversationId);
    this.openMenuId.set(null);
    this.mobileOpen.set(false);
    this.router.navigate(['/']);
  }

  toggleMenu(conversationId: string): void {
    this.openMenuId.update((openMenuId) => openMenuId === conversationId ? null : conversationId);
  }

  toggleMobile(): void {
    this.mobileOpen.update((open) => !open);
  }

  @HostListener('document:keydown.escape')
  closeOverlays(): void {
    this.openMenuId.set(null);
    this.profileMenuOpen.set(false);
    this.mobileOpen.set(false);
  }

  @HostListener('document:pointerdown', ['$event'])
  closeMenusOnOutsidePointer(event: PointerEvent): void {
    if (event.target instanceof Node && this.host.nativeElement.contains(event.target)) return;
    this.openMenuId.set(null);
    this.profileMenuOpen.set(false);
  }

  formatUpdatedAt(timestamp: number): string {
    const date = new Date(timestamp);
    const today = new Date();
    if (date.toDateString() === today.toDateString()) {
      return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date);
    }
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
  }

  requestRenameConversation(conversationId: string, currentTitle: string): void {
    this.pendingRenameId.set(conversationId);
    this.renameTitle = currentTitle;
    this.openMenuId.set(null);
  }

  cancelRenameConversation(): void {
    this.pendingRenameId.set(null);
    this.renameTitle = '';
  }

  confirmRenameConversation(): void {
    const conversationId = this.pendingRenameId();
    const title = this.renameTitle.trim();
    if (!conversationId || !title) return;

    this.chatService.renameConversation(conversationId, title);
    this.cancelRenameConversation();
  }

  requestDeleteConversation(conversationId: string, title: string): void {
    this.pendingDelete.set({ id: conversationId, title });
    this.openMenuId.set(null);
  }

  cancelDeleteConversation(): void {
    this.pendingDelete.set(null);
  }

  confirmDeleteConversation(): void {
    const conversation = this.pendingDelete();
    if (!conversation) return;

    this.chatService.deleteConversation(conversation.id);
    this.pendingDelete.set(null);
  }

  toggleProfileMenu(): void {
    this.profileMenuOpen.update((isOpen) => !isOpen);
  }

  logout(): void {
    this.profileMenuOpen.set(false);
    this.authService.logout();
    this.router.navigate(['/login']);
  }
}
