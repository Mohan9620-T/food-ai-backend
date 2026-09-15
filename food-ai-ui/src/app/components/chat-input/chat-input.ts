import {
  afterNextRender,
  ChangeDetectorRef,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ChatService, ChatStreamError } from '../../services/chat';
import { AuthService } from '../../services/auth';
import { ChatRequest } from '../../models/chat';
import {
  isDocumentCapabilityQuestion,
  isDocumentFileRequest,
} from '../../models/document-requests';
import { finalize, Subscription } from 'rxjs';
import { SpeechRecognitionService } from '../../services/speech-recognition';

interface ComposerDraft {
  message: string;
  selectedImage: File | null;
  selectedDocument: File | null;
  imagePreviewUrl: string | null;
  imageError: string | null;
}

const EMPTY_DRAFT: ComposerDraft = {
  message: '',
  selectedImage: null,
  selectedDocument: null,
  imagePreviewUrl: null,
  imageError: null,
};

@Component({
  selector: 'app-chat-input',
  imports: [FormsModule],
  templateUrl: './chat-input.html',
  styleUrls: ['./chat-input.css'],
  host: {
    '(document:dragenter)': 'handleDragEnter($event)',
    '(document:dragover)': 'handleDragOver($event)',
    '(document:dragleave)': 'handleDragLeave($event)',
    '(document:drop)': 'handleDrop($event)',
  },
})
export class ChatInput {
  private readonly messageInput = viewChild<ElementRef<HTMLTextAreaElement>>('messageInput');
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
  private readonly draft = computed(
    () => this.drafts().get(this.chatService.getActiveConversationId() ?? '') ?? EMPTY_DRAFT,
  );
  get message(): string {
    return this.draft().message;
  }
  set message(value: string) {
    this.updateDraft({ message: value });
  }
  get selectedImage(): File | null {
    return this.draft().selectedImage;
  }
  set selectedImage(value: File | null) {
    this.updateDraft({ selectedImage: value });
  }
  get selectedDocument(): File | null {
    return this.draft().selectedDocument;
  }
  set selectedDocument(value: File | null) {
    this.updateDraft({ selectedDocument: value });
  }
  get imagePreviewUrl(): string | null {
    return this.draft().imagePreviewUrl;
  }
  set imagePreviewUrl(value: string | null) {
    this.updateDraft({ imagePreviewUrl: value });
  }
  get imageError(): string | null {
    return this.draft().imageError;
  }
  set imageError(value: string | null) {
    this.updateDraft({ imageError: value });
  }
  isDraggingImage = false;
  private dragDepth = 0;
  private visionSubscription: Subscription | null = null;
  private readonly documentSubscriptions = new Map<string, Subscription>();
  private readonly pendingUploads = new Map<string, { file: File; message: string }>();
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
      this.documentSubscriptions.forEach((subscription) => subscription.unsubscribe());
      this.speechService.stop();
      this.drafts().forEach((draft) => {
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
      jpg: 'image/jpeg',
      jpeg: 'image/jpeg',
      png: 'image/png',
      webp: 'image/webp',
      gif: 'image/gif',
    };
    const extension = file.name.split('.').at(-1)?.toLowerCase() ?? '';
    const inferredType =
      !file.type || file.type === 'application/octet-stream' ? imageTypes[extension] : undefined;
    const attachment = inferredType
      ? new File([file], file.name, { type: inferredType, lastModified: file.lastModified })
      : file;
    if (attachment.type.startsWith('image/')) this.attachImage(attachment);
    else this.attachDocument(attachment);
  }

  private attachDocument(file: File): void {
    const supported = ['.pdf', '.docx', '.txt', '.csv', '.xlsx', '.pptx', '.md', '.markdown'].some(
      (extension) => file.name.toLowerCase().endsWith(extension),
    );
    if (!supported) {
      this.imageError =
        'Upload an image, PDF, Word, Excel, CSV, PowerPoint, TXT, or Markdown file.';
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      this.imageError = 'Document is too large. Maximum size is 15 MB.';
      return;
    }
    this.removeImage();
    this.selectedDocument = file;
    this.imageError = null;
  }

  removeDocument(): void {
    this.selectedDocument = null;
  }

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
      (interim) => this.updateDictatedMessage(this.finalDictation + interim),
      (finalText) => {
        this.finalDictation += finalText;
        this.updateDictatedMessage(this.finalDictation);
      },
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
    const spreadsheetOperation =
      !image && this.chatService.isSpreadsheetOperationRequest(userMessage);
    const spreadsheetSessionId = spreadsheetOperation
      ? this.chatService.getActiveSessionId()
      : null;
    if (spreadsheetOperation && spreadsheetSessionId === null) {
      this.imageError = this.chatService.isSpreadsheetRowCountRequest(userMessage)
        ? 'Upload an Excel or CSV spreadsheet first, then ask your row-count question.'
        : 'Upload an Excel spreadsheet first, then ask me to add the filter.';
      return;
    }
    const documentAutomation =
      !image &&
      !spreadsheetOperation &&
      !isDocumentCapabilityQuestion(userMessage) &&
      (this.chatService.hasPendingAutomation(conversationId) ||
        isDocumentFileRequest(userMessage) ||
        (this.chatService.hasDocumentContext(conversationId) &&
          this.chatService.isDocumentAutomationRequest(userMessage)));
    const automationSessionId = documentAutomation ? this.chatService.getActiveSessionId() : null;
    const previewUrl = this.imagePreviewUrl;
    if (!isEditing) {
      this.chatService.addMessage(
        { sender: 'user', text: userMessage || '[Image]', imageUrl: previewUrl ?? undefined },
        conversationId,
      );
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
      referenceHistory: this.chatService.getReferenceHistory(userMessage, conversationId),
    };
    this.chatService.startResponse(conversationId, request);
    if (image) this.requestVisionResponse(conversationId, image, userMessage || null);
    else if (spreadsheetOperation && spreadsheetSessionId !== null) {
      this.requestSpreadsheetResponse(conversationId, spreadsheetSessionId, userMessage);
    } else if (documentAutomation) {
      this.requestDocumentAutomationResponse(conversationId, automationSessionId, userMessage);
    } else this.requestResponse(conversationId, request);
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
    if (pending && pending.conversationId === conversationId)
      this.chatService.finishResponse(pending.conversationId);
    queueMicrotask(() => this.messageInput()?.nativeElement.focus());
  }

  private requestVisionResponse(conversationId: string, image: File, message: string | null): void {
    this.visionSubscription = this.chatService
      .sendVisionMessage(image, message, conversationId)
      .subscribe({
        error: (error: HttpErrorResponse) => {
          const detail = typeof error.error?.detail === 'string' ? error.error.detail : null;
          this.updateDraft(
            {
              imageError:
                error.status === 503
                  ? (detail ?? 'Vision model unavailable. Please start Ollama and try again.')
                  : (detail ?? 'The image could not be analyzed. Please try again.'),
            },
            conversationId,
          );
          this.finishVisionResponse(conversationId);
        },
        complete: () => this.finishVisionResponse(conversationId),
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
        retryMessage.conversationId,
      ),
    };
    this.chatService.startResponse(retryMessage.conversationId, request);
    const sessionId = this.chatService.getActiveSessionId();
    if (this.chatService.isSpreadsheetOperationRequest(retryMessage.text) && sessionId !== null) {
      this.requestSpreadsheetResponse(retryMessage.conversationId, sessionId, retryMessage.text);
    } else if (
      !isDocumentCapabilityQuestion(retryMessage.text) &&
      (this.chatService.hasPendingAutomation(retryMessage.conversationId) ||
        isDocumentFileRequest(retryMessage.text) ||
        (this.chatService.hasDocumentContext(retryMessage.conversationId) &&
          this.chatService.isDocumentAutomationRequest(retryMessage.text)))
    ) {
      this.requestDocumentAutomationResponse(
        retryMessage.conversationId,
        sessionId,
        retryMessage.text,
      );
    } else {
      this.requestResponse(retryMessage.conversationId, request);
    }
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
        this.chatService.addMessage(
          {
            sender: 'bot',
            text:
              error instanceof ChatStreamError
                ? `Response interrupted: ${error.message}`
                : 'The response could not be streamed. Please try again.',
          },
          conversationId,
        );
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

  private requestDocumentResponse(
    conversationId: string,
    file: File,
    message: string | null,
  ): void {
    let uploaded = false;
    const operation = new Subscription();
    this.documentSubscriptions.set(conversationId, operation);
    operation.add(
      this.chatService
        .uploadDocument(file, message, conversationId)
        .pipe(finalize(() => this.finishDocumentResponse(conversationId)))
        .subscribe({
          next: () => {
            uploaded = true;
            this.acceptDocumentUpload(conversationId, file, message ?? '');
          },
          error: (error: HttpErrorResponse) => {
            this.updateDraft(
              {
                ...(uploaded ? { message: message ?? '' } : {}),
                imageError: this.documentError(
                  error,
                  uploaded
                    ? 'Your file is saved, but the document request failed. Send your instruction again to retry.'
                    : 'The document could not be processed. Your file is still attached; please try again.',
                ),
              },
              conversationId,
            );
          },
        }),
    );
  }

  private requestSpreadsheetResponse(
    conversationId: string,
    sessionId: number,
    message: string,
  ): void {
    const operation = new Subscription();
    this.documentSubscriptions.set(conversationId, operation);
    operation.add(
      this.chatService
        .updateSpreadsheet(sessionId, message, conversationId)
        .pipe(finalize(() => this.finishDocumentResponse(conversationId)))
        .subscribe({
          next: () => this.updateDraft({ imageError: null }, conversationId),
          error: (error: HttpErrorResponse) => {
            this.updateDraft(
              {
                message,
                imageError: this.documentError(
                  error,
                  'The Excel workbook could not be updated. Please try again.',
                ),
              },
              conversationId,
            );
          },
        }),
    );
  }

  private requestDocumentAutomationResponse(
    conversationId: string,
    sessionId: number | null,
    message: string,
  ): void {
    const operation = new Subscription();
    this.documentSubscriptions.set(conversationId, operation);
    operation.add(
      this.chatService
        .automateDocument(sessionId, message, conversationId)
        .pipe(finalize(() => this.finishDocumentResponse(conversationId)))
        .subscribe({
          next: () => this.updateDraft({ imageError: null }, conversationId),
          error: (error: HttpErrorResponse) => {
            this.updateDraft(
              {
                message,
                imageError: this.documentError(
                  error,
                  'The document request could not be completed. Please try again.',
                ),
              },
              conversationId,
            );
          },
        }),
    );
  }

  private addUploadMessage(conversationId: string, file: File, message: string): void {
    const pending = this.pendingUploads.get(conversationId);
    if (pending?.file === file && pending.message === message) return;
    this.chatService.addMessage(
      { sender: 'user', text: message || `[Document: ${file.name}]` },
      conversationId,
    );
    this.pendingUploads.set(conversationId, { file, message });
  }

  private startDocumentResponse(conversationId: string, message: string): void {
    this.chatService.startResponse(conversationId, {
      message,
      history: this.chatService.getHistory(conversationId),
      referenceHistory: this.chatService.getReferenceHistory(message, conversationId),
    });
  }

  private acceptDocumentUpload(conversationId: string, file: File, message: string): void {
    this.pendingUploads.delete(conversationId);
    const draft = this.drafts().get(conversationId);
    this.updateDraft(
      {
        ...(draft?.selectedDocument === file ? { selectedDocument: null } : {}),
        ...(draft?.message.trim() === message ? { message: '' } : {}),
        imageError: null,
      },
      conversationId,
    );
  }

  private finishDocumentResponse(conversationId: string): void {
    this.documentSubscriptions.delete(conversationId);
    this.chatService.finishResponse(conversationId);
    if (this.chatService.getActiveConversationId() === conversationId) {
      queueMicrotask(() => this.messageInput()?.nativeElement.focus());
    }
  }

  private documentError(error: HttpErrorResponse, fallback: string): string {
    const detail = error.error?.detail;
    if (
      typeof detail === 'string' &&
      detail.trim() &&
      !/[{}]/.test(detail) &&
      !/"(?:error|message|detail)"\s*:/i.test(detail)
    ) {
      return detail;
    }
    return fallback;
  }

  private updateDraft(
    patch: Partial<ComposerDraft>,
    conversationId = this.chatService.getActiveConversationId() ?? '',
  ): void {
    this.drafts.update((drafts) =>
      new Map(drafts).set(conversationId, {
        ...EMPTY_DRAFT,
        ...drafts.get(conversationId),
        ...patch,
      }),
    );
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
