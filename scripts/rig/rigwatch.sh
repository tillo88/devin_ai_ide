#!/usr/bin/env bash
# rigwatch.sh - snapshot del carico del rig: temperatura CPU + container Docker +
# top dei processi. Pensato per `watch`, per beccare chi satura i core durante un run.
#
# REGOLA (dal runbook rig): NIENTE nvidia-smi/NVML qui. Interrogare NVML mentre
# CUDA e' attivo puo' lasciare il driver in D-state -> hang. La temperatura GPU
# si guarda SOLO one-shot PRIMA o DOPO un run, mai durante l'inferenza.
#
# Uso:
#   watch -n2 'bash ~/devin_ai_ide/scripts/rig/rigwatch.sh'
#   # se `docker` richiede root e tillo non e' nel gruppo docker:
#   sudo -v && sudo watch -n2 "bash $HOME/devin_ai_ide/scripts/rig/rigwatch.sh"

echo "== CPU TEMP =="
if command -v sensors >/dev/null 2>&1; then
    # Intel (i9-10900X -> coretemp): Package id 0 + Core N. AMD: Tctl/Tdie.
    sensors 2>/dev/null | grep -E "Package id|Core [0-9]|Tctl|Tdie" || echo "(sensors senza dati: sensors-detect?)"
else
    # Fallback senza lm-sensors: thermal zone del kernel (milligradi).
    for z in /sys/class/thermal/thermal_zone*; do
        [ -r "$z/temp" ] && printf "%-16s %s C\n" "$(cat "$z/type" 2>/dev/null)" "$(( $(cat "$z/temp")/1000 ))"
    done
fi

echo "== CONTAINER =="
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}" 2>/dev/null \
    || echo "(docker: serve sudo, oppure aggiungi tillo al gruppo docker)"

echo "== TOP CPU (10) =="
top -b -o %CPU -n1 | head -17 | tail -11

echo "== LOAD =="
uptime
