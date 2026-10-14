#!/usr/bin/env bash
# Rodada automática: atualiza o meu canal, coleta as referências, recalcula, baixa legendas e (com a chave da Anthropic) analisa os outliers novos.
# Chamado pelo agendamento do Mac (agendar_mac.sh) ou à mão: bash ~/.claude/skills/referencias-youtube/rodada_semanal.sh
set -uo pipefail
SKILL="$HOME/.claude/skills/referencias-youtube"
WORK="${REFERENCIAS_WORKDIR:-$HOME/GranaPropria}"
PY="${REFERENCIAS_PYTHON:-python3}"
mkdir -p "$WORK/referencias" && cd "$WORK" || exit 1
echo "===== $(date '+%Y-%m-%d %H:%M') rodada semanal ====="
"$PY" "$SKILL/scripts/meu_canal.py" || echo "AVISO: meu_canal.py falhou"
"$PY" "$SKILL/scripts/postados.py" --licoes || echo "AVISO: relatório dos vídeos falhou"
"$PY" "$SKILL/scripts/referencias.py" coletar || echo "AVISO: coleta falhou"
"$PY" "$SKILL/scripts/referencias.py" transcrever || echo "AVISO: transcrição falhou"
"$PY" "$SKILL/scripts/aprendizado.py" || echo "AVISO: aprendizado falhou"
# com ANTHROPIC_API_KEY no .env: o Claude dá nota e escreve ideias dos outliers novos (até auto_max_por_rodada)
"$PY" "$SKILL/scripts/referencias.py" analisar || echo "AVISO: análise automática falhou"
"$PY" "$SKILL/scripts/painel.py" || echo "AVISO: painel falhou"
echo "===== fim $(date '+%H:%M') ====="
