"""Pacote do vídeo para uma ideia aprovada: 5 títulos + roteiro completo no tom do Léo.

O Claude recebe o briefing, o diagnóstico do canal (tom, quedas de retenção, regras), os aprendizados, a ideia,
a análise da referência e o começo da transcrição dela (para o ritmo). Depois o código confere as regras de
retenção que dá para checar sozinho (pedido de like/inscrição cedo, número na tela no começo, promessa repetida).

Saída: ./referencias/roteiros/{ideia_id}.json e {ideia_id}.md (para abrir no Notes/Docs ou imprimir).
"""
import re

from yt_common import fmt_time, log, median, now, out_dir, read_json, write_json

import referencias as R

TRECHO_REFERENCIA = 12000  # caracteres da transcrição da referência (~8 min): ritmo e estrutura, não o conteúdo
PALAVRAS_POR_MIN = 150     # fala em português num vídeo de YouTube

_SECAO = {"type": "object", "properties": {
    "nome": {"type": "string"},
    "inicio_s": {"type": "integer"},
    "objetivo": {"type": "string"},
    "fala": {"type": "string"},
    "na_tela": {"type": "string"}},
    "required": ["nome", "inicio_s", "objetivo", "fala", "na_tela"], "additionalProperties": False}
_TITULO = {"type": "object", "properties": {"titulo": {"type": "string"}, "por_que": {"type": "string"}},
           "required": ["titulo", "por_que"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "titulos": {"type": "array", "items": _TITULO},
        "titulo_recomendado": {"type": "integer"},
        "promessa": {"type": "string"},
        "secoes": {"type": "array", "items": _SECAO},
        "preparar": {"type": "array", "items": {"type": "string"}},
        "a_conferir": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titulos", "titulo_recomendado", "promessa", "secoes", "preparar", "a_conferir"],
    "additionalProperties": False,
}

PEDIDO_CTA = re.compile(r"inscrev|inscriç|deix[ae] (o |seu |teu )?(like|joinha)|ativ[ae] (o )?sininho|"
                        r"curt[ae] (o |esse )?v[íi]deo|compartilh", re.I)
PROPOSITO = re.compile(r"\bdeus\b|\bfé\b|prop[óo]sito|me chamo|meu nome é|eu sou o l[ée]o", re.I)
NUMERO = re.compile(r"\d")


def pasta():
    return out_dir() / "roteiros"


def duracao_alvo(cfg):
    """Minutos: config `duracao_alvo_min`, senão a mediana dos vídeos longos do Léo (8–20), senão 12."""
    if cfg.get("duracao_alvo_min"):
        return int(cfg["duracao_alvo_min"])
    vs = (read_json(out_dir() / "meu-canal-dados.json", {}) or {}).get("videos") or []
    m = median([v.get("duration_s") for v in vs if v.get("formato") != "short" and v.get("duration_s")])
    return max(8, min(20, round(m / 60))) if m else 12


def _referencia(a, v, trecho):
    linhas = [
        "## Referência que inspirou (outlier no canal dela)",
        f"{v.get('titulo')} — {v.get('canal')} ({v.get('idioma') or 'pt'})",
        f"Estrutura: {' → '.join(a.get('estrutura') or [])}",
        f"Por que assistiram: {a.get('por_que_assistiram') or ''}",
    ]
    if trecho:
        linhas += ["Começo da transcrição da referência (só para ritmo e estrutura; não copie):", trecho]
    return linhas


def pedido(ideia, a, v, trecho, minutos):
    linhas = [
        "## Ideia aprovada pelo Léo",
        f"Título: {ideia['titulo']}", f"Gancho: {ideia.get('gancho', '')}",
        "Tópicos:", *[f"- {t}" for t in ideia.get("topicos") or []],
    ]
    if ideia.get("a_conferir"):
        linhas.append(f"A conferir com o Léo: {ideia['a_conferir']}")
    if a.get("origem") == "comentarios":
        linhas += ["## De onde veio: dúvida que se repete nos comentários do público",
                   f"{a.get('pergunta')} (~{a.get('pessoas')} comentários)", *[f"- “{e}”" for e in a.get("exemplos") or []],
                   "Responda essa dúvida de verdade no roteiro; cite que veio dos comentários."]
    else:
        linhas += _referencia(a, v, trecho)
    linhas += [
        "## Tarefa: pacote do vídeo",
        f"Vídeo de ~{minutos} min (~{minutos * PALAVRAS_POR_MIN} palavras de fala no total).",
        "- titulos: 5 opções no estilo YouTube BR, diferentes entre si (número, contraste, curiosidade, história, "
        "pergunta); por_que em 1 frase. titulo_recomendado = índice (0–4) do melhor para a página inicial.",
        "- promessa: a promessa do vídeo em 1 frase (o que o CLT leva ao final).",
        "- secoes: o roteiro completo, em ordem, com inicio_s (segundos em que a seção começa). A fala é o texto "
        "que o Léo lê, em 2ª pessoa, no tom dele (pila, tropa, tá?), frases curtas, objeções na voz do público. "
        "na_tela = o que mostrar (número, planilha, print, b-roll). Siga as regras de roteiro do diagnóstico: os "
        "primeiros 30 s só promessa + prova com um número na tela em até 10 s; nada de apresentação, pedido de "
        "like/inscrição ou fé/propósito antes da primeira entrega (~3–4 min); não repita a promessa; toda seção "
        "traz um número; termine com um gancho para o próximo vídeo.",
        "- preparar: o que o Léo precisa separar antes de gravar (prints, planilha, contas).",
        "- a_conferir: cada fato da vida do Léo que você usou e NÃO está no briefing (nunca invente números dele).",
    ]
    return "\n".join(linhas)


def checar(r, por_secao=True):
    """Avisos das regras de retenção que o código consegue conferir.
    por_secao=False (texto colado pelo Léo, em parágrafos): junta os parágrafos sem número num aviso só."""
    avisos = []
    secoes = r.get("secoes") or []
    inicio = " ".join(s["fala"] for s in secoes if s["inicio_s"] < 30)
    if secoes and not NUMERO.search(inicio + " ".join(s["na_tela"] for s in secoes if s["inicio_s"] < 30)):
        avisos.append("Nenhum número nos primeiros 30 s (regra: prova com número na tela em até 10 s).")
    for s in secoes:
        if s["inicio_s"] < 180 and PEDIDO_CTA.search(s["fala"]):
            avisos.append(f"Pedido de like/inscrição em {fmt_time(s['inicio_s'])} (“{s['nome']}”): leve para depois "
                          "da primeira entrega (~3–4 min) — é onde os vídeos do Léo mais perdem gente.")
        if s["inicio_s"] < 60 and PROPOSITO.search(s["fala"]):
            avisos.append(f"Apresentação ou fé/propósito no começo (“{s['nome']}”): deixe para depois da primeira entrega.")
        if por_secao and not NUMERO.search(s["fala"] + s["na_tela"]):
            avisos.append(f"Seção sem número (“{s['nome']}”): toda seção deve trazer um número concreto.")
    if not por_secao:
        sem = [s for s in secoes if len(s["fala"].split()) >= 40 and not NUMERO.search(s["fala"])]
        if sem:
            avisos.append(f"{len(sem)} de {len(secoes)} parágrafos longos sem nenhum número (o primeiro em "
                          f"{fmt_time(sem[0]['inicio_s'])}): toda parte deve trazer um número concreto.")
    palavras = sum(len(s["fala"].split()) for s in secoes)
    return avisos, palavras


def para_md(p):
    t = p["titulos"]
    rec = p["titulo_recomendado"] if 0 <= p["titulo_recomendado"] < len(t) else 0
    L = [f"# {t[rec]['titulo'] if t else p['ideia_titulo']}", "",
         f"_Ideia: {p['ideia_titulo']} · ~{p['minutos_estimados']} min ({p['palavras']} palavras) · "
         f"gerado em {p['gerado_em'][:10]}_", "", f"**Promessa:** {p['promessa']}", "", "## Títulos"]
    L += [f"{n + 1}. {x['titulo']}{'  ⭐ recomendado' if n == rec else ''} — {x['por_que']}" for n, x in enumerate(t)]
    if p["avisos"]:
        L += ["", "## ⚠️ Conferir antes de gravar", *[f"- {a}" for a in p["avisos"]]]
    if p["a_conferir"]:
        L += ["", "## Fatos para o Léo confirmar", *[f"- {a}" for a in p["a_conferir"]]]
    if p["preparar"]:
        L += ["", "## Separar antes de gravar", *[f"- {a}" for a in p["preparar"]]]
    L += ["", "## Roteiro"]
    for s in p["secoes"]:
        L += ["", f"### [{fmt_time(s['inicio_s'])}] {s['nome']}", f"_Objetivo: {s['objetivo']}_", "",
              s["fala"], "", f"🎬 **Na tela:** {s['na_tela']}"]
    return "\n".join(L) + "\n"


def gerar(ideia_id, client=None, cfg=None):
    import gerador
    import thumbs
    cfg = cfg or R.config()
    ref_id, a, ideia, v = thumbs.achar_ideia(ideia_id)
    tr = out_dir() / "transcricoes" / f"{ref_id}.md"
    trecho = tr.read_text(encoding="utf-8")[:TRECHO_REFERENCIA] if tr.exists() else ""
    minutos = duracao_alvo(cfg)
    r, resp = gerador._chamar(client, cfg, SCHEMA, pedido(ideia, a, v, trecho, minutos), max_tokens=32000)
    avisos, palavras = checar(r)
    p = {"ideia_id": ideia_id, "ideia_titulo": ideia["titulo"], "referencia": ref_id, "gerado_em": now().isoformat(),
         "minutos_alvo": minutos, "palavras": palavras,
         "minutos_estimados": round(palavras / PALAVRAS_POR_MIN, 1), "avisos": avisos,
         **{k: r[k] for k in SCHEMA["required"]}}
    pasta().mkdir(parents=True, exist_ok=True)
    write_json(pasta() / f"{ideia_id}.json", p)
    (pasta() / f"{ideia_id}.md").write_text(para_md(p), encoding="utf-8")
    u = resp.usage
    log(f"Roteiro ({ideia_id}): {len(p['secoes'])} seções, {palavras} palavras, {len(avisos)} aviso(s) — tokens: "
        f"{u.input_tokens} entrada, {getattr(u, 'cache_read_input_tokens', 0) or 0} do cache, {u.output_tokens} saída")
    return p


# ---------------------------------------------------------------- revisor do roteiro escrito pelo Léo

MAX_TEXTO = 40000
_PROBLEMA = {"type": "object", "properties": {
    "trecho": {"type": "string"}, "em": {"type": "string"}, "problema": {"type": "string"}, "sugestao": {"type": "string"}},
    "required": ["trecho", "em", "problema", "sugestao"], "additionalProperties": False}
SCHEMA_REVISAO = {"type": "object", "properties": {
    "nota": {"type": "integer", "enum": list(range(1, 11))},
    "resumo": {"type": "string"},
    "gancho_sugerido": {"type": "string"},
    "problemas": {"type": "array", "items": _PROBLEMA},
    "pontos_fortes": {"type": "array", "items": {"type": "string"}}},
    "required": ["nota", "resumo", "gancho_sugerido", "problemas", "pontos_fortes"], "additionalProperties": False}


def secoes_do_texto(texto):
    """Parágrafos -> seções com o tempo estimado em que cada um começa (150 palavras/min)."""
    pars = [re.sub(r"\s+", " ", x).strip() for x in re.split(r"\n\s*\n|\n(?=\S)", texto or "")]
    out, acc = [], 0
    for par in (x for x in pars if x):
        palavras = par.split()
        out.append({"nome": " ".join(palavras[:6]) + ("…" if len(palavras) > 6 else ""), "inicio_s": round(acc * 60 / PALAVRAS_POR_MIN),
                    "objetivo": "", "fala": par, "na_tela": ""})
        acc += len(palavras)
    return out


def revisar(ideia_id, texto, client=None, cfg=None):
    """Checagem automática + revisão do Claude de um roteiro escrito pelo Léo. Grava roteiros/{id}-meu.json."""
    import gerador
    cfg = cfg or R.config()
    texto = (texto or "").strip()[:MAX_TEXTO]
    secoes = secoes_do_texto(texto)
    if sum(len(s["fala"].split()) for s in secoes) < 60:
        raise ValueError("Cole o roteiro inteiro (pelo menos uns 30 segundos de fala) para revisar.")
    avisos, palavras = checar({"secoes": secoes}, por_secao=False)
    ideia = None
    if ideia_id:
        import thumbs
        try:
            ideia = thumbs.achar_ideia(ideia_id)[2]
        except ValueError:
            pass
    pedido = "\n".join([
        *(["## Ideia do vídeo", f"Título: {ideia['titulo']}", f"Gancho planejado: {ideia.get('gancho', '')}"] if ideia else []),
        "## Roteiro escrito pelo Léo (tempo estimado de cada parágrafo, 150 palavras/min)",
        *[f"[{fmt_time(s['inicio_s'])}] {s['fala']}" for s in secoes],
        "## A checagem automática já encontrou", *([f"- {a}" for a in avisos] or ["- nada"]),
        "## Tarefa: revisar para retenção",
        "Revise como um editor de YouTube que conhece as quedas de retenção do canal do Léo (diagnóstico acima). "
        "nota 1–10 para o roteiro segurar o CLT até o fim; resumo em 2 frases. problemas: até 8, do mais grave para o "
        "menos, cada um com trecho = cópia LITERAL de até 25 palavras do texto, em = tempo [m:ss] do parágrafo, "
        "problema em 1 frase e sugestao = o trecho reescrito no tom do Léo, pronto para colar. Procure: começo lento, "
        "apresentação/like/fé antes da primeira entrega, promessa repetida, parte sem número ou prova, enrolação, "
        "mudança de assunto sem gancho, final sem chamada para o próximo vídeo. gancho_sugerido: os primeiros 15 s "
        "reescritos. pontos_fortes: 1–3 itens. Não mude fatos nem invente números da vida do Léo.",
    ])
    r, resp = gerador._chamar(client, cfg, SCHEMA_REVISAO, pedido, max_tokens=12000)
    out = {"ideia_id": ideia_id, "texto": texto, "gerado_em": now().isoformat(), "palavras": palavras,
           "minutos_estimados": round(palavras / PALAVRAS_POR_MIN, 1), "avisos": avisos, **r}
    if ideia_id:
        pasta().mkdir(parents=True, exist_ok=True)
        write_json(pasta() / f"{ideia_id}-meu.json", out)
    u = resp.usage
    log(f"Revisão do roteiro ({ideia_id or 'avulso'}): nota {r['nota']}, {len(r['problemas'])} problema(s) — tokens: "
        f"{u.input_tokens} entrada, {u.output_tokens} saída")
    return out
