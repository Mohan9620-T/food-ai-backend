import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SpeechRecognitionService } from './speech-recognition';

type MockRecognition = {
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
};

let instance: MockRecognition | null = null;

class MockSpeechRecognition {
  continuous = false;
  interimResults = false;
  onresult: ((event: any) => void) | null = null;
  onerror: ((event: any) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();

  constructor() {
    instance = this;
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

describe('SpeechRecognitionService', () => {
  afterEach(() => {
    TestBed.resetTestingModule();
    setRecognitionConstructor(undefined);
    instance = null;
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
    instance!.onresult!({
      resultIndex: 0,
      results: [
        Object.assign([{ transcript: 'hello' }], { isFinal: false }),
        Object.assign([{ transcript: ' world ' }], { isFinal: true })
      ]
    });

    expect(instance!.continuous).toBe(true);
    expect(instance!.interimResults).toBe(true);
    expect(service.isListening()).toBe(true);
    expect(onInterim).toHaveBeenCalledWith('hello');
    expect(onFinal).toHaveBeenCalledWith('world ');
  });

  it('exposes recognition errors and resets listening state', () => {
    setRecognitionConstructor(MockSpeechRecognition);
    const service = new SpeechRecognitionService();
    service.start(vi.fn(), vi.fn());

    instance!.onerror!({ error: 'not-allowed' });

    expect(service.error()).toBe('not-allowed');
    expect(service.isListening()).toBe(false);
  });

  it('does not emit the same finalized result twice', () => {
    setRecognitionConstructor(MockSpeechRecognition);
    const service = new SpeechRecognitionService();
    const onFinal = vi.fn();
    service.start(vi.fn(), onFinal);
    const event = {
      resultIndex: 0,
      results: [Object.assign([{ transcript: 'Are you?' }], { isFinal: true })]
    };

    instance!.onresult!(event);
    instance!.onresult!(event);

    expect(onFinal).toHaveBeenCalledOnce();
    expect(onFinal).toHaveBeenCalledWith('Are you? ');
  });

  it('ignores queued callbacks after an intentional stop', () => {
    setRecognitionConstructor(MockSpeechRecognition);
    const service = new SpeechRecognitionService();
    const onInterim = vi.fn();
    service.start(onInterim, vi.fn());
    const recognition = instance!;

    service.stop();

    expect(recognition.onresult).toBeNull();
    expect(recognition.onerror).toBeNull();
    expect(recognition.stop).toHaveBeenCalledOnce();
    expect(service.error()).toBeNull();
  });
});
