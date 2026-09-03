import { inject, Injectable, NgZone, signal, WritableSignal } from '@angular/core';

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

interface SpeechRecognitionConstructorLike {
  new (): SpeechRecognitionLike;
}

type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructorLike;
  webkitSpeechRecognition?: SpeechRecognitionConstructorLike;
};

@Injectable({ providedIn: 'root' })
export class SpeechRecognitionService {
  readonly isSupported: boolean;
  readonly isListening: WritableSignal<boolean> = signal(false);
  readonly error: WritableSignal<string | null> = signal(null);

  private readonly ngZone = SpeechRecognitionService.resolveNgZone();
  private readonly RecognitionConstructor?: SpeechRecognitionConstructorLike;
  private recognition: SpeechRecognitionLike | null = null;
  private errorTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    const speechWindow = window as SpeechRecognitionWindow;
    this.RecognitionConstructor =
      speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    this.isSupported = Boolean(this.RecognitionConstructor);
  }

  start(
    onInterim: (text: string) => void,
    onFinal: (text: string) => void
  ): void {
    if (!this.RecognitionConstructor) {
      this.error.set('not-supported');
      return;
    }

    if (this.recognition) this.stop();
    this.clearError();
    const recognition = new this.RecognitionConstructor();
    this.recognition = recognition;
    recognition.continuous = true;
    recognition.interimResults = true;
    const finalizedResultIndexes = new Set<number>();
    // Zone.js does not patch SpeechRecognition, so re-enter Angular's zone to schedule rendering.
    recognition.onresult = event => {
      this.ngZone.run(() => {
        let interimTranscript = '';
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          const transcript = result[0]?.transcript ?? '';
          if (result.isFinal) {
            const finalTranscript = transcript.trim();
            if (finalTranscript && !finalizedResultIndexes.has(index)) {
              finalizedResultIndexes.add(index);
              onFinal(`${finalTranscript} `);
            }
          } else {
            interimTranscript += transcript;
          }
        }
        onInterim(interimTranscript);
      });
    };
    recognition.onerror = event => {
      this.ngZone.run(() => {
        // Chrome emits "aborted" when stop() is intentional; it is not a user-facing failure.
        if (event.error !== 'aborted') this.setTemporaryError(event.error);
        this.reset(recognition);
      });
    };
    recognition.onend = () => {
      this.ngZone.run(() => this.reset(recognition));
    };

    try {
      recognition.start();
      this.isListening.set(true);
    } catch {
      this.setTemporaryError('start-failed');
      this.reset(recognition);
    }
  }

  stop(): void {
    const recognition = this.recognition;
    this.reset(recognition);
    if (recognition) {
      // Ignore events queued by the browser after an intentional stop. Otherwise a late
      // transcript can repopulate a composer that was just cleared by Send.
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
    }
    recognition?.stop();
  }

  clearError(): void {
    if (this.errorTimer) clearTimeout(this.errorTimer);
    this.errorTimer = null;
    this.error.set(null);
  }

  private reset(recognition?: SpeechRecognitionLike | null): void {
    if (recognition && this.recognition !== recognition) return;
    this.isListening.set(false);
    this.recognition = null;
  }

  private setTemporaryError(error: string): void {
    this.clearError();
    this.error.set(error);
    this.errorTimer = setTimeout(() => this.ngZone.run(() => this.clearError()), 4000);
  }

  private static resolveNgZone(): NgZone {
    try {
      return inject(NgZone);
    } catch {
      // Preserve direct construction used by the service's isolated unit tests.
      return {
        run: <T>(callback: () => T): T => callback()
      } as NgZone;
    }
  }
}
