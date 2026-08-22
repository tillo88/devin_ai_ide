#!/usr/bin/env bash
set -Eeuo pipefail

RUNTIME_DIR=/var/lib/ai-rig/searxng/runtime
CONTAINER=searxng

fail() { printf 'SEARXNG_CONTROL=FAIL %s\n' "$*" >&2; exit 1; }

container_running() {
  docker ps --quiet --filter "name=^/${CONTAINER}$" | grep -q .
}

start_service() {
  [[ -f "$RUNTIME_DIR/docker-compose.yml" ]] || fail "compose runtime missing"
  [[ -f "$RUNTIME_DIR/config/settings.yml" ]] || fail "settings runtime missing"
  grep -q CAMBIAMI "$RUNTIME_DIR/config/settings.yml" \
    && fail "SearXNG secret placeholder is still present"
  cd "$RUNTIME_DIR"
  docker compose up -d --pull never
  for _ in $(seq 1 60); do
    if curl --fail --silent --max-time 2 \
      'http://127.0.0.1:8081/search?q=health&format=json' | grep -q '"results"'; then
      printf 'SEARXNG_CONTROL=PASS action=start\n'
      return 0
    fi
    sleep 1
  done
  fail "JSON readiness timeout"
}

stop_service() {
  if ! container_running; then
    docker rm "$CONTAINER" >/dev/null 2>&1 || true
    printf 'SEARXNG_CONTROL=PASS action=stop already_inactive=true\n'
    return 0
  fi

  # Una sola SIGTERM, poi attesa bounded e verifica. Nessun hard-kill fallback.
  docker update --restart=no "$CONTAINER" >/dev/null
  docker kill --signal TERM "$CONTAINER" >/dev/null
  for _ in $(seq 1 120); do
    container_running || break
    sleep 0.5
  done
  container_running && fail "container survived SIGTERM; manual recovery required"
  docker rm "$CONTAINER" >/dev/null
  printf 'SEARXNG_CONTROL=PASS action=stop signal=SIGTERM\n'
}

case "${1:-}" in
  start) start_service ;;
  stop) stop_service ;;
  status)
    container_running && printf 'SEARXNG_CONTROL=PASS state=running\n' \
      || fail "state=inactive"
    ;;
  *) fail "usage: $0 start|stop|status" ;;
esac
