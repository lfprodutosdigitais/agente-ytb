#!/usr/bin/env bash
# Instalação guiada no Mac: instala a skill, o yt-dlp, guia o Google Cloud (abrindo cada página),
# pega o client_secret.json da pasta Downloads sozinho, autoriza e roda a Fase 1.
# Pode rodar quantas vezes quiser: pula o que já estiver feito.
set -uo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
SKILL="$HOME/.claude/skills/referencias-youtube"
WORK="${REFERENCIAS_WORKDIR:-$HOME/GranaPropria}"

b=$(tput bold 2>/dev/null || true); n=$(tput sgr0 2>/dev/null || true)
step() { echo; echo "${b}━━ $1${n}"; }
pause() { echo; read -r -p "   ➜ $1 Quando terminar, aperte Enter... " _ </dev/tty; }
open_url() { echo "   abrindo: $1"; open "$1" 2>/dev/null || true; }

step "1/7 Verificando Python"
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "   Precisa do Python 3.9+. Vou abrir o instalador das ferramentas de linha de comando da Apple."
  xcode-select --install 2>/dev/null || true
  echo "   Termine a instalação na janela que abriu e rode este instalador de novo."
  exit 1
fi
echo "   $(python3 --version) ok"

step "2/7 Instalando yt-dlp (para baixar legendas)"
if python3 -c 'import yt_dlp' 2>/dev/null; then
  python3 -m pip install --user -q -U yt-dlp 2>/dev/null || true
  echo "   yt-dlp ok"
else
  python3 -m pip install --user -q -U yt-dlp 2>/dev/null \
    || python3 -m pip install --user -q -U --break-system-packages yt-dlp 2>/dev/null \
    || { command -v brew >/dev/null && brew install yt-dlp; }
  if python3 -c 'import yt_dlp' 2>/dev/null || command -v yt-dlp >/dev/null; then echo "   yt-dlp ok"
  else echo "   ⚠️  Não consegui instalar o yt-dlp. Sem ele não há transcrições (o resto funciona)."; fi
fi

step "3/7 Instalando a skill em $SKILL"
bash "$SRC/install.sh" | sed 's/^/   /'

step "4/7 Token do Apify"
if grep -qE '^APIFY_TOKEN=.+' "$SKILL/.env" 2>/dev/null; then
  echo "   já configurado"
else
  echo "   Pegue em https://console.apify.com/settings/integrations (campo 'Personal API tokens')."
  read -r -s -p "   Cole o APIFY_TOKEN aqui (não aparece na tela) e Enter, ou só Enter para pular: " tok </dev/tty; echo
  if [ -n "$tok" ]; then
    python3 - "$SKILL" "$tok" <<'PY'
import sys; sys.path.insert(0, sys.argv[1] + "/scripts")
import yt_common, pathlib
yt_common.save_env_var("APIFY_TOKEN", sys.argv[2], pathlib.Path(sys.argv[1]) / ".env")
PY
    echo "   salvo no .env da skill"
  fi
fi

step "5/7 Google Cloud (só a primeira vez)"
if [ -f "$SKILL/client_secret.json" ]; then
  echo "   client_secret.json já está na skill — pulando."
else
  echo "   Use a conta Google dona do canal @Granapropria em todas as páginas."
  echo
  echo "   ${b}a) Criar projeto${n}: nome 'grana-propria-referencias' → Criar."
  echo "      Depois confira no topo da tela que esse projeto está selecionado."
  open_url "https://console.cloud.google.com/projectcreate"
  pause "Projeto criado?"

  echo "   ${b}b) Ativar as 3 APIs${n}: em cada aba que abrir, clique em 'Ativar' (Enable)."
  open_url "https://console.cloud.google.com/apis/library/youtube.googleapis.com"
  open_url "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com"
  open_url "https://console.cloud.google.com/apis/library/youtubereporting.googleapis.com"
  pause "Ativou as 3?"

  echo "   ${b}c) Tela de consentimento${n}: clique em 'Começar' (Get started)."
  echo "      Nome do app: Referencias YouTube · e-mail de suporte: o seu"
  echo "      Público: ${b}External${n} · contato: seu e-mail · aceite a política → Criar."
  open_url "https://console.cloud.google.com/auth/overview"
  pause "Criou?"

  echo "   ${b}d) Escopos${n}: 'Add or remove scopes' → na caixa 'Manually add scopes' cole as 2 linhas abaixo"
  echo "      → 'Add to table' → Update → Save:"
  echo "        https://www.googleapis.com/auth/youtube.readonly"
  echo "        https://www.googleapis.com/auth/yt-analytics.readonly"
  printf '%s\n%s' "https://www.googleapis.com/auth/youtube.readonly" "https://www.googleapis.com/auth/yt-analytics.readonly" | pbcopy 2>/dev/null \
    && echo "      (já copiei as 2 linhas pra você: é só colar com Cmd+V)"
  open_url "https://console.cloud.google.com/auth/scopes"
  pause "Salvou os escopos?"

  echo "   ${b}e) Público${n}: em 'Test users' → Add users → seu e-mail → Save."
  echo "      Depois em 'Publishing status' → ${b}Publish app${n} → Confirm. Tem que ficar 'In production'."
  echo "      (Se falar em verificação, ignore: é uso pessoal e só leitura.)"
  open_url "https://console.cloud.google.com/auth/audience"
  pause "Ficou 'In production'?"

  echo "   ${b}f) Credencial${n}: 'Create client' → Application type: ${b}Desktop app${n} → nome: referencias-cli → Create."
  echo "      Na janela que aparece, clique em ${b}Download JSON${n}. Eu pego o arquivo na pasta Downloads sozinho."
  START=$(date +%s)
  open_url "https://console.cloud.google.com/auth/clients"
  echo "   aguardando o download (até 15 min)..."
  found=""
  for _ in $(seq 1 450); do
    for f in "$HOME"/Downloads/client_secret*.json; do
      [ -f "$f" ] || continue
      if [ "$(stat -f %m "$f" 2>/dev/null || echo 0)" -ge "$START" ]; then found="$f"; break; fi
    done
    [ -n "$found" ] && break
    sleep 2
  done
  if [ -z "$found" ]; then
    read -r -p "   Não achei. Arraste o arquivo .json aqui para a janela do Terminal e aperte Enter: " found </dev/tty
    found="${found%\"}"; found="${found#\"}"; found="${found% }"; found="${found//\\ / }"
  fi
  if [ ! -f "$found" ]; then echo "   Arquivo não encontrado. Rode o instalador de novo."; exit 1; fi
  mv "$found" "$SKILL/client_secret.json" && chmod 600 "$SKILL/client_secret.json"
  echo "   ✅ client_secret.json guardado em $SKILL (e removido de Downloads)"
fi

step "6/7 Autorizando o acesso ao seu canal"
if grep -qE '^GOOGLE_REFRESH_TOKEN=.+' "$SKILL/.env" 2>/dev/null && python3 "$SKILL/scripts/meu_canal.py" --check; then
  echo "   já autorizado"
else
  echo "   Vai abrir o navegador:"
  echo "   • escolha a conta do canal (se for conta de marca, escolha 'Grana Própria')"
  echo "   • 'O Google não verificou este app' → Avançado → Acessar Referencias YouTube"
  echo "   • marque TODAS as caixas → Continuar"
  python3 "$SKILL/scripts/auth_google.py" || exit 1
  python3 "$SKILL/scripts/meu_canal.py" --check || exit 1
fi

step "7/7 Rodando a Fase 1 (coleta do seu canal) em $WORK/referencias"
mkdir -p "$WORK" && cd "$WORK" || exit 1
python3 "$SKILL/scripts/meu_canal.py" || exit 1

echo
echo "${b}✅ Tudo pronto.${n} Dados em: $WORK/referencias/"
if ! command -v claude >/dev/null; then
  echo
  echo "   Falta o Claude Code no seu Mac (é ele que roda a skill)."
  read -r -p "   Instalar agora? [s/N] " yn </dev/tty
  if [[ "$yn" =~ ^[sS] ]]; then curl -fsSL https://claude.ai/install.sh | bash; fi
fi
echo
echo "   Para o diagnóstico completo (tom, temas, porquês), rode:"
echo "     ${b}cd $WORK && claude${n}"
echo "   e digite: ${b}/referencias-youtube${n}"
open "$WORK/referencias" 2>/dev/null || true
