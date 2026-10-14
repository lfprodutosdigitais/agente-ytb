#!/usr/bin/env bash
# Rodada automática (sem Claude): atualiza o meu canal, coleta as referências, recalcula e baixa legendas.
# Chamado pelo agendamento do Mac (agendar_mac.sh) ou à mão: bash ~/.claude/skills/referencias-youtube/rodada_semanal.sh
set -uo pipefail
SKILL="$HOME/.claude/skills/referencias-youtube"
WORK="${REFERENCIAS_WORKDIR:-$HOME/GranaPropria}"
PY="${REFERENCIAS_PYTHON:-python3}"
mkdir -p "$WORK/referencias" && cd "$WORK" || exit 1
echo "===== $(date '+%Y-%m-%d %H:%M') rodada semanal ====="
"$PY" "$SKILL/scripts/meu_canal.py" || echo "AVISO: meu_canal.py falhou"
"$PY" "$SKILL/scripts/referencias.py" coletar || echo "AVISO: coleta falhou"
"$PY" "$SKILL/scripts/referencias.py" transcrever || echo "AVISO: transcrição falhou"
echo "===== fim $(date '+%H:%M') ====="
