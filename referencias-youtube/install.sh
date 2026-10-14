#!/usr/bin/env bash
# Instala/atualiza a skill em ~/.claude/skills/referencias-youtube (cópia, fora do git).
# Preserva .env, client_secret.json, config.json e canais.txt que você já tenha editado.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.claude/skills/referencias-youtube"
mkdir -p "$DEST/scripts"
cp "$SRC"/SKILL.md "$SRC"/README.md "$SRC"/.env.example "$DEST"/
cp "$SRC"/scripts/*.py "$DEST/scripts/"
cp "$SRC"/rodada_semanal.sh "$SRC"/agendar_mac.sh "$DEST"/ && chmod +x "$DEST"/*.sh
for f in config.json canais.txt; do [ -f "$DEST/$f" ] || cp "$SRC/$f" "$DEST/$f"; done
# config.json já existente: acrescenta só as chaves novas, sem mudar o que você editou
python3 - "$SRC/config.json" "$DEST/config.json" <<'PY'
import json, sys
new, cur = (json.load(open(f, encoding="utf-8")) for f in sys.argv[1:3])
json.dump({**new, **cur}, open(sys.argv[2], "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PY
[ -f "$DEST/.env" ] || { cp "$SRC/.env.example" "$DEST/.env"; chmod 600 "$DEST/.env"; }
command -v yt-dlp >/dev/null || echo "Instale o yt-dlp: pip install -U yt-dlp  (ou brew install yt-dlp)"
echo "Skill instalada em $DEST"
[ -f "$DEST/client_secret.json" ] || echo "Próximo passo: coloque o client_secret.json em $DEST e rode: python3 $DEST/scripts/auth_google.py"
