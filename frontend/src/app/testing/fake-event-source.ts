import { EVENT_SOURCE_FACTORY } from '../core/stream.service';

/** Minimal stand-in for EventSource (jsdom has none). Tests drive it with open()/emit()/fail(). */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readyState = 0;
  closed = false;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  static latest(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }

  emit(topic: string, data: unknown): void {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ topic, data }) }));
  }

  emitRaw(raw: string): void {
    this.onmessage?.(new MessageEvent('message', { data: raw }));
  }

  /** permanent = browser gave up (readyState CLOSED); otherwise it would retry by itself. */
  fail(permanent = false): void {
    this.readyState = permanent ? 2 : 0;
    this.onerror?.(new Event('error'));
  }

  close(): void {
    this.readyState = 2;
    this.closed = true;
  }
}

export const fakeEventSourceProvider = {
  provide: EVENT_SOURCE_FACTORY,
  useValue: (url: string) => new FakeEventSource(url) as unknown as EventSource,
};
