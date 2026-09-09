import { afterNextRender, ChangeDetectorRef, Component, computed, DestroyRef, effect, ElementRef, inject, signal, viewChild } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { ChatService, ChatStreamError } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ChatDocumentAttachment, ChatRequest } from '../../models/chat';
import { defer, EMPTY, finalize, Subscription, switchMap, tap } from 'rxjs';
import { SpeechRecognitionService } from '../../services/speech-recognition';

interface ComposerDraft {
  message: string;
  selectedImage: File | null;
  selectedDocument: File | null;
  imagePreviewUrl: string | null;
  imageError: string | null;
  showDocumentCreator: boolean;
  savedDocument: ChatDocumentAttachment | null;
}

const EMPTY_DRAFT: ComposerDraft = {
  message: '', selectedImage: null, selectedDocument: null,
  imagePreviewUrl: null, imageError: null, showDocumentCreator: false, savedDocument: null
};

@Component({
  selector: 'app-chat-input',
  imports: [FormsModule, ReactiveFormsModule],
  templateUrl: './chat-input.html',
  styleUrls: ['./chat-input.css'],
  host: {
    '(document:dragenter)': 'handleDragEnter($event)',
    '(document:dragover)': 'handleDragOver($event)',
    '(document:dragleave)': 'handleDragLeave($event)',
    '(document:drop)': 'handleDrop($event)'
  }
})
export class ChatInput {
  private readonly messageInput = viewChild<ElementRef<HTMLTextAreaElement>>('messageInput');
  private readonly documentInstructionInput = viewChild<ElementRef<HTMLTextAreaElement>>('documentInstructionInput');
  private readonly documentModeInput = viewChild<ElementRef<HTMLSelectElement>>('documentModeInput');
  private readonly createDocumentButton = viewChild<ElementRef<HTMLButtonElement>>('createDocumentButton');
  private readonly destroyRef = inject(DestroyRef);
  private readonly changeDetectorRef = inject(ChangeDetectorRef);
  private readonly chatService = inject(ChatService);
  readonly isSending = computed(() => this.chatService.isResponding() || this.processingDocument());
  readonly editingMessage = this.chatService.editingMessage;
  readonly analyzingImage = this.chatService.analyzingImage;
  readonly processingDocument = this.chatService.processingDocument;
  private readonly speechService = inject(SpeechRecognitionService);
  readonly speechSupported = this.speechService.isSupported;
  readonly isListening = this.speechService.isListening;
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  private readonly drafts = signal<ReadonlyMap<string, ComposerDraft>>(new Map());
  private readonly draft = computed(() => this.drafts().get(this.chatService.getActiveConversationId() ?? '') ?? EMPTY_DRAFT);
  get message(): string { return this.draft().message; }
  set message(value: string) { this.updateDraft({ message: value }); }
  get selectedImage(): File | null { return this.draft().selectedImage; }
  set selectedImage(value: File | null) { this.updateDraft({ selectedImage: value }); }
  get selectedDocument(): File | null { return this.draft().selectedDocument; }
  set selectedDocument(value: File | null) { this.updateDraft({ selectedDocument: value }); }
  get imagePreviewUrl(): string | null { return this.draft().imagePreviewUrl; }
  set imagePreviewUrl(value: string | null) { this.updateDraft({ imagePreviewUrl: value }); }
  get imageError(): string | null { return this.draft().imageError; }
  set imageError(value: string | null) { this.updateDraft({ imageError: value }); }
  get showDocumentCreator(): boolean { return this.draft().showDocumentCreator; }
  get exportSourceDocument(): ChatDocumentAttachment | null {
    return this.draft().savedDocument ?? this.chatService.messages()
      .filter(message => message.attachment?.kind === 'uploaded').at(-1)?.attachment ?? null;
  }
  private readonly documentForms = new Map<string, ReturnType<ChatInput['newDocumentForm']>>();
  get documentForm(): ReturnType<ChatInput['newDocumentForm']> {
    const conversationId = this.chatService.getActiveConversationId() ?? '';
    let form = this.documentForms.get(conversationId);
    if (!form) {
      form = this.newDocumentForm();
      this.documentForms.set(conversationId, form);
    }
    return form;
  }
  isDraggingImage = false;
  private dragDepth = 0;
  private visionSubscription: Subscription | null = null;
  private readonly documentSubscriptions = new Map<string, Subscription>();
  private readonly pendingUploads = new Map<string, { file: File; message: string }>();
  private readonly pendingGenerations = new Map<string, string>();
  private dictationPrefix = '';
  private finalDictation = '';

  constructor() {
    afterNextRender(() => this.messageInput()?.nativeElement.focus());
    effect(() => {
      const editingMessage = this.editingMessage();
      if (!editingMessage) return;

      this.message = editingMessage.text;
      queueMicrotask(() => {
        const textarea = this.messageInput()?.nativeElement;
        if (!textarea) return;
        this.resizeTextarea(textarea);
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      });
    });
    effect(() => {
      if (!this.chatService.retryMessage()) return;

      queueMicrotask(() => this.retryPendingMessage());
    });
    this.destroyRef.onDestroy(() => {
      this.visionSubscription?.unsubscribe();
      this.documentSubscriptions.forEach(subscription => subscription.unsubscribe());
      this.speechService.stop();
      this.drafts().forEach(draft => {
        if (draft.imagePreviewUrl) URL.revokeObjectURL(draft.imagePreviewUrl);
      });
    });
  }

  selectImage(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    input.value = '';
    if (!file || this.isSending()) return;
    this.attachFile(file);
  }

  private attachFile(file: File): void {
    const imageTypes: Record<string, string> = {
      jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif'
    };
    const extension = file.name.split('.').at(-1)?.toLowerCase() ?? '';
    const inferredType = (!file.type || file.type === 'application/octet-stream') ? imageTypes[extension] : undefined;
    const attachment = inferredType ? new File([file], file.name, { type: inferredType, lastModified: file.lastModified }) : file;
    if (attachment.type.startsWith('image/')) this.attachImage(attachment);
    else this.attachDocument(attachment);
  }

  private attachDocument(file: File): void {
    const supported = ['.pdf', '.docx', '.txt', '.csv', '.xlsx'].some(extension => file.name.toLowerCase().endsWith(extension));
    if (!supported) { this.imageError = 'Upload an image, PDF, DOCX, TXT, CSV, or XLSX file.'; return; }
    if (file.size > 15 * 1024 * 1024) { this.imageError = 'Document is too large. Maximum size is 15 MB.'; return; }
    this.removeImage(); this.selectedDocument = file; this.imageError = null;
  }

  removeDocument(): void { this.selectedDocument = null; }

  get speechError(): string | null {
    const error = this.speechService.error();
    if (!error) return null;
    if (error === 'not-allowed' || error === 'service-not-allowed') {
      return 'Microphone access was denied.';
    }
    if (error === 'no-speech') return 'No speech detected. Try again.';
    if (error === 'not-supported') return 'Voice input is not supported in this browser.';
    return 'Voice input failed. Please try again.';
  }

  toggleDictation(): void {
    if (this.isListening()) {
      this.speechService.stop();
      return;
    }
    if (this.isSending()) return;

    const existingMessage = this.message.trim();
    this.dictationPrefix = existingMessage ? `${existingMessage} ` : '';
    this.finalDictation = '';
    this.speechService.clearError();
    this.speechService.start(
      interim => this.updateDictatedMessage(this.finalDictation + interim),
      finalText => {
        this.finalDictation += finalText;
        this.updateDictatedMessage(this.finalDictation);
      }
    );
  }

  handleDragEnter(event: DragEvent): void {
    if (!this.hasDraggedFiles(event)) return;
    event.preventDefault();
    if (this.isSending()) return;
    this.dragDepth += 1;
    this.isDraggingImage = true;
  }

  handleDragOver(event: DragEvent): void {
    if (!this.hasDraggedFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = this.isSending() ? 'none' : 'copy';
  }

  handleDragLeave(event: DragEvent): void {
    if (!this.hasDraggedFiles(event)) return;
    this.dragDepth = Math.max(0, this.dragDepth - 1);
    if (this.dragDepth === 0) this.isDraggingImage = false;
  }

  handleDrop(event: DragEvent): void {
    if (!this.hasDraggedFiles(event)) return;
    event.preventDefault();
    this.dragDepth = 0;
    this.isDraggingImage = false;
    if (this.isSending()) return;

    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length === 0) return;
    if (files.length > 1) {
      this.imageError = 'Drop one image or document at a time.';
      return;
    }
    this.attachFile(files[0]);
  }

  private hasDraggedFiles(event: DragEvent): boolean {
    return Array.from(event.dataTransfer?.types ?? []).includes('Files');
  }

  private attachImage(file: File): void {
    this.imageError = null;
    if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) {
      this.imageError = 'Unsupported file type. Upload a JPEG, PNG, WebP, or GIF image.';
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      this.imageError = 'Image is too large. Maximum size is 8 MB.';
      return;
    }
    this.removeImage();
    this.removeDocument();
    this.selectedImage = file;
    this.imagePreviewUrl = URL.createObjectURL(file);
  }

  removeImage(): void {
    if (this.imagePreviewUrl) URL.revokeObjectURL(this.imagePreviewUrl);
    this.selectedImage = null;
    this.imagePreviewUrl = null;
  }

  resizeInput(event: Event): void {
    this.resizeTextarea(event.target as HTMLTextAreaElement);
  }

  handleKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.repeat) return;

    event.preventDefault();
    this.sendMessage();
  }

  sendMessage(): void {
    if (this.isSending()) return;

    const userMessage = this.message.trim();

    if (!userMessage && !this.selectedImage && !this.selectedDocument) {
      return;
    }

    // Freeze the current transcript before clearing the composer. SpeechRecognition may
    // otherwise deliver a queued result after Send and put the sent text back in the input.
    if (this.isListening()) this.speechService.stop();

    const isEditing = this.editingMessage() !== null;
    const conversationId = isEditing
      ? this.chatService.replaceEditingMessage(userMessage)
      : this.chatService.getActiveConversationId();
    if (!conversationId) return;

    const image = this.selectedImage;
    const documentFile = this.selectedDocument;
    if (documentFile) {
      this.imageError = null;
      this.addUploadMessage(conversationId, documentFile, userMessage);
      this.startDocumentResponse(conversationId, userMessage);
      this.requestDocumentResponse(conversationId, documentFile, userMessage || null);
      return;
    }
    const previewUrl = this.imagePreviewUrl;
    if (!isEditing) {
      this.chatService.addMessage({ sender: 'user', text: userMessage || '[Image]', imageUrl: previewUrl ?? undefined }, conversationId);
    }
    this.selectedImage = null;
    this.selectedDocument = null;
    this.imagePreviewUrl = null;
    this.imageError = null;
    this.message = '';
    const textarea = this.messageInput()?.nativeElement;
    if (textarea) {
      textarea.style.height = '48px';
      textarea.scrollTop = 0;
    }
    const request: ChatRequest = {
      message: userMessage,
      history: this.chatService.getHistory(conversationId),
      referenceHistory: this.chatService.getReferenceHistory(userMessage, conversationId)
    };
    this.chatService.startResponse(conversationId, request);
    if (image) this.requestVisionResponse(conversationId, image, userMessage || null);
    else this.requestResponse(conversationId, request);
  }

  createDocument(): void {
    if (this.isSending()) return;
    this.updateDraft({ showDocumentCreator: true, imageError: null });
    if (!this.documentForm.controls.instruction.value.trim() && this.message.trim()) {
      this.documentForm.controls.instruction.setValue(this.message);
    }
    this.changeDetectorRef.detectChanges();
    if (this.documentForm.controls.mode.value === 'export-file') this.documentModeInput()?.nativeElement.focus();
    else this.documentInstructionInput()?.nativeElement.focus();
  }

  closeDocumentCreator(): void {
    if (this.isSending()) return;
    this.updateDraft({ showDocumentCreator: false, imageError: null });
    this.createDocumentButton()?.nativeElement.focus();
  }

  submitDocument(): void {
    if (this.isSending()) return;
    const form = this.documentForm;
    const mode = form.controls.mode.value;
    const instruction = mode === 'export' ? form.controls.instruction.value : form.controls.instruction.value.trim();
    if (mode !== 'export-file' && (!instruction.trim() || form.invalid)) {
      this.imageError = mode === 'export'
        ? 'Enter the text to export (up to 2,000 characters).'
        : 'Describe the document you want to create (up to 2,000 characters).';
      this.documentInstructionInput()?.nativeElement.focus();
      return;
    }
    const conversationId = this.chatService.getActiveConversationId();
    if (!conversationId) return;
    const sourceDocument = this.exportSourceDocument;
    if (mode === 'export-file' && (this.selectedImage || (!this.selectedDocument && !sourceDocument))) {
      this.imageError = 'Attach a PDF, DOCX, TXT, CSV, or XLSX file, or import one into this conversation before exporting it.';
      return;
    }
    if (mode === 'export' && (this.selectedDocument || this.selectedImage)) {
      this.imageError = 'Export uses only the text below. Remove the attached file, or send it first and paste the text you want to export.';
      return;
    }
    if (this.selectedImage) {
      this.imageError = 'Send the attached image first, then create a document from the conversation.';
      return;
    }
    if (this.isListening()) this.speechService.stop();
    this.imageError = null;
    const file = this.selectedDocument;
    const message = mode === 'export-file' ? '' : this.message.trim();
    const format = form.controls.format.value;
    const sessionId = this.chatService.getActiveSessionId();
    const generationMessage = mode === 'export-file'
      ? `Export extracted text from ${file?.name ?? sourceDocument?.filename}` : instruction;
    this.startDocumentResponse(conversationId, generationMessage);
    const generate = (sourceSessionId: number | null, sourceDocumentId?: number) => defer(() => {
      if (this.pendingGenerations.get(conversationId) !== generationMessage) {
        this.chatService.addMessage({ sender: 'user', text: generationMessage }, conversationId);
        this.pendingGenerations.set(conversationId, generationMessage);
      }
      return mode === 'export-file'
        ? this.chatService.generateDocument(sourceSessionId, '', format, conversationId, 'export', sourceDocumentId)
        : this.chatService.generateDocument(sourceSessionId, instruction, format, conversationId, mode);
    });
    const request = file
      ? defer(() => {
          this.addUploadMessage(conversationId, file, message);
          return mode === 'export-file'
            ? this.chatService.uploadDocument(file, null, conversationId, false)
            : this.chatService.uploadDocument(file, message || null, conversationId);
        }).pipe(
          tap(response => this.acceptDocumentUpload(conversationId, file, message, response.attachment)),
          switchMap(response => {
            if (mode === 'ai' && response.analysis_status === 'unavailable') {
              this.updateDraft({ imageError: 'File saved, but AI analysis is unavailable. Choose Export saved file without AI to create a document from its extracted text, or try AI again later.' }, conversationId);
              return EMPTY;
            }
            if (mode === 'export-file' && !response.attachment) {
              this.updateDraft({ imageError: 'The upload did not return a saved file reference. Please refresh the conversation before exporting it.' }, conversationId);
              return EMPTY;
            }
            return generate(response.session_id, response.attachment?.id);
          })
        )
      : generate(sessionId, sourceDocument?.id);
    const operation = new Subscription();
    this.documentSubscriptions.set(conversationId, operation);
    operation.add(request.pipe(finalize(() => this.finishDocumentResponse(conversationId))).subscribe({
      next: () => {
        this.pendingGenerations.delete(conversationId);
        this.updateDraft({ showDocumentCreator: false, imageError: null }, conversationId);
        form.reset({ instruction: mode === 'export-file' ? form.controls.instruction.value : '', format, mode });
        if (mode !== 'export-file' && this.drafts().get(conversationId)?.message.trim() === instruction.trim()) {
          this.updateDraft({ message: '' }, conversationId);
        }
      },
      error: (error: HttpErrorResponse) => {
        this.updateDraft({ imageError: this.documentError(error, 'The document could not be generated. Please try again.') }, conversationId);
      }
    }));
  }

  private newDocumentForm() {
    const form = new FormGroup({
      instruction: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(2000)] }),
      format: new FormControl<'pdf' | 'docx'>('pdf', { nonNullable: true }),
      mode: new FormControl<'ai' | 'export' | 'export-file'>('ai', { nonNullable: true })
    });
    form.controls.mode.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(mode => {
      // A generation error describes the previous mode. Keeping it visible after
      // the user chooses a recovery mode makes a working no-AI export look broken.
      this.imageError = null;
      if (mode === 'export-file') form.controls.instruction.disable({ emitEvent: false });
      else form.controls.instruction.enable({ emitEvent: false });
    });
    return form;
  }

  stopResponse(): void {
    if (!this.isSending()) return;
    const conversationId = this.chatService.getActiveConversationId();
    if (conversationId && this.documentSubscriptions.has(conversationId)) {
      this.documentSubscriptions.get(conversationId)?.unsubscribe();
      this.imageError = 'Document processing was cancelled. You can try again.';
    } else if (this.analyzingImage()) {
      this.visionSubscription?.unsubscribe();
      this.visionSubscription = null;
      this.imageError = 'Image analysis was cancelled.';
    } else {
      this.chatService.stopStreaming();
    }
    const pending = this.chatService.getPendingResponse();
    if (pending && pending.conversationId === conversationId) this.chatService.finishResponse(pending.conversationId);
    queueMicrotask(() => this.messageInput()?.nativeElement.focus());
  }

  private requestVisionResponse(conversationId: string, image: File, message: string | null): void {
    this.visionSubscription = this.chatService.sendVisionMessage(image, message, conversationId).subscribe({
      error: (error: HttpErrorResponse) => {
        const detail = typeof error.error?.detail === 'string' ? error.error.detail : null;
        this.updateDraft({ imageError: error.status === 503 ? detail ?? 'Vision model unavailable. Please start Ollama and try again.' : detail ?? 'The image could not be analyzed. Please try again.' }, conversationId);
        this.finishVisionResponse(conversationId);
      },
      complete: () => this.finishVisionResponse(conversationId)
    });
  }

  private finishVisionResponse(conversationId: string): void {
    this.visionSubscription = null;
    this.chatService.finishResponse(conversationId);
    queueMicrotask(() => this.messageInput()?.nativeElement.focus());
  }

  private retryPendingMessage(): void {
    const retryMessage = this.chatService.consumeRetryMessage();
    if (!retryMessage || this.isSending()) return;

    const request: ChatRequest = {
      message: retryMessage.text,
      history: this.chatService.getHistory(retryMessage.conversationId),
      referenceHistory: this.chatService.getReferenceHistory(
        retryMessage.text,
        retryMessage.conversationId
      )
    };
    this.chatService.startResponse(retryMessage.conversationId, request);
    this.requestResponse(retryMessage.conversationId, request);
  }

  private async requestResponse(conversationId: string, request: ChatRequest): Promise<void> {
    try {
      await this.chatService.streamMessage(request, conversationId);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (!this.authService.getToken()) {
        this.router.navigate(['/login']);
        return;
      }
      if (this.chatService.getActiveConversationId() === conversationId && !this.message.trim()) {
        this.message = request.message;
      }
      if (!(error instanceof ChatStreamError && error.displayed)) {
        this.chatService.addMessage({
          sender: 'bot',
          text: error instanceof ChatStreamError
            ? `Response interrupted: ${error.message}`
            : 'The response could not be streamed. Please try again.'
        }, conversationId);
      }
    } finally {
      this.chatService.finishResponse(conversationId);
      queueMicrotask(() => this.messageInput()?.nativeElement.focus());
    }
  }

  private resizeTextarea(textarea: HTMLTextAreaElement): void {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
  }

  private requestDocumentResponse(conversationId: string, file: File, message: string | null): void {
    const operation = new Subscription();
    this.documentSubscriptions.set(conversationId, operation);
    operation.add(this.chatService.uploadDocument(file, message, conversationId).pipe(
      finalize(() => this.finishDocumentResponse(conversationId))
    ).subscribe({
      next: response => this.acceptDocumentUpload(conversationId, file, message ?? '', response.attachment),
      error: (error: HttpErrorResponse) => {
        this.updateDraft({ imageError: this.documentError(error, 'The document could not be processed. Your file is still attached; please try again.') }, conversationId);
      }
    }));
  }

  private addUploadMessage(conversationId: string, file: File, message: string): void {
    const pending = this.pendingUploads.get(conversationId);
    if (pending?.file === file && pending.message === message) return;
    this.chatService.addMessage({ sender: 'user', text: message || `[Document: ${file.name}]` }, conversationId);
    this.pendingUploads.set(conversationId, { file, message });
  }

  private startDocumentResponse(conversationId: string, message: string): void {
    this.chatService.startResponse(conversationId, {
      message, history: this.chatService.getHistory(conversationId),
      referenceHistory: this.chatService.getReferenceHistory(message, conversationId)
    });
  }

  private acceptDocumentUpload(conversationId: string, file: File, message: string, attachment?: ChatDocumentAttachment): void {
    this.pendingUploads.delete(conversationId);
    const draft = this.drafts().get(conversationId);
    this.updateDraft({
      ...(draft?.selectedDocument === file ? { selectedDocument: null } : {}),
      ...(draft?.message.trim() === message ? { message: '' } : {}),
      ...(attachment?.kind === 'uploaded' ? { savedDocument: attachment } : {}),
      imageError: null
    }, conversationId);
  }

  private finishDocumentResponse(conversationId: string): void {
    this.documentSubscriptions.delete(conversationId);
    this.chatService.finishResponse(conversationId);
    if (this.chatService.getActiveConversationId() === conversationId) {
      queueMicrotask(() => this.messageInput()?.nativeElement.focus());
    }
  }

  private documentError(error: HttpErrorResponse, fallback: string): string {
    return typeof error.error?.detail === 'string' ? error.error.detail : fallback;
  }

  private updateDraft(patch: Partial<ComposerDraft>, conversationId = this.chatService.getActiveConversationId() ?? ''): void {
    this.drafts.update(drafts => new Map(drafts).set(conversationId, { ...EMPTY_DRAFT, ...drafts.get(conversationId), ...patch }));
  }

  private updateDictatedMessage(dictatedText: string): void {
    this.message = this.dictationPrefix + dictatedText;
    // SpeechRecognition callbacks are not browser events known to Angular.
    // Render each interim transcript immediately while the user is speaking.
    const textarea = this.messageInput()?.nativeElement;
    if (textarea) {
      textarea.value = this.message;
      this.resizeTextarea(textarea);
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }
    this.changeDetectorRef.detectChanges();
  }
}
