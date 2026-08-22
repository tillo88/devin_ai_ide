#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR=/var/lib/ai-rig/searxng/runtime
CONTROL_DIR=/usr/local/lib/ai-rig-searxng
UNIT_PATH=/etc/systemd/system/ai-rig-searxng.service
LEGACY_SETTINGS=/mnt/ai-rig-shared/searxng/config/settings.yml

fail() { printf 'SEARXNG_INSTALL=FAIL %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "root required"

install_files() {
  for source in docker-compose.yml config/settings.yml.example searxng_control.sh ai-rig-searxng.service; do
    [[ -f "$SOURCE_DIR/$source" && ! -L "$SOURCE_DIR/$source" ]] \
      || fail "source missing or unsafe: $source"
  done

  install -d -m 0755 -o root -g root "$RUNTIME_DIR" "$CONTROL_DIR"
  install -d -m 0700 -o root -g root "$RUNTIME_DIR/config"
  install -m 0644 -o root -g root "$SOURCE_DIR/docker-compose.yml" "$RUNTIME_DIR/docker-compose.yml"
  install -m 0600 -o root -g root "$SOURCE_DIR/config/settings.yml.example" "$RUNTIME_DIR/config/settings.yml.example"
  install -m 0755 -o root -g root "$SOURCE_DIR/searxng_control.sh" "$CONTROL_DIR/searxng_control"
  install -m 0644 -o root -g root "$SOURCE_DIR/ai-rig-searxng.service" "$UNIT_PATH"

  if [[ ! -e "$RUNTIME_DIR/config/settings.yml" ]]; then
    if [[ -f "$LEGACY_SETTINGS" && ! -L "$LEGACY_SETTINGS" ]]; then
      install -m 0600 -o root -g root "$LEGACY_SETTINGS" "$RUNTIME_DIR/config/settings.yml"
    else
      secret="$(openssl rand -hex 32)"
      template="$(<"$SOURCE_DIR/config/settings.yml.example")"
      printf '%s\n' "${template/CAMBIAMI_openssl_rand_hex_32/$secret}" \
        >"$RUNTIME_DIR/config/settings.yml"
      chown root:root "$RUNTIME_DIR/config/settings.yml"
      chmod 0600 "$RUNTIME_DIR/config/settings.yml"
    fi
  fi

  [[ -f "$RUNTIME_DIR/config/settings.yml" && ! -L "$RUNTIME_DIR/config/settings.yml" ]] \
    || fail "runtime settings missing or unsafe"
  grep -q CAMBIAMI "$RUNTIME_DIR/config/settings.yml" \
    && fail "runtime settings still contain placeholder"

  systemctl daemon-reload
  systemd-analyze verify "$UNIT_PATH" >/dev/null
  printf 'SEARXNG_INSTALL=PASS action=install runtime=%s service_mutation=false\n' "$RUNTIME_DIR"
}

check_files() {
  [[ -f "$RUNTIME_DIR/docker-compose.yml" ]] || fail "runtime compose missing"
  [[ -x "$CONTROL_DIR/searxng_control" ]] || fail "runtime control missing"
  [[ -f "$RUNTIME_DIR/config/settings.yml" ]] || fail "runtime settings missing"
  grep -Fq '127.0.0.1:8081:8080' "$RUNTIME_DIR/docker-compose.yml" \
    || fail "loopback port binding missing"
  grep -q CAMBIAMI "$RUNTIME_DIR/config/settings.yml" \
    && fail "runtime settings still contain placeholder"
  (cd "$RUNTIME_DIR" && docker compose config --quiet)
  printf 'SEARXNG_INSTALL=PASS action=check\n'
}

case "${1:---check}" in
  --install)
    install_files
    check_files
    ;;
  --activate)
    install_files
    check_files
    systemctl enable ai-rig-searxng.service >/dev/null
    systemctl restart ai-rig-searxng.service
    systemctl is-active --quiet ai-rig-searxng.service || fail "service not active"
    /usr/local/lib/ai-rig-searxng/searxng_control status
    printf 'SEARXNG_INSTALL=PASS action=activate\n'
    ;;
  --check) check_files ;;
  *) fail "usage: $0 --install|--activate|--check" ;;
esac
