"""Material de publicação de um vídeo do Léo: descrição, capítulos, comentário fixado, tags e hashtags.

Fonte: a legenda do próprio vídeo (yt-dlp; funciona com vídeo público ou não listado). Se o YouTube ainda não gerou
a legenda (leva de minutos a horas depois do upload), usa o roteiro da ideia, com os capítulos estimados pela
duração real do vídeo — e avisa que são aproximados.

Os capítulos são conferidos pelas regras do YouTube (primeiro em 0:00, pelo menos 3, cada um com 10 s ou mais).
Os links fixos da descrição (grupo, Instagram...) ficam em ./referencias/publicacao/links.txt: o Claude nunca
inventa link.

Saída: ./referencias/publicacao/{video_id}.json e {video_id}.txt (pronto para colar no YouTube Studio).
"""
import re

from yt_common import fmt_time, log, now, out_dir, read_json, write_json

import referencias as R

MAX_LEGENDA = 60000  # caracteres (~40 min de fala)
MIN_CAPITULO_S = 10
ID_RE = re.compile(r"^[\w-]{11}$")

SCHEMA = {
    "type": "object",
    "properties": {
        "descricao": {"type": "string"},
        "capitulos": {"type": "array", "items": {"type": "object", "properties": {
            "inicio_s": {"type": "integer"}, "titulo": {"type": "string"}},
            "required": ["inicio_s", "titulo"], "additionalProperties": False}},
        "comentario_fixado": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["descricao", "capitulos", "comentario_fixado", "tags", "hashtags"],
    "additionalProperties": False,
}


def pasta():
    return out_dir() / "publicacao"


def video_id(entrada):
    """Link ou id -> id de 11 caracteres (ValueError se não reconhecer)."""
    t = (entrada or "").strip()
    if ID_RE.match(t):
        return t
    import youtube_publico
    try:
        tipo, val = youtube_publico.parse_entrada(t)
    except ValueError:
        tipo, val = None, None
    if tipo != "video":
        raise ValueError("Cole o link do vídeo (pode ser não listado), ex.: youtu.be/ABC123xyz_0")
    return val


def links_fixos():
    f = pasta() / "links.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def salvar_links(texto):
    pasta().mkdir(parents=True, exist_ok=True)
    (pasta() / "links.txt").write_text((texto or "").strip() + "\n", encoding="utf-8")


def capitulos_validos(caps, duracao_s=None):
    """Aplica as regras do YouTube: ordena, começa em 0:00, tira os de menos de 10 s; [] se sobrarem menos de 3."""
    out = []
    for c in sorted(caps, key=lambda c: c["inicio_s"]):
        t, tit = max(0, int(c["inicio_s"])), c["titulo"].strip()
        if not tit or (duracao_s and t >= duracao_s - MIN_CAPITULO_S):
            continue
        if not out:
            t = 0
        elif t - out[-1]["inicio_s"] < MIN_CAPITULO_S:
            continue
        out.append({"inicio_s": t, "titulo": tit})
    return out if len(out) >= 3 else []


def capitulos_do_roteiro(rot, duracao_s):
    """Seções do roteiro posicionadas na duração real, pela fração de palavras."""
    pal = [len(s["fala"].split()) for s in rot["secoes"]]
    total, acc, out = sum(pal) or 1, 0, []
    for s, n in zip(rot["secoes"], pal):
        out.append({"inicio_s": round(acc / total * duracao_s), "titulo": s["nome"]})
        acc += n
    return out


def texto_final(p):
    """Descrição pronta para colar: corpo + capítulos + links fixos + hashtags."""
    partes = [p["descricao"].strip()]
    if p["capitulos"]:
        partes.append("\n".join(f"{fmt_time(c['inicio_s'])} {c['titulo']}" for c in p["capitulos"]))
    if p.get("links"):
        partes.append(p["links"])
    if p["hashtags"]:
        partes.append(" ".join(p["hashtags"]))
    return "\n\n".join(partes)


def para_txt(p):
    return "\n".join([
        f"# {p['titulo']}", "",
        "===== DESCRIÇÃO =====", p["descricao_completa"], "",
        "===== COMENTÁRIO FIXADO =====", p["comentario_fixado"], "",
        "===== TAGS =====", ", ".join(p["tags"]), "",
    ])


def _ideia_do_video(vid):
    import postados
    return postados.postadas(R.ratings_atuais()).get(vid)


def gerar(entrada, ideia_id=None, client=None, cfg=None, g=None):
    import gerador
    import roteiro as RT
    import transcricao
    cfg = cfg or R.config()
    vid = video_id(entrada)
    ideia_id = ideia_id or _ideia_do_video(vid)
    det = {}
    try:  # título e duração reais (o dono vê até vídeo privado)
        import meu_canal
        if g is None:
            from yt_common import Google
            g = Google()
        det = meu_canal.video_details(g, [vid]).get(vid) or {}
    except (Exception, SystemExit) as e:
        log(f"  detalhes do vídeo {vid}: {e}")
    rot = read_json(RT.pasta() / f"{ideia_id}.json", None) if ideia_id else None
    titulo = det.get("title") or (rot["titulos"][rot["titulo_recomendado"]]["titulo"] if rot and rot.get("titulos") else "")
    dur = det.get("duration_s")
    leg = None
    try:
        leg = transcricao.fetch(vid, titulo)
    except Exception as e:  # sem yt-dlp/rede: tenta pelo roteiro
        log(f"  legenda {vid}: {e}")
    if leg:
        fonte, texto = "legenda", leg.read_text(encoding="utf-8")[:MAX_LEGENDA]
    elif rot and dur:
        fonte = "roteiro"
        texto = "\n\n".join(f"[{fmt_time(c['inicio_s'])}] {s['nome']}\n{s['fala']}"
                            for c, s in zip(capitulos_do_roteiro(rot, dur), rot["secoes"]))
    else:
        raise ValueError("O YouTube ainda não gerou a legenda desse vídeo (leva de minutos a algumas horas depois do "
                         "upload)" + ("" if rot else " e essa ideia não tem roteiro") + ". Tente de novo mais tarde.")
    links = links_fixos()
    pedido = "\n".join([
        "## Vídeo do Léo pronto para publicar",
        f"Título: {titulo or '(ainda sem título)'}" + (f" · duração {fmt_time(dur)}" if dur else ""),
        "Legenda real do vídeo, com tempo [m:ss]:" if fonte == "legenda" else
        "Ainda sem legenda: este é o ROTEIRO, com tempos ESTIMADOS pela duração real:",
        texto,
        "## Tarefa: material de publicação",
        "- descricao: só o texto (sem capítulos, links ou hashtags, o sistema acrescenta). As 2 primeiras linhas "
        "aparecem antes do 'mais': gancho para o CLT com a promessa do vídeo e a palavra-chave principal. Depois, "
        "2–4 parágrafos curtos no tom do Léo resumindo o que o vídeo entrega, com os números que ele fala.",
        "- capitulos: 4–10 capítulos com inicio_s em SEGUNDOS tirados dos tempos acima (o primeiro em 0), títulos "
        "curtos e com curiosidade (não 'Introdução').",
        "- comentario_fixado: pergunta que puxa comentário de quem é CLT (ex.: quanto ganha, o que já tentou), "
        "no tom do Léo, 1–3 frases.",
        "- tags: 10–15 termos que um CLT digitaria na busca. hashtags: 3, começando com #.",
        "Não invente links, preços, números ou fatos que não estão no vídeo. Português do Brasil.",
    ])
    r, resp = gerador._chamar(client, cfg, SCHEMA, pedido, max_tokens=12000)
    caps = capitulos_validos(r["capitulos"], dur)
    p = {"video_id": vid, "ideia_id": ideia_id, "titulo": titulo, "duracao_s": dur, "fonte": fonte,
         "gerado_em": now().isoformat(), "descricao": r["descricao"].strip(), "capitulos": caps,
         "comentario_fixado": r["comentario_fixado"].strip(), "links": links,
         "tags": [t.strip() for t in r["tags"] if t.strip()][:15],
         "hashtags": ["#" + h.strip().lstrip("#").replace(" ", "") for h in r["hashtags"] if h.strip()][:3]}
    p["descricao_completa"] = texto_final(p)
    p["avisos"] = ([] if caps else ["Capítulos ficaram fora das regras do YouTube (mínimo 3, 10 s cada) e foram tirados."]) + \
                  (["Capítulos estimados pelo roteiro: confira os tempos no vídeo antes de publicar."] if fonte == "roteiro" else []) + \
                  ([] if links else ["Sem links fixos: cadastre grupo/Instagram uma vez e eles entram em toda descrição."])
    pasta().mkdir(parents=True, exist_ok=True)
    write_json(pasta() / f"{vid}.json", p)
    (pasta() / f"{vid}.txt").write_text(para_txt(p), encoding="utf-8")
    u = resp.usage
    log(f"Publicação ({vid}, fonte {fonte}): {len(caps)} capítulos — tokens: {u.input_tokens} entrada, {u.output_tokens} saída")
    return p
