import { Injectable, signal, WritableSignal } from '@angular/core';

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

  private readonly RecognitionConstructor?: SpeechRecognitionConstructorLike;
  private recognition: SpeechRecognitionLike | null = null;

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
    this.error.set(null);
    const recognition = new this.RecognitionConstructor();
    this.recognition = recognition;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = event => {
      let interimTranscript = '';
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = result[0]?.transcript ?? '';
        if (result.isFinal) {
          const finalTranscript = transcript.trim();
          if (finalTranscript) onFinal(`${finalTranscript} `);
        } else {
          interimTranscript += transcript;
        }
      }
      onInterim(interimTranscript);
    };
    recognition.onerror = event => {
      this.error.set(event.error);
      this.reset(recognition);
    };
    recognition.onend = () => this.reset(recognition);

    try {
      recognition.start();
      this.isListening.set(true);
    } catch {
      this.error.set('start-failed');
      this.reset(recognition);
    }
  }

  stop(): void {
    const recognition = this.recognition;
    this.reset(recognition);
    recognition?.stop();
  }

  private reset(recognition?: SpeechRecognitionLike | null): void {
    if (recognition && this.recognition !== recognition) return;
    this.isListening.set(false);
    this.recognition = null;
  }
}
