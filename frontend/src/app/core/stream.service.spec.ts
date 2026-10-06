import { TestBed } from '@angular/core/testing';

import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { StreamService, topicMatches } from './stream.service';

describe('StreamService', () => {
  let stream: StreamService;

  beforeEach(() => {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({ providers: [fakeEventSourceProvider] });
    stream = TestBed.inject(StreamService);
  });

  it('opens exactly one connection', () => {
    stream.connect();
    stream.connect();
    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.latest().url).toBe('/api/v1/stream');
  });

  it('dispatches by topic prefix and stops after unsubscribe', () => {
    const got: string[] = [];
    const off = stream.on<{ msg: string }>('logs', (d, topic) => got.push(`${topic}:${d.msg}`));
    stream.connect();
    const es = FakeEventSource.latest();
    es.emit('logs.ollama', { msg: 'a' });
    es.emit('dashboard.snapshot', { msg: 'ignored' });
    off();
    es.emit('logs.ollama', { msg: 'after-off' });
    expect(got).toEqual(['logs.ollama:a']);
  });

  it('survives malformed messages and failing handlers', () => {
    const got: unknown[] = [];
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    stream.on('x', () => { throw new Error('boom'); });
    stream.on('x', (d) => got.push(d));
    stream.connect();
    FakeEventSource.latest().emitRaw('not json');
    FakeEventSource.latest().emit('x.y', 1);
    expect(got).toEqual([1]);                     // second handler still ran
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it('reports state changes once and reconnects itself when the browser gives up', async () => {
    const states: string[] = [];
    stream.onStateChange((s) => states.push(s));
    stream.retryDelayMs = 0;
    stream.connect();
    const first = FakeEventSource.latest();
    first.open();
    first.fail();                                // browser retries itself
    first.fail();                                // same state again -> not reported twice
    first.fail(true);                            // browser gave up -> we retry
    await new Promise((r) => setTimeout(r, 0));
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances.length).toBe(2);
    FakeEventSource.latest().open();
    expect(states).toEqual(['connecting', 'open', 'reconnecting', 'open']);
  });

  it('matches topics like the backend', () => {
    expect(topicMatches('logs.ollama', 'logs')).toBe(true);
    expect(topicMatches('logsx.a', 'logs')).toBe(false);
    expect(topicMatches('anything', '')).toBe(true);
  });
});
