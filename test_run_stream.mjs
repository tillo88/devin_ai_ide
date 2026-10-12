import test from "node:test";
import assert from "node:assert/strict";

import { connectRunStream, retryDelay, RETRY_DELAYS_MS } from "./devin/ui/static/js/run_stream.js";

function harness({ alive = () => true } = {}) {
  const opened = [];
  const timers = [];
  const events = [];
  let seq = -1;
  const reconnects = [];
  const stream = connectRunStream({
    open: (afterSeq) => {
      const source = { afterSeq, closed: false, close() { this.closed = true; } };
      opened.push(source);
      return source;
    },
    lastSeq: () => seq,
    onEvent: (event) => { events.push(event); seq = event.seq; },
    shouldReconnect: alive,
    onReconnect: () => reconnects.push(seq),
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimer: (id) => { timers[id - 1].cleared = true; },
  });
  const fire = () => { const t = timers.shift(); if (!t.cleared) t.fn(); };
  return { stream, opened, timers, events, reconnects, fire };
}

test("a dropped stream reconnects from the last received event", () => {
  const h = harness();
  h.opened[0].onmessage({ data: JSON.stringify({ seq: 7, type: "act" }) });
  h.opened[0].onerror();
  assert.equal(h.opened[0].closed, true);
  assert.equal(h.timers[0].ms, RETRY_DELAYS_MS[0]);
  h.fire();
  assert.equal(h.opened.length, 2);
  assert.equal(h.opened[1].afterSeq, 7);
  assert.deepEqual(h.reconnects, [7]);
});

test("delays grow while the stream keeps failing and reset after a message", () => {
  const h = harness();
  h.opened[0].onerror(); assert.equal(h.timers[0].ms, 1000); h.fire();
  h.opened[1].onerror(); assert.equal(h.timers[0].ms, 2000); h.fire();
  h.opened[2].onmessage({ data: JSON.stringify({ seq: 1 }) });
  h.opened[2].onerror(); assert.equal(h.timers[0].ms, 1000);
  assert.equal(retryDelay(99), RETRY_DELAYS_MS.at(-1));
});

test("a finished run is not reconnected", () => {
  let alive = true;
  const h = harness({ alive: () => alive });
  alive = false;
  h.opened[0].onerror();
  assert.equal(h.timers.length, 0);
  assert.equal(h.opened.length, 1);
});

test("the run ending during the wait cancels the reconnection", () => {
  let alive = true;
  const h = harness({ alive: () => alive });
  h.opened[0].onerror();
  alive = false;
  h.fire();
  assert.equal(h.opened.length, 1);
});

test("stop closes the stream and cancels a pending reconnection", () => {
  const h = harness();
  h.opened[0].onerror();
  h.stream.stop();
  assert.equal(h.timers[0].cleared, true);
  h.fire();
  assert.equal(h.opened.length, 1);
});

test("an invalid event does not break the stream", () => {
  const h = harness();
  h.opened[0].onmessage({ data: "{not json" });
  h.opened[0].onmessage({ data: JSON.stringify({ seq: 3 }) });
  assert.deepEqual(h.events.map((e) => e.seq), [3]);
});
