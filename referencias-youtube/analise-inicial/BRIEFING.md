# Briefing para análise de referências — canal Grana Própria (Léo Freitas)

Público fixo: CLT que quer ganhar dinheiro além do salário. Todo vídeo do Léo conversa com esse CLT.

História do Léo (use nas ideias, sempre a partir dela):
- 2020: motoboy CLT levando sangue/plaquetas para hospitais em Florianópolis, salário ~R$ 3 mil, morava em comunidade, guardava dinheiro debaixo da gaveta.
- Caiu em golpes e pirâmide; perdeu R$ 5 mil em apostas no impulso. Estudou, começou com banca de R$ 100, aportava R$ 600–800/mês do salário; fazia R$ 700–800/mês de renda extra.
- Juntou ~R$ 35 mil (banca + reserva de emergência de R$ 20 mil), calculou que aguentava 1 ano, pediu demissão em 2021.
- 2022: abriu canal no YouTube sobre trade esportivo (hoje 188 mil inscritos). Monetizou com AdSense (R$ 47 mil em 2024), grupo VIP (R$ 76 mil), CPA de parceiros, operações próprias.
- 2024: empresa faturou ~R$ 1 milhão, lucro líquido R$ 570 mil, equipe (editora, gestor de tráfego, social media). Hoje: mais de R$ 3 milhões faturados no total.
- Conselhos dele: domine sua área, plano de ação com metas, 1 canal só, produto bom, esteira de produtos (R$ 19,90 → R$ 97 → R$ 197), "CNPJ rico, empresário pobre" (separar PF de PJ), perder o medo do julgamento, princípios.
- Prometeu ao público: série "do zero ao milhão" e desafio "do zero aos primeiros 100 mil" com canal novo documentado.
- EVITE centrar ideias em apostas: o CLT não replica; no máximo uma frase de contexto.

Tom do Léo: 2ª pessoa ("você"), informal de Floripa ("pila", "pô", "tropa", "rapaziada", "tá?"), fé presente mas NÃO no começo do vídeo, conselhos em lista, objeções na voz do público ("Léo, mas eu não sei em que sou bom..."), prova com números reais.

Regras de roteiro (vêm da retenção real dos vídeos dele):
1. Primeiros 30 s = só promessa + prova (número/planilha na tela em até 10 s). Nada de "eu me chamo Léo Freitas".
2. Pedido de like/inscrição e fala de propósito/Deus só depois da primeira entrega (~3–4 min) ou no fim.
3. Não repetir a promessa duas vezes.
4. Título "passo a passo" exige passo 1 prático no 1º minuto.
5. Toda seção traz um número.

Temas JÁ cobertos nos últimos 3 meses (NÃO repetir): (a) a história dele do motoboy ao 1º milhão com a planilha de 2024; (b) passo a passo genérico "ganhar dinheiro na internet com seu conhecimento" (descobrir em que é bom, 1 rede, esteira, medo de gravar); (c) lista de profissões digitais que pagam bem.

Canal em inglês: adaptar para o Brasil (R$, CLT, MEI, CNPJ, Pix, Tesouro, CDB, FGTS, 13º), nunca traduzir literal.

## O que o Léo aprovou e reprovou (38 avaliações: 31 👍 · 7 👎) — SIGA ISSO
REPETIR (aprovado):
- Conta/simulação feita na tela com os números do Léo (15 de 15 aprovadas): "quantas vendas de R$ 10 para substituir o salário", "quanto investir para ganhar R$ 3 mil sem trabalhar".
- A história dele como régua do CLT: "se eu ganhasse R$ 3 mil de novo", "os R$ 35 mil que me deixaram pedir demissão".
- Renda extra FEITA NA TELA: criar produto de R$ 9,90–19,90 com IA e tentar vender em 24 h / 7 dias; testar R$ 20 de anúncio.
- Lições aprendidas perdendo dinheiro; desmontar promessa de guru ("venda 100% automática existe?").
EVITAR (reprovado):
- Monetização de audiência/influenciador (quanto paga ter seguidores, publi, fixo + comissão): 3 de 3 👎.
- Qualquer gancho ligado às bets/apostas.
- Dropshipping ou modelos que dependem de importação ("drop não funciona no Brasil").
- Repetir o mesmo ângulo entre ideias (ex.: "CNPJ rico, empresário pobre" já tem 3 ideias — NÃO usar de novo).

## Saída: para CADA vídeo, atualize o arquivo existente
/home/user/agente-ytb/referencias-youtube/analise-inicial/analises/{video_id}.json
Leia o JSON atual (tem relevancia, tema, motivo_relevancia), MANTENHA esses campos e acrescente:
{
  "gancho": "texto LITERAL dos primeiros ~15 segundos da transcrição (as primeiras ~40 palavras faladas)",
  "estrutura": ["tópico 1 do roteiro", "tópico 2", "..."],   // 5–9 itens, na ordem do vídeo
  "por_que_clicaram": "padrão do título (e da promessa/thumbnail inferida pelo título): número, curiosidade, contraste, autoridade...",
  "por_que_assistiram": "o que no tema e no roteiro segura a audiência (open loops, prova, passo a passo, história...)",
  "ideias": [
    {"id": "{video_id}-1", "titulo": "título para o canal do Léo (estilo YouTube BR, pode usar CAIXA ALTA parcial)",
     "gancho": "fala dos primeiros 15 s, em 1ª pessoa, no tom do Léo, seguindo as regras de roteiro (número logo de cara)",
     "topicos": ["4–7 tópicos do roteiro, cada um com um número/fato concreto da história do Léo quando possível"]},
    ... 2 ou 3 ideias ...
  ]
}
Se uma ideia depender de um fato da vida do Léo que NÃO está nesta página, não afirme: acrescente na ideia
"a_conferir": "o que o Léo precisa confirmar". NUNCA use vidIQ nem qualquer ferramenta que gaste créditos. Se a transcrição vier traduzida para o inglês
(vídeo brasileiro), use-a para entender o vídeo, copie o gancho como veio e acrescente "gancho_traduzido": true —
o gancho original em português é preenchido depois no Mac pela legenda.
Escreva JSON válido (UTF-8, ensure_ascii=False). Valide com: python3 -c "import json;json.load(open('ARQUIVO'))".
Ideias devem ser diferentes entre si e não repetir os temas já cobertos. Português do Brasil.
