import {
  afterNextRender,
  Component,
  ElementRef,
  Injector,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import { ChatService } from '../../services/chat';
import { ChatResponse, DocumentAutomationTurn } from '../../models/chat';

@Component({
  selector: 'app-document-wizard',
  imports: [ReactiveFormsModule],
  templateUrl: './document-wizard.html',
  styleUrl: './document-wizard.css',
})
export class DocumentWizard {
  readonly response = input.required<ChatResponse>();
  readonly sessionId = input.required<number>();
  readonly conversationId = input.required<string>();
  readonly instruction = input.required<string>();
  readonly choices = input<DocumentAutomationTurn['choices']>([]);
  private readonly chat = inject(ChatService);
  private readonly injector = inject(Injector);
  private readonly heading = viewChild<ElementRef<HTMLElement>>('heading');
  private readonly otherInput = viewChild<ElementRef<HTMLInputElement>>('otherInput');
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly selected = signal<string | null>(null);
  readonly freeform = signal(false);
  readonly other = new FormControl('', { nonNullable: true });
  readonly question = computed(
    () => this.response().clarification?.question ?? this.response().response,
  );
  readonly options = computed(() => this.response().clarification?.options ?? []);
  readonly review = computed(() => this.response().status === 'ready_for_review');

  constructor() {
    effect(() => {
      this.response();
      this.selected.set(null);
      this.freeform.set(this.options().length === 0);
      this.other.reset();
      this.error.set(null);
      afterNextRender(() => this.heading()?.nativeElement.focus({ preventScroll: true }), {
        injector: this.injector,
      });
    });
  }

  choose(id: string): void {
    this.selected.set(id);
    this.freeform.set(false);
  }

  chooseOther(): void {
    this.selected.set(null);
    this.freeform.set(true);
    afterNextRender(() => this.otherInput()?.nativeElement.focus(), { injector: this.injector });
  }

  next(): void {
    const answer = this.freeform()
      ? this.other.value.trim()
      : this.options().find((option) => option.id === this.selected())?.label;
    if (!answer) {
      this.error.set('Choose an option or enter your answer.');
      return;
    }
    this.submit(answer, false);
  }

  build(): void {
    if (this.review()) this.submit(this.instruction(), true);
  }

  private submit(instruction: string, confirm: boolean): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    const conversationId = this.conversationId();
    // The service owns in-flight work so switching chats cannot cancel a Build.
    this.chat
      .automateDocument(this.sessionId(), instruction, conversationId, confirm)
      .pipe(finalize(() => this.busy.set(false)))
      .subscribe({
        error: () => this.error.set('The document request could not be completed. Please retry.'),
      });
  }
}
