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

## Fase 2 — Referências

Script: `python3 ~/.claude/skills/referencias-youtube/scripts/referencias.py <ação>`. Cortes e pesos em
`config.json` (`corte_longo`, `corte_short`, `vizinhos`, `idade_minima_dias`, `em_alta_fator`, ...).

1. **Coleta** (`coletar`): Apify `streamers/youtube-scraper`, últimos 50 longos de cada canal (Shorts desligados:
   `max_shorts: 0` no `config.json`; ponha 50 para voltar a coletar) de
   `canais.txt`. Grava `./referencias/videos.json`, um snapshot de views por dia em `historico.json` e o custo
   de cada run em `custos-apify.json`. Depois calcula os scores e gera `candidatos.json`.
   - Canal novo ou primeira vez: rode antes `teste @handle` e mostre ao usuário o custo e a estimativa.
2. **Scores** (automático; `calcular` refaz sem gastar Apify):
   - `outlier_score` = views ÷ mediana dos ~10 vídeos do mesmo canal e formato mais próximos na data.
     Outlier se ≥ `corte_longo` (3x) ou ≥ `corte_short` (5x); vídeos com menos de 7 dias ficam de fora.
   - `engajamento_x` = (comentários/views) ÷ média do canal; acima de 1 soma bônus no `score`.
   - `em_alta`: com 2+ snapshots, views/dia ≥ 3x a média do canal em vídeo de até 30 dias.
3. **Relevância (você, Claude)**: para cada id em `candidatos.json → pendentes_relevancia`, leia título,
   descrição e canal e grave `./referencias/analises/{video_id}.json` com:
   `{"relevancia": 1-5, "motivo_relevancia": "...", "tema": "<um dos temas>"}`.
   Temas: sair do CLT, renda extra, negócio/empreender, investimento, mentalidade, história pessoal,
   dívida/orçamento, notícia, outro. Régua: 5 = fala direto com o CLT que quer renda além do salário
   (sair do CLT, renda extra, primeiro negócio); 3 = adaptável com a história do Léo; 1 = não serve
   (ex.: análise de ação específica, notícia sem gancho de renda). Se existir `./referencias/avaliacoes.json`
   e `./referencias/aprendizados.md`, use os 👍/👎 como exemplos de calibragem.
4. **Transcrição**: `transcrever` baixa a legenda (yt-dlp, pt/en) de quem tem relevância ≥ 3 em
   `./referencias/transcricoes/{video_id}.md`.
5. **Análise (você, Claude)** de cada id em `pendentes_analise` (rode `calcular` para atualizar a lista).
   Antes, leia `meu-canal.md` (tom, regras de roteiro, temas cobertos nos últimos 3 meses) e
   `aprendizados.md` se existir. Complete o mesmo `analises/{video_id}.json` com:
   ```json
   {"gancho": "primeiros 15 s, literal da transcrição",
    "estrutura": ["tópico 1", "tópico 2", "..."],
    "por_que_clicaram": "padrão de título e thumbnail",
    "por_que_assistiram": "tema e roteiro",
    "ideias": [{"id": "<video_id>-1", "titulo": "...", "gancho": "fala dos primeiros 15 s",
                "topicos": ["..."]}]}
   ```
   - 2–3 ideias, contadas a partir da história do Léo (motoboy CLT em Floripa → reserva de R$ 35 mil →
     pediu demissão em 2021 → empresa de milhões), no tom dele e seguindo as regras de roteiro do
     `meu-canal.md` (nada de pedido de like/propósito nos primeiros 30 s; número na tela logo no começo).
   - Canal em inglês (`idioma: en`): adaptar para o Brasil (R$, CLT, MEI, CNPJ, Tesouro, CDB, FGTS), nunca
     traduzir literal.
   - Não repetir tema da seção "Cobertos nos últimos 3 meses" do `meu-canal.md`.
6. **Temas validados**: `temas` (ou `calcular`) grava `temas-validados.json`: mesmo tema outlier em 2+
   canais nos últimos 90 dias. Ideias desses temas têm prioridade.

## Fases 3–4

Ainda não implementadas (a construção avança fase a fase, só depois da anterior testada).
