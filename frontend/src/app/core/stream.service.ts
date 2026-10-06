import { Injectable, InjectionToken, inject, signal } from '@angular/core';

export type StreamState = 'idle' | 'connecting' | 'open' | 'reconnecting';
type TopicHandler = (data: unknown, topic: string) => void;

export const STREAM_URL = '/api/v1/stream';
const READY_STATE_CLOSED = 2; // EventSource.CLOSED; literal because jsdom has no EventSource

/** Creates the EventSource. Tests replace it with a fake (see testing/fake-event-source.ts). */
export const EVENT_SOURCE_FACTORY = new InjectionToken<(url: string) => EventSource>('EVENT_SOURCE_FACTORY', {
  providedIn: 'root',
  factory: () => (url: string) => new EventSource(url),
});

/** Same rule as the backend: "logs" matches "logs.ollama" but not "logsx". Empty prefix = everything. */
export function topicMatches(topic: string, prefix: string): boolean {
  return prefix === '' || topic === prefix || topic.startsWith(prefix + '.');
}

/**
 * One Server-Sent-Events connection for the whole app.
 * Every message is {topic, data}; components register for a topic prefix with on().
 */
@Injectable({ providedIn: 'root' })
export class StreamService {
  private readonly create = inject(EVENT_SOURCE_FACTORY);
  private readonly _state = signal<StreamState>('idle');
  readonly state = this._state.asReadonly();

  retryDelayMs = 3000;                 // only used when the browser gives up (proxy error page, 5xx)
  private source: EventSource | null = null;
  private everOpen = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly topicHandlers = new Set<{ prefix: string; fn: TopicHandler }>();
  private readonly stateHandlers = new Set<(s: StreamState) => void>();

  /** Idempotent: a second call while connected does nothing. */
  connect(): void {
    if (this.source) return;
    this.setState(this.everOpen ? 'reconnecting' : 'connecting');
    const es = this.create(STREAM_URL);
    this.source = es;
    es.onopen = () => {
      this.everOpen = true;
      this.setState('open');
    };
    es.onmessage = (ev: MessageEvent<string>) => this.dispatch(ev.data);
    es.onerror = () => {
      if (es.readyState === READY_STATE_CLOSED) {
        // browser stopped retrying (e.g. dev proxy answered 504 while the backend restarted) -> retry ourselves
        es.close();
        this.source = null;
        this.setState('reconnecting');
        this.retryTimer = setTimeout(() => {
          this.retryTimer = null;
          this.connect();
        }, this.retryDelayMs);
      } else {
        this.setState(this.everOpen ? 'reconnecting' : 'connecting');   // browser retries by itself
      }
    };
  }

  disconnect(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.source?.close();
    this.source = null;
    this.setState('idle');
  }

  /** Register for a topic prefix. Returns the unsubscribe function - call it in ngOnDestroy/DestroyRef. */
  on<T = unknown>(prefix: string, fn: (data: T, topic: string) => void): () => void {
    const entry = { prefix, fn: fn as TopicHandler };
    this.topicHandlers.add(entry);
    return () => this.topicHandlers.delete(entry);
  }

  onStateChange(fn: (s: StreamState) => void): () => void {
    this.stateHandlers.add(fn);
    return () => this.stateHandlers.delete(fn);
  }

  private setState(s: StreamState): void {
    if (this._state() === s) return;   // the browser fires onerror on every retry; report changes only
    this._state.set(s);
    for (const fn of [...this.stateHandlers]) fn(s);
  }

  private dispatch(raw: string): void {
    let msg: { topic?: unknown; data?: unknown };
    try {
      msg = JSON.parse(raw);
    } catch {
      return;                          // not our envelope; never let one bad message kill the stream
    }
    if (typeof msg.topic !== 'string') return;
    for (const h of [...this.topicHandlers]) {
      if (!topicMatches(msg.topic, h.prefix)) continue;
      try {
        h.fn(msg.data, msg.topic);
      } catch (e) {
        console.error(`stream handler for "${h.prefix}" failed`, e);   // one broken page must not stop the others
      }
    }
  }
}
