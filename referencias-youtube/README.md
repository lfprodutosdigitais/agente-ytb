# referencias-youtube

Skill do Claude Code para o canal **Grana Própria**. Fica em `~/.claude/skills/referencias-youtube/`;
as saídas vão para `./referencias/` no diretório onde você roda o Claude Code.

Requisitos: Python 3.9+ e `yt-dlp` (`pip install -U yt-dlp`). Nada mais.

## Instalar

```bash
git clone https://github.com/lfprodutosdigitais/agente-ytb && cd agente-ytb
bash referencias-youtube/install.sh
```

O `install.sh` **copia** os arquivos (não cria link), então `.env` e `client_secret.json` ficam só em
`~/.claude/skills/referencias-youtube/`, fora de qualquer git. Rodar de novo atualiza os scripts sem
apagar seus segredos, `config.json` e `canais.txt`.

Coloque seu token do Apify no `.env`: `APIFY_TOKEN=apify_api_...`

## Configurar o Google (uma vez só, ~10 min)

Use a mesma conta Google que é dona do canal @Granapropria.

1. **Criar o projeto** — <https://console.cloud.google.com/projectcreate>
   Nome: `grana-propria-referencias` → Criar. Confira no topo da tela que esse projeto está selecionado.

2. **Ativar as APIs** (clique em "Ativar" em cada uma, com o projeto selecionado):
   - YouTube Data API v3 — <https://console.cloud.google.com/apis/library/youtube.googleapis.com>
   - YouTube Analytics API — <https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com>
   - YouTube Reporting API — <https://console.cloud.google.com/apis/library/youtubereporting.googleapis.com>
     (plano B para impressões/CTR, caso não venham pela Analytics API)

3. **Tela de consentimento OAuth** — <https://console.cloud.google.com/auth/overview>
   - "Começar" / "Get started": nome do app `Referencias YouTube`, e-mail de suporte = o seu.
   - Público / Audience: **External**. Contato: seu e-mail. Aceite a política → Criar.
   - Em **Data access / Acesso a dados** (<https://console.cloud.google.com/auth/scopes>) → "Add or remove scopes",
     adicione só estes dois (somente leitura) e salve:
     - `https://www.googleapis.com/auth/youtube.readonly`
     - `https://www.googleapis.com/auth/yt-analytics.readonly`
   - Em **Audience / Público** (<https://console.cloud.google.com/auth/audience>):
     - "Test users" → **Add users** → seu e-mail.
     - **Publishing status** → **Publish app** → confirme. O status deve ficar **"In production"**.
       Em "Testing" o refresh token expira em 7 dias. O Google pode dizer que o app precisa de
       verificação: ignore — é uso pessoal (<100 usuários) e escopos só de leitura funcionam sem verificar.
       Na hora do login vai aparecer "O Google não verificou este app": é normal, o app é seu.

4. **Credencial OAuth "Desktop app"** — <https://console.cloud.google.com/auth/clients>
   - **Create client** → Application type: **Desktop app** → nome `referencias-cli` → Create.
   - **Download JSON** e salve como:
     `~/.claude/skills/referencias-youtube/client_secret.json`

5. **Autorizar** (abre o navegador):
   ```bash
   python3 ~/.claude/skills/referencias-youtube/scripts/auth_google.py
   ```
   - Escolha a conta do canal. Se o canal for uma *conta de marca* (Brand Account), escolha o canal
     Grana Própria na lista que aparece.
   - "O Google não verificou este app" → **Avançado** → **Acessar Referencias YouTube (não seguro)**.
   - Marque **todas** as caixas de permissão → Continuar.
   - O refresh token é gravado no `.env` da skill.

6. **Testar**:
   ```bash
   python3 ~/.claude/skills/referencias-youtube/scripts/meu_canal.py --check
   ```
   Deve imprimir o nome do canal e as views dos últimos 28 dias.

### Problemas comuns

| Erro | Solução |
|---|---|
| `invalid_grant` | App ainda em "Testing" (token venceu em 7 dias) ou acesso revogado. Publique o app (passo 3) e rode `auth_google.py` de novo. |
| `access_denied` / "app bloqueado" | Seu e-mail não está em Test users, ou o app está em "Testing" e você usou outra conta. |
| `403 ... has not been used in project` | A API não foi ativada no projeto (passo 2). O erro traz o link para ativar. |
| "Nenhum canal nessa conta" | O canal é Brand Account: rode `auth_google.py` e escolha o canal (não a conta pessoal). |
| Não veio refresh token | Remova o app em <https://myaccount.google.com/permissions> e autorize de novo. |

## Rodar

No Claude Code, dentro da pasta onde quer as saídas: `/referencias-youtube`.

Ou só a parte de dados da Fase 1:

```bash
python3 ~/.claude/skills/referencias-youtube/scripts/meu_canal.py            # usa cache
python3 ~/.claude/skills/referencias-youtube/scripts/meu_canal.py --refresh  # re-puxa tudo
```

Cache: vídeos com mais de 35 dias na última coleta não são consultados de novo (as métricas já
estabilizaram); transcrições são baixadas uma vez só.

## Rodada automática (toda semana)

```bash
bash ~/.claude/skills/referencias-youtube/agendar_mac.sh          # agenda: toda segunda às 7h
bash ~/.claude/skills/referencias-youtube/agendar_mac.sh remover  # cancela
```

Atualiza o seu canal, coleta as referências (YouTube API, grátis) e baixa as legendas. Log em
`~/GranaPropria/referencias/agendamento.log`. Descobrir canais novos:
`python3 ~/.claude/skills/referencias-youtube/scripts/referencias.py descobrir`.

## Como usar no dia a dia

- **Rodar de novo:** no Claude Code, dentro de `~/GranaPropria`, digite `/referencias-youtube`. Sem o Claude,
  só os dados: `bash ~/.claude/skills/referencias-youtube/rodada_semanal.sh` (já roda sozinho toda segunda 7h).
- **Ver as ideias:** dois cliques em `~/GranaPropria/Abrir painel.command` (salva sozinho e deixa adicionar
  referência na hora) — ou `open ~/GranaPropria/referencias/painel.html` (modo arquivo) — dê 👍/👎, mude o status, cole a URL quando
  postar e clique em **Exportar avaliações** (o arquivo vai para Downloads e é lido na rodada seguinte).
- **Adicionar canal:** uma linha em `~/.claude/skills/referencias-youtube/canais.txt`
  (`https://www.youtube.com/@canal`, com ` | en` no fim se for em inglês). Para achar canais:
  `python3 ~/.claude/skills/referencias-youtube/scripts/referencias.py descobrir` → `canais-sugeridos.md`.
- **Mudar os cortes:** `~/.claude/skills/referencias-youtube/config.json` — `corte_longo` (3), `corte_short` (5),
  `vizinhos` (10), `idade_minima_dias` (7), `em_alta_fator` (3), `max_longos` (50), `max_shorts` (0 = sem Shorts),
  `termos_descoberta`.
- **Custo:** coleta pela YouTube Data API é grátis (~100 de 10.000 unidades diárias com 14 canais; a descoberta
  usa ~800). Apify só se `"fonte": "apify"`: ~US$ 0,004 por vídeo (~US$ 0,20 por canal por rodada).

## Ideia nova na hora (opcional)

Quando o Léo dá 👎 no painel (aberto pelo atalho `Abrir painel`), o Claude cria uma ideia nova no lugar:

```bash
python3 -m pip install --user --break-system-packages anthropic   # Python do Homebrew exige essa flag
open -e ~/.claude/skills/referencias-youtube/.env   # preencher ANTHROPIC_API_KEY=sk-ant-...
```

Com a chave, **"＋ Adicionar referência"** também faz tudo sozinho: coleta o canal, e o Claude dá a nota de
relevância, lê a legenda e escreve 2–3 ideias de cada outlier (até `auto_max_por_canal`, 6). O progresso aparece no
topo do painel e as ideias entram em "Ideias para gravar" conforme ficam prontas. Pelo terminal:
`referencias.py adicionar @canal --en` faz o mesmo; `referencias.py analisar` analisa os pendentes (a rodada de
segunda também roda isso, até `auto_max_por_rodada`, 10).

Chave em <https://console.anthropic.com/settings/keys>. Custo: alguns centavos de dólar por ideia
(modelo Claude Opus 5.5; a parte fixa do pedido fica em cache). Sem a chave, o 👎 só troca pela próxima da fila.

## Testes

```bash
cd referencias-youtube && python3 -m unittest discover -s tests
```
