import { PLATFORM_ID, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { switchMap } from 'rxjs';
import { vi } from 'vitest';
import { ChatService } from './chat';
import { AuthService } from './auth';

describe('ChatService document workflow', () => {
  let service: ChatService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [
      provideHttpClient(), provideHttpClientTesting(),
      { provide: PLATFORM_ID, useValue: 'server' },
      { provide: AuthService, useValue: {
        currentUserId: signal('42'), getToken: () => 'unit-test-token'
      } }
    ] });
    service = TestBed.inject(ChatService);
    http = TestBed.inject(HttpTestingController);
    TestBed.flushEffects();
    http.expectOne(request => request.url.endsWith('/chat/sessions')).flush([]);
  });

  afterEach(() => http.verify());

  it.each(['pdf', 'docx', 'txt', 'csv', 'xlsx'])('uploads %s bytes and saves the returned attachment', async extension => {
    const file = new File(['test bytes'], `report.${extension}`);
    const id = service.getActiveConversationId()!;
    service.addMessage({ sender: 'user', text: 'Read this file' }, id);
    service.uploadDocument(file, 'Read this file', id).subscribe();
    expect(service.processingDocument()).toBe(true);
    const request = http.expectOne(req => req.url.endsWith('/chat/documents'));
    const form: FormData = request.request.body;
    const uploaded = form.get('file') as File;
    expect(uploaded.name).toBe(file.name);
    expect(uploaded.size).toBe(file.size);
    const content = new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(uploaded);
    });
    expect(await content).toBe('test bytes');
    expect(form.get('message')).toBe('Read this file');
    expect(form.has('session_id')).toBe(false);
    const attachment = { id: 5, filename: file.name, file_size: file.size, content_type: 'application/octet-stream', kind: 'uploaded' };
    request.flush({ response: 'File summary', session_id: 71, attachment });
    expect(service.processingDocument()).toBe(false);
    expect(service.getActiveSessionId()).toBe(71);
    expect(service.messages()[0].attachment).toEqual(attachment);
    expect(service.messages()[1].text).toBe('File summary');
  });

  it.each(['pdf', 'docx'] as const)('generates %s in a new chat and uses its session subsequently', format => {
    const id = service.getActiveConversationId()!;
    service.generateDocument(null, 'Create a report', format, id).subscribe();
    const request = http.expectOne(req => req.url.endsWith('/chat/documents/generate'));
    expect(request.request.body).toEqual({ session_id: null, instruction: 'Create a report', output_format: format });
    const attachment = { id: 9, filename: `report.${format}`, file_size: 100, content_type: 'application/octet-stream', kind: 'generated' };
    request.flush({ response: 'Created report', session_id: 73, attachment });
    expect(service.messages().at(-1)?.attachment).toEqual(attachment);
    service.sendMessage({ message: 'Next', history: [], referenceHistory: [] }, id).subscribe();
    http.expectOne(req => req.urlWithParams.endsWith('/chat/?session_id=73'))
      .flush({ response: 'Next answer', session_id: 73 });
  });

  it('keeps processing active across upload then generation', () => {
    const id = service.getActiveConversationId()!;
    service.uploadDocument(new File(['source'], 'source.txt'), null, id).pipe(
      switchMap(response => service.generateDocument(response.session_id, 'Create a summary', 'pdf', id))
    ).subscribe();
    http.expectOne(req => req.url.endsWith('/chat/documents')).flush({ response: 'Summary', session_id: 81 });
    expect(service.processingDocument()).toBe(true);
    const generate = http.expectOne(req => req.url.endsWith('/chat/documents/generate'));
    expect(generate.request.body.session_id).toBe(81);
    generate.flush({ response: 'Document', session_id: 81 });
    expect(service.processingDocument()).toBe(false);
  });

  it.each(['pdf', 'docx'] as const)('explicitly requests %s text export without changing the text or uploading files', format => {
    const id = service.getActiveConversationId()!;
    const text = '  Header\n\nExact text.  ';
    service.generateDocument(null, text, format, id, 'export').subscribe();
    const request = http.expectOne(req => req.url.endsWith('/chat/documents/generate'));
    expect(request.request.body).toEqual({ session_id: null, instruction: text, output_format: format, mode: 'export' });
    http.expectNone(req => req.url.endsWith('/chat/documents'));
    expect(service.processingDocument()).toBe(true);
    request.flush({ response: 'Exported', session_id: 92 });
    expect(service.processingDocument()).toBe(false);
    expect(service.messages().at(-1)?.text).toBe('Exported');
  });

  it('clears export processing state on failure and lets the same draft be retried', () => {
    const id = service.getActiveConversationId()!;
    const failed = vi.fn();
    service.generateDocument(null, 'Literal text', 'pdf', id, 'export').subscribe({ error: failed });
    http.expectOne(req => req.url.endsWith('/chat/documents/generate'))
      .flush({ detail: 'Document renderer unavailable.' }, { status: 503, statusText: 'Service Unavailable' });
    expect(failed.mock.calls[0][0].error.detail).toBe('Document renderer unavailable.');
    expect(service.processingDocument()).toBe(false);
    expect(service.messages()).toHaveLength(0);
    service.generateDocument(null, 'Literal text', 'pdf', id, 'export').subscribe();
    http.expectOne(req => req.url.endsWith('/chat/documents/generate')).flush({ response: 'Exported', session_id: 93 });
    expect(service.messages()).toHaveLength(1);
    expect(service.processingDocument()).toBe(false);
  });

  it('requests an import without AI and exports the saved document source without instruction text', () => {
    const id = service.getActiveConversationId()!;
    const attachment = { id: 31, filename: 'notes.pdf', content_type: 'application/pdf', file_size: 20, kind: 'uploaded' as const };
    service.addMessage({ sender: 'user', text: '[Document: notes.pdf]' }, id);
    service.uploadDocument(new File(['source'], 'notes.pdf'), null, id, false).pipe(
      switchMap(response => service.generateDocument(response.session_id, '', 'docx', id, 'export', response.attachment?.id))
    ).subscribe();
    const upload = http.expectOne(req => req.url.endsWith('/chat/documents'));
    expect((upload.request.body as FormData).get('analyze')).toBe('false');
    upload.flush({ response: 'File saved.', session_id: 94, attachment, analysis_status: 'skipped' });
    const generation = http.expectOne(req => req.url.endsWith('/chat/documents/generate'));
    expect(generation.request.body).toEqual({ session_id: 94, output_format: 'docx', mode: 'export', source_document_id: 31 });
    generation.flush({ response: 'Exported.', session_id: 94 });
    expect(service.processingDocument()).toBe(false);
  });

  it('keeps the uploaded attachment and its saved session when AI analysis is unavailable', () => {
    const id = service.getActiveConversationId()!;
    const received = vi.fn();
    const attachment = { id: 32, filename: 'notes.pdf', content_type: 'application/pdf', file_size: 20, kind: 'uploaded' as const };
    service.addMessage({ sender: 'user', text: 'Read this file' }, id);
    service.uploadDocument(new File(['source'], 'notes.pdf'), 'Read this file', id).subscribe(received);
    const request = http.expectOne(req => req.url.endsWith('/chat/documents'));
    expect((request.request.body as FormData).has('analyze')).toBe(false);
    request.flush({ response: 'Saved file; extracted text only, AI analysis unavailable.', session_id: 95, attachment, analysis_status: 'unavailable' });
    expect(received.mock.calls[0][0].analysis_status).toBe('unavailable');
    expect(service.getActiveSessionId()).toBe(95);
    expect(service.messages()[0].attachment).toEqual(attachment);
    expect(service.messages()[1].text).toContain('extracted text only');
    expect(service.processingDocument()).toBe(false);
  });

  it('does not mark an unstarted request busy and clears state on cancellation', () => {
    const result = service.uploadDocument(new File(['source'], 'source.txt'), null);
    expect(service.processingDocument()).toBe(false);
    const subscription = result.subscribe();
    const request = http.expectOne(req => req.url.endsWith('/chat/documents'));
    subscription.unsubscribe();
    expect(request.cancelled).toBe(true);
    expect(service.processingDocument()).toBe(false);
  });

  it('preserves server error details and clears processing without binding a failed session', () => {
    const failed = vi.fn();
    service.uploadDocument(new File(['scan'], 'scan.pdf'), null).subscribe({ error: failed });
    http.expectOne(req => req.url.endsWith('/chat/documents'))
      .flush({ detail: 'Scanned PDFs require Tesseract OCR.' }, { status: 503, statusText: 'Service Unavailable' });
    expect(failed.mock.calls[0][0].error.detail).toContain('Tesseract');
    expect(service.processingDocument()).toBe(false);
    expect(service.getActiveSessionId()).toBeNull();
  });

  it('attaches a background result only to the conversation that started it', () => {
    const first = service.getActiveConversationId()!;
    service.generateDocument(null, 'Create a report', 'pdf', first).subscribe();
    service.createConversation();
    expect(service.processingDocument()).toBe(false);
    http.expectOne(req => req.url.endsWith('/chat/documents/generate')).flush({ response: 'Created', session_id: 90 });
    expect(service.messages()).toHaveLength(0);
    service.selectConversation(first);
    expect(service.messages().at(-1)?.text).toBe('Created');
    expect(service.getActiveSessionId()).toBe(90);
  });
});
