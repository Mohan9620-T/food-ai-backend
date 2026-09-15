import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import axe from 'axe-core';
import { DocumentResult } from './document-result';
import { ChatDocumentAttachment } from '../../models/chat';

describe('DocumentResult', () => {
  const attachment: ChatDocumentAttachment = {
    id: 80,
    filename: 'Dishes.xlsx',
    file_size: 5432,
    kind: 'generated',
    content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    provenance: 'general_knowledge',
    assumptions: ['Prices are approximate INR amounts.'],
  };
  beforeEach(() =>
    TestBed.configureTestingModule({
      imports: [DocumentResult],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }),
  );
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('uses real metadata, emits download, and only discloses known general-knowledge provenance', async () => {
    const fixture = TestBed.createComponent(DocumentResult);
    fixture.componentRef.setInput('attachment', attachment);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    expect(root.textContent).toContain('EXCEL');
    expect(root.textContent).toContain('5432 bytes');
    expect(root.textContent).toContain(
      'Built from general knowledge — check key facts before relying on it.',
    );
    expect(root.textContent).toContain('Assumed: Prices are approximate INR amounts.');
    const download = vi.fn();
    fixture.componentInstance.download.subscribe(download);
    root.querySelector<HTMLButtonElement>('button[aria-label^="Download"]')?.click();
    expect(download).toHaveBeenCalledOnce();
    const results = await axe.run(root, { rules: { 'color-contrast': { enabled: false } } });
    expect(results.violations).toEqual([]);
    for (const provenance of ['uploaded_source', null] as const) {
      fixture.componentRef.setInput('attachment', { ...attachment, provenance });
      fixture.detectChanges();
      expect(root.textContent).not.toContain('Built from general knowledge');
    }
    fixture.destroy();
  });

  it('renders actual preview response cells and switches worksheets through the file endpoint', () => {
    const fixture = TestBed.createComponent(DocumentResult);
    fixture.componentRef.setInput('attachment', attachment);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    root.querySelector<HTMLButtonElement>('button[aria-label^="View"]')?.click();
    fixture.detectChanges();
    const http = TestBed.inject(HttpTestingController);
    const sheets = [
      { name: 'Dishes', rows: 2, columns: 3 },
      { name: 'Notes', rows: 1, columns: 1 },
    ];
    http
      .expectOne((r) => r.url.endsWith('/80/preview'))
      .flush({
        sha256: 'actual-file-hash',
        sheets,
        sheet: 'Dishes',
        row_offset: 0,
        column_offset: 0,
        rows: [
          ['Dish', 'Price', 'Note'],
          ['Idli', 0, null],
        ],
        formulas: [
          [null, null, null],
          [null, null, null],
        ],
      });
    fixture.detectChanges();
    expect(Array.from(root.querySelectorAll('tbody td')).map((cell) => cell.textContent)).toEqual([
      'Dish',
      'Price',
      'Note',
      'Idli',
      '0',
      '',
    ]);
    const tabs = Array.from(root.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    tabs[1].click();
    fixture.detectChanges();
    http
      .expectOne((r) => r.params.get('sheet') === 'Notes' && r.params.get('row_offset') === '0')
      .flush({
        sha256: 'actual-file-hash',
        sheets,
        sheet: 'Notes',
        row_offset: 0,
        column_offset: 0,
        rows: [['Approximate prices']],
        formulas: [[null]],
      });
    fixture.detectChanges();
    expect(root.querySelector('tbody td')?.textContent).toBe('Approximate prices');
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    fixture.destroy();
  });

  it('shows a recoverable preview error instead of invented cells', () => {
    const fixture = TestBed.createComponent(DocumentResult);
    fixture.componentRef.setInput('attachment', attachment);
    fixture.detectChanges();
    fixture.componentInstance.view();
    fixture.detectChanges();
    TestBed.inject(HttpTestingController)
      .expectOne((r) => r.url.endsWith('/80/preview'))
      .flush({}, { status: 500, statusText: 'Error' });
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('could not be loaded');
    expect(root.querySelector('table')).toBeNull();
    fixture.destroy();
  });
});
