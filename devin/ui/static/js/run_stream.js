// Flusso eventi di una run che si riconnette da solo.
//
// Il flusso SSE puo' cadere (proxy, frontdoor, rete). Prima si chiudeva e
// basta: log, timeline e pulsanti di approvazione restavano fermi finche'
// l'utente non premeva "Aggiorna". Qui la caduta porta a una riconnessione
// con attesa crescente, ripartendo dall'ultimo evento ricevuto, finche'
// `shouldReconnect()` dice che la run e' ancora viva.

export const RETRY_DELAYS_MS = [1000, 2000, 5000, 10000, 20000];

export function retryDelay(attempt, delays = RETRY_DELAYS_MS) {
  return delays[Math.min(Math.max(attempt, 0), delays.length - 1)];
}

export function connectRunStream({
  open,            // (afterSeq) => EventSource-like {onopen,onmessage,onerror,close}
  lastSeq,         // () => numero dell'ultimo evento ricevuto
  onEvent,         // (event) => void
  shouldReconnect, // () => boolean
  onReconnect = () => {},
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
  delays = RETRY_DELAYS_MS,
}) {
  let source = null;
  let timer = null;
  let attempts = 0;
  let stopped = false;

  function start() {
    if (stopped) return;
    source = open(lastSeq());
    const current = source;
    current.onopen = () => { attempts = 0; };
    current.onmessage = (message) => {
      attempts = 0;
      let event;
      try {
        event = JSON.parse(message.data);
      } catch (err) {
        console.warn("Invalid run event", err);
        return;
      }
      onEvent(event);
    };
    current.onerror = () => {
      current.close();
      if (source !== current || stopped) return;
      source = null;
      if (!shouldReconnect()) return;
      const delay = retryDelay(attempts, delays);
      attempts += 1;
      timer = setTimer(() => {
        timer = null;
        if (stopped || !shouldReconnect()) return;
        onReconnect();
        start();
      }, delay);
    };
  }

  start();
  return {
    stop() {
      stopped = true;
      if (timer !== null) clearTimer(timer);
      timer = null;
      if (source) source.close();
      source = null;
    },
    get attempts() { return attempts; },
  };
}
