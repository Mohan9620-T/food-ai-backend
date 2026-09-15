import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { environment } from '../../../environments/environment';

export interface WorkbookPage {
  sha256: string;
  sheets: { name: string; rows: number; columns: number }[];
  sheet: string;
  row_offset: number;
  column_offset: number;
  rows: (string | number | boolean | null)[][];
  formulas: (string | null)[][];
}

@Component({
  selector: 'app-spreadsheet-preview',
  templateUrl: './spreadsheet-preview.html',
  styleUrl: './spreadsheet-preview.css',
})
export class SpreadsheetPreview {
  readonly documentId = input.required<number>();
  private readonly http = inject(HttpClient);
  readonly page = signal<WorkbookPage | null>(null);
  readonly selection = signal<{ sheet: string | null; row: number; column: number }>({
    sheet: null,
    row: 0,
    column: 0,
  });
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly current = computed(() =>
    this.page()?.sheets.find((sheet) => sheet.name === this.page()?.sheet),
  );
  readonly columns = computed(() =>
    Array.from({ length: this.page()?.rows[0]?.length ?? 0 }, (_, i) =>
      this.columnName((this.page()?.column_offset ?? 0) + i),
    ),
  );
  readonly hasFormulas = computed(
    () => this.page()?.formulas.some((row) => row.some(Boolean)) ?? false,
  );

  constructor() {
    effect((onCleanup) => {
      const id = this.documentId();
      const selection = this.selection();
      this.loading.set(true);
      this.error.set(null);
      let params = new HttpParams()
        .set('row_offset', selection.row)
        .set('column_offset', selection.column);
      if (selection.sheet !== null) params = params.set('sheet', selection.sheet);
      const request = this.http
        .get<WorkbookPage>(`${environment.apiUrl}/chat/documents/${id}/preview`, { params })
        .subscribe({
          next: (page) => {
            this.page.set(page);
            this.loading.set(false);
          },
          error: () => {
            this.loading.set(false);
            this.error.set(
              'This workbook preview could not be loaded. Retry or download the file.',
            );
          },
        });
      onCleanup(() => request.unsubscribe());
    });
  }
  columnName(index: number): string {
    let result = '';
    let value = index + 1;
    while (value > 0) {
      value--;
      result = String.fromCharCode(65 + (value % 26)) + result;
      value = Math.floor(value / 26);
    }
    return result;
  }
  selectSheet(sheet: string): void {
    this.selection.set({ sheet, row: 0, column: 0 });
  }
  moveSheet(event: Event, index: number, direction: number): void {
    const sheets = this.page()?.sheets ?? [];
    if (!sheets.length) return;
    event.preventDefault();
    const next = (index + direction + sheets.length) % sheets.length;
    const target = event.currentTarget;
    if (target instanceof HTMLElement) {
      target.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
    }
    this.selectSheet(sheets[next].name);
  }
  moveRows(direction: number): void {
    this.selection.update((s) => ({ ...s, row: Math.max(0, s.row + direction * 100) }));
  }
  moveColumns(direction: number): void {
    this.selection.update((s) => ({ ...s, column: Math.max(0, s.column + direction * 50) }));
  }
  retry(): void {
    this.selection.update((s) => ({ ...s }));
  }
}
