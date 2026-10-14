#!/usr/bin/env bash
# Agenda a rodada semanal no Mac (launchd): toda segunda às 7h. Se o Mac estiver dormindo, roda ao acordar.
# Uso: bash ~/.claude/skills/referencias-youtube/agendar_mac.sh            (agenda)
#      bash ~/.claude/skills/referencias-youtube/agendar_mac.sh remover    (cancela)
set -euo pipefail
SKILL="$HOME/.claude/skills/referencias-youtube"
WORK="${REFERENCIAS_WORKDIR:-$HOME/GranaPropria}"
LABEL="com.granapropria.referencias"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LABEL_AL="com.granapropria.alertas"
PLIST_AL="$HOME/Library/LaunchAgents/$LABEL_AL.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootout "gui/$(id -u)" "$PLIST_AL" 2>/dev/null || true
if [ "${1:-}" = "remover" ]; then rm -f "$PLIST" "$PLIST_AL"; echo "Agendamentos removidos."; exit 0; fi
PY="$(command -v python3)"
mkdir -p "$HOME/Library/LaunchAgents" "$WORK/referencias"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SKILL/rodada_semanal.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$PY"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>REFERENCIAS_PYTHON</key><string>$PY</string>
    <key>REFERENCIAS_WORKDIR</key><string>$WORK</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$WORK/referencias/agendamento.log</string>
  <key>StandardErrorPath</key><string>$WORK/referencias/agendamento.log</string>
</dict>
</plist>
PL
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
# alerta de vídeo explodindo: a cada 3 h (e ao acordar o Mac, se perdeu a hora)
cat > "$PLIST_AL" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL_AL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$SKILL/scripts/alerta.py</string></array>
  <key>WorkingDirectory</key><string>$WORK</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(dirname "$PY"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StartInterval</key><integer>10800</integer>
  <key>StandardOutPath</key><string>$WORK/referencias/alertas.log</string>
  <key>StandardErrorPath</key><string>$WORK/referencias/alertas.log</string>
</dict>
</plist>
PL
plutil -lint "$PLIST_AL" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST_AL"
echo "Agendado: toda segunda às 7h (log em $WORK/referencias/agendamento.log)."
echo "Alerta de vídeo explodindo: a cada 3 h (log em $WORK/referencias/alertas.log)."
echo "Para rodar agora e testar: launchctl kickstart gui/$(id -u)/$LABEL"
