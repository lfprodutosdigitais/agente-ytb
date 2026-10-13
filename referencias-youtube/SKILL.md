---
name: referencias-youtube
description: Sistema de referências de vídeo para o canal Grana Própria (Léo Freitas). Analisa o próprio canal via API oficial do Google, coleta vídeos outliers de canais de referência e gera ideias adaptadas ao público CLT que quer renda além do salário. Use quando o usuário pedir /referencias-youtube, "rodar referências", "atualizar meu-canal" ou ideias de vídeo baseadas em referências.
---

# Referências YouTube — Grana Própria

Público fixo de TODO vídeo: **CLT que quer ganhar dinheiro além do salário**. O Léo conta o que fez
para sair do CLT e ficar milionário empreendendo e criando novas fontes de renda.

- Pasta da skill (scripts, `.env`, `client_secret.json`, `config.json`, `canais.txt`): `~/.claude/skills/referencias-youtube/`
- Saídas: `./referencias/` no diretório onde o Claude Code está rodando.
- Scripts: `python3 ~/.claude/skills/referencias-youtube/scripts/<script>.py` (só biblioteca padrão + `yt-dlp`).

Antes de tudo: se `~/.claude/skills/referencias-youtube/.env` não tem `GOOGLE_REFRESH_TOKEN`, guie o
usuário pelo `README.md` da skill (seção "Configurar o Google") e rode `scripts/auth_google.py`.

## Fase 1 — Meu canal

1. `python3 ~/.claude/skills/referencias-youtube/scripts/meu_canal.py`
   (`--check` testa só o token; `--refresh` ignora o cache; o cache fica em `./referencias/cache/meu-canal/`).
2. O script gera `./referencias/meu-canal-dados.json` e `./referencias/meu-canal.md` com a parte numérica
   e blocos de análise `<!-- CLAUDE:chave instrução -->` … `<!-- /CLAUDE:chave -->`. **Escreva a análise
   ENTRE as duas marcas** (mantenha as marcas). O script preserva esse texto nas rodadas seguintes; a cada
   rodada, revise os blocos já preenchidos contra os números novos e atualize o que mudou:
   - Leia as transcrições em `./referencias/transcricoes/{video_id}.md` (comece pelos vídeos acima da
     mediana, pelos de retenção mais baixa e pelos dos últimos 90 dias; amostre os demais).
   - **Tom**: pessoa gramatical, vocabulário, ritmo, como abre os vídeos, bordões, uso de números/provas.
   - **Temas**: classifique cada vídeo em um tema (sair do CLT, renda extra, negócio/empreender,
     investimento, mentalidade, história pessoal, dívida/orçamento, notícia, outro); tabela tema → qtd,
     views medianas, x mediana.
   - **Acima da mediana**: separe "por que clicaram" (título, thumb, CTR) de "por que assistiram"
     (gancho literal = campo `gancho_literal` no JSON, tema, estrutura, % assistida).
   - **Retenção**: para cada queda listada, abra a transcrição naquele tempo e diga o que estava
     acontecendo (gancho longo, enrolação, pedido de inscrição, mudança de assunto). Feche com 3-5 regras
     práticas de roteiro para o Léo.
   - **Cobertos nos últimos 3 meses**: lista de TEMAS (não títulos) — a Fase 2 usa para não repetir.
   - **Views pagas**: a coluna "% anúncio" mostra quanto das views veio de Google Ads. Vídeo com ≥30% de
     anúncio tem retenção e % assistida puxadas para baixo; compare orgânico com orgânico e diga isso.
3. Se o JSON/markdown disser que impressões/CTR estão vazios, avise o usuário com a mensagem exata.
4. Mostre o `meu-canal.md` final ao usuário.

## Fases 2–4

Ainda não implementadas (a construção avança fase a fase, só depois da anterior testada).
