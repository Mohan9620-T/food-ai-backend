import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SpeechRecognitionService } from './speech-recognition';

type MockRecognition = {
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: MockResultEvent) => void) | null;
  onerror: ((event: MockErrorEvent) => void) | null;
  onend: (() => void) | null;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
};

type MockResultEvent = Event & {
  readonly resultIndex: number;
  readonly results: ArrayLike<ArrayLike<{ transcript: string }> & { readonly isFinal: boolean }>;
};

type MockErrorEvent = Event & { readonly error: string };

const instances: MockRecognition[] = [];

class MockSpeechRecognition {
  continuous = false;
  interimResults = false;
  onresult: ((event: MockResultEvent) => void) | null = null;
  onerror: ((event: MockErrorEvent) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();

  constructor() {
    instances.push(this);
  }
}

function setRecognitionConstructor(value?: typeof MockSpeechRecognition): void {
  Object.defineProperty(window, 'SpeechRecognition', {
    configurable: true,
    value
  });
  Object.defineProperty(window, 'webkitSpeechRecognition', {
    configurable: true,
    value: undefined
  });
}

function resultEvent(
  results: MockResultEvent['results'],
  resultIndex = 0
): MockResultEvent {
  return Object.assign(new Event('result'), { resultIndex, results });
}

function errorEvent(error: string): MockErrorEvent {
  return Object.assign(new Event('error'), { error });
}

describe('SpeechRecognitionService', () => {
  afterEach(() => {
    TestBed.resetTestingModule();
    setRecognitionConstructor(undefined);
    instances.length = 0;
  });

  it('reports whether browser speech recognition is supported', () => {
    setRecognitionConstructor(undefined);
    expect(new SpeechRecognitionService().isSupported).toBe(false);

    setRecognitionConstructor(MockSpeechRecognition);
    expect(new SpeechRecognitionService().isSupported).toBe(true);
  });

  it('delivers interim and finalized transcripts', () => {
    setRecognitionConstructor(MockSpeechRecognition);
    const service = new SpeechRecognitionService();
    const onInterim = vi.fn();
    const onFinal = vi.fn();

    service.start(onInterim, onFinal);
    instances[0].onresult!(resultEvent([
        Object.assign([{ transcript: 'hello' }], { isFinal: false }),
        Object.assign([{ transcript: ' world ' }], { isFinal: true })
      ]));

    expect(instances[0].continuous).toBe(true);
    expect(instances[0].interimResults).toBe(true);
    expect(service.isListening()).toBe(true);
    expect(onInterim).toHaveBeenCalledWith('hello');
    expect(onFinal).toHaveBeenCalledWith('world ');
  });

  it('exposes recognition errors and resets listening state', () => {
    setRecognitionConstructor(MockSpeechRecognition);
    const service = new SpeechRecognitionService();
    service.start(vi.fn(), vi.fn());

    instances[0].onerror!(errorEvent('not-allowed'));

    expect(service.error()).toBe('not-allowed');
    expect(service.isListening()).toBe(false);
  });

  it('does not emit the same finalized result twice', () => {
    setRecognitionConstructor(MockSpeechRecognition);
    const service = new SpeechRecognitionService();
    const onFinal = vi.fn();
    service.start(vi.fn(), onFinal);
    const event = resultEvent([
      Object.assign([{ transcript: 'Are you?' }], { isFinal: true })
    ]);

    instances[0].onresult!(event);
    instances[0].onresult!(event);

    expect(onFinal).toHaveBeenCalledOnce();
    expect(onFinal).toHaveBeenCalledWith('Are you? ');
  });

  it('ignores queued callbacks after an intentional stop', () => {
    setRecognitionConstructor(MockSpeechRecognition);
    const service = new SpeechRecognitionService();
    const onInterim = vi.fn();
    service.start(onInterim, vi.fn());
    const recognition = instances[0];

    service.stop();

    expect(recognition.onresult).toBeNull();
    expect(recognition.onerror).toBeNull();
    expect(recognition.stop).toHaveBeenCalledOnce();
    expect(service.error()).toBeNull();
  });
});
