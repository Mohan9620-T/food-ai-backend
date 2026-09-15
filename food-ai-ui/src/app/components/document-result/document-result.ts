import { Component, DestroyRef, computed, inject, input, output, signal } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ChatDocumentAttachment } from '../../models/chat';
import { environment } from '../../../environments/environment';
import { SpreadsheetPreview } from '../spreadsheet-preview/spreadsheet-preview';

@Component({
  selector: 'app-document-result',
  imports: [SpreadsheetPreview],
  templateUrl: './document-result.html',
  styleUrl: './document-result.css',
})
export class DocumentResult {
  readonly attachment = input.required<ChatDocumentAttachment>();
  readonly download = output<void>();
  private readonly http = inject(HttpClient);
  private readonly document = inject(DOCUMENT);
  private readonly destroyRef = inject(DestroyRef);
  readonly preview = signal(false);
  readonly opening = signal(false);
  readonly error = signal<string | null>(null);
  readonly isWorkbook = computed(
    () =>
      this.attachment().content_type ===
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  );
  readonly badge = computed(
    () =>
      ({
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'EXCEL',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'WORD',
        'application/pdf': 'PDF',
        'text/csv': 'CSV',
        'text/plain': 'TEXT',
        'text/markdown': 'MARKDOWN',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'POWERPOINT',
      })[this.attachment().content_type] ?? 'FILE',
  );
  private urls: string[] = [];
  constructor() {
    this.destroyRef.onDestroy(() => this.urls.forEach((url) => URL.revokeObjectURL(url)));
  }
  view(): void {
    if (this.isWorkbook()) {
      this.preview.update((open) => !open);
      return;
    }
    const tab = this.document.defaultView?.open('about:blank', '_blank');
    if (!tab) {
      this.error.set('Allow a new tab to view this file, or use Download.');
      return;
    }
    tab.opener = null;
    this.opening.set(true);
    this.error.set(null);
    this.http
      .get(`${environment.apiUrl}/chat/documents/${this.attachment().id}/download?inline=true`, {
        responseType: 'blob',
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (blob) => {
          const url = URL.createObjectURL(blob);
          this.urls.push(url);
          tab.location.href = url;
          this.opening.set(false);
        },
        error: () => {
          tab.close();
          this.opening.set(false);
          this.error.set('The file could not be opened. Please retry.');
        },
      });
  }
}
