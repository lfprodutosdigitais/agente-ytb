"""Ideias que o próprio público pediu: perguntas que se repetem nos comentários.

1. Puxa (YouTube Data API, 1 unidade por vídeo) os comentários mais relevantes dos vídeos do Léo e dos melhores
   outliers em português. Guarda só texto e curtidas (nunca o nome de quem comentou), em cache por vídeo.
2. Fica com perguntas e pedidos ("como", "quanto", "faz um vídeo"...), sem links, ordenados por curtidas.
3. O Claude agrupa as dúvidas que se repetem e escreve 1 ideia por grupo, na história e no tom do Léo.
4. Cada grupo vira ./referencias/analises/publico-*.json (origem "comentarios"): aparece no feed como as outras
   ideias, com Sim/Não, roteiro, thumb e publicação.

Uso: python3 scripts/comentarios.py
"""
import hashlib
import re
from pathlib import Path
from datetime import timedelta

from yt_common import HttpError, log, now, out_dir, parse_date, read_json, write_json

import referencias as R

DATA = "https://www.googleapis.com/youtube/v3"
PERGUNTA = re.compile(r"\?|\b(como|quanto|quantos|qual|quais|vale a pena|d[aá] pra|tem como|faz(er)? um v[ií]deo|"
                      r"ensina|explica|e se|onde|por ?que|preciso|consigo|ganho|recebo|sal[aá]rio|come[cç]ar)\b", re.I)
LINK = re.compile(r"https?://|www\.|\.com\b", re.I)
POR_VIDEO = 40      # comentários filtrados por vídeo
TOTAL = 450         # comentários que vão para o Claude
MAX_REFS = 15       # vídeos de referência consultados
VALIDADE = {"leo": 2, "ref": 14}  # dias até buscar de novo

_IDEIA = {"type": "object", "properties": {
    "titulo": {"type": "string"}, "gancho": {"type": "string"},
    "topicos": {"type": "array", "items": {"type": "string"}}, "a_conferir": {"type": "string"}},
    "required": ["titulo", "gancho", "topicos", "a_conferir"], "additionalProperties": False}


def schema():
    import gerador
    grupo = {"type": "object", "properties": {
        "pergunta": {"type": "string"},
        "tema": {"type": "string", "enum": gerador.TEMAS},
        "pessoas": {"type": "integer"},
        "exemplos": {"type": "array", "items": {"type": "string"}},
        "ideia": _IDEIA},
        "required": ["pergunta", "tema", "pessoas", "exemplos", "ideia"], "additionalProperties": False}
    return {"type": "object", "properties": {"grupos": {"type": "array", "items": grupo}},
            "required": ["grupos"], "additionalProperties": False}


def fontes(cfg):
    """[(video_id, 'leo'|'ref', título)]: todos os vídeos do Léo + os melhores outliers em português."""
    out = [(v["video_id"], "leo", v.get("title")) for v in
           (read_json(out_dir() / "meu-canal-dados.json", {}) or {}).get("videos") or []]
    videos = read_json(R.paths()["videos"], {}) or {}
    analyses = R.load_analyses()
    refs = [v for vid, v in videos.items() if (v.get("idioma") or "pt") == "pt" and (v.get("comentarios") or 0) > 0
            and (analyses.get(vid) or {}).get("relevancia", 0) >= 4]
    refs.sort(key=lambda v: -(v.get("score") or 0))
    out += [(v["video_id"], "ref", v.get("titulo")) for v in refs[:cfg.get("comentarios_max_refs", MAX_REFS)]]
    return out


DIAG = {"videos": 0, "recebidos": 0, "desativados": 0, "via_ytdlp": 0, "erro": ""}


def via_ytdlp(vid, n=100):
    """Comentários mais curtidos pelo yt-dlp (sem API). [] se não conseguir."""
    import json as _json
    import subprocess
    import tempfile
    import transcricao
    with tempfile.TemporaryDirectory() as tmp:
        cmd = transcricao.ytdlp_cmd() + ["--skip-download", "--write-comments", "--no-warnings", "-q",
               "--extractor-args", f"youtube:max_comments={n},all,0,0;comment_sort=top",
               "-o", f"{tmp}/%(id)s.%(ext)s", f"https://www.youtube.com/watch?v={vid}"]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        f = next(Path(tmp).glob("*.info.json"), None)
        if not f:
            return []
        info = _json.loads(f.read_text(encoding="utf-8"))
    return [{"texto": (c.get("text") or "").strip()[:600], "likes": c.get("like_count") or 0, "respostas": 0}
            for c in info.get("comments") or [] if c.get("parent") in (None, "root")]


def baixar(g, vid, tipo):
    """Comentários (texto + curtidas) de um vídeo, com cache. [] se os comentários estão desativados."""
    f = out_dir() / "cache" / "comentarios" / f"{vid}.json"
    c = read_json(f, None)
    DIAG["videos"] += 1
    if c and c.get("itens") and now() - parse_date(c["em"]) < timedelta(days=VALIDADE[tipo]):
        DIAG["recebidos"] += len(c["itens"])
        return c["itens"]
    itens = None
    try:
        r = g.get(f"{DATA}/commentThreads", {"part": "snippet", "videoId": vid, "maxResults": 100,
                                             "order": "relevance", "textFormat": "plainText"})
        itens = []
        for it in r.get("items") or []:
            sn = it["snippet"]["topLevelComment"]["snippet"]
            itens.append({"texto": (sn.get("textDisplay") or sn.get("textOriginal") or "").strip()[:600],
                          "likes": sn.get("likeCount", 0), "respostas": it["snippet"].get("totalReplyCount", 0)})
    except HttpError as e:
        if "commentsDisabled" in e.body or e.status == 404:  # comentários desativados ou vídeo removido
            DIAG["desativados"] += 1
            return []
        DIAG["erro"] = f"API {e.status}: {e.body[:160]}"
    if not itens:  # API recusou ou veio vazia: tenta pelo yt-dlp
        try:
            itens = via_ytdlp(vid)
            DIAG["via_ytdlp"] += 1 if itens else 0
        except Exception as e:
            DIAG["erro"] = DIAG["erro"] or f"yt-dlp: {e}"[:160]
            itens = []
    DIAG["recebidos"] += len(itens)
    if itens:
        write_json(f, {"em": now().isoformat(), "itens": itens})
    return itens


def filtrar(itens):
    out = [c for c in itens if 25 <= len(c["texto"]) <= 600 and not LINK.search(c["texto"])
           and PERGUNTA.search(c["texto"])]
    return sorted(out, key=lambda c: -(c["likes"] + 2 * c["respostas"]))[:POR_VIDEO]


def coletar(g, cfg):
    """{'leo': [...], 'ref': [...]} de comentários filtrados (texto, curtidas, vídeo)."""
    DIAG.update(videos=0, recebidos=0, desativados=0, via_ytdlp=0, erro="")
    out, unidades = {"leo": [], "ref": []}, 0
    for vid, tipo, titulo in fontes(cfg):
        itens = baixar(g, vid, tipo)
        unidades += 1
        out[tipo] += [{**c, "video": titulo} for c in filtrar(itens)]
    for k in out:
        out[k].sort(key=lambda c: -(c["likes"] + 2 * c["respostas"]))
    log(f"Comentários: {DIAG['recebidos']} lidos em {DIAG['videos']} vídeos → {len(out['leo'])} perguntas no canal "
        f"do Léo, {len(out['ref'])} nas referências (~{unidades} unidades de cota"
        + (f"; {DIAG['via_ytdlp']} vídeo(s) pelo yt-dlp" if DIAG["via_ytdlp"] else "")
        + (f"; {DIAG['desativados']} com comentários desativados" if DIAG["desativados"] else "") + ")"
        + (f" · último erro: {DIAG['erro']}" if DIAG["erro"] else ""))
    return out


def existentes():
    return {aid: a for aid, a in R.load_analyses().items() if a.get("origem") == "comentarios"}


def pedido(coments, ja):
    leo = coments["leo"][:TOTAL // 2]
    ref = coments["ref"][:TOTAL - len(leo)]
    fmt = lambda c: f"- ({c['likes']} curtidas) {c['texto'].replace(chr(10), ' ')}"
    return "\n".join([
        "## Comentários no canal do LÉO (o público dele)", *([fmt(c) for c in leo] or ["(nenhum ainda)"]),
        "## Comentários em vídeos de referência (mesmo público CLT)", *([fmt(c) for c in ref] or ["(nenhum)"]),
        "## Perguntas que já viraram ideia (NÃO repetir)", *[f"- {a.get('pergunta')}" for a in ja.values()],
        "## Tarefa",
        "Agrupe as dúvidas e pedidos que SE REPETEM (mesma dúvida dita de jeitos diferentes) e que o Léo pode "
        "responder com a história dele. Ignore elogios, spam, ataques e dúvidas sobre apostas. Até 6 grupos, do mais "
        "pedido para o menos; cada grupo com pelo menos 3 comentários parecidos.",
        "- pergunta: a dúvida em 1 frase, na voz do público ('Léo, com R$ 1.500 de salário dá pra...?').",
        "- pessoas: quantos comentários acima entram no grupo (conte de verdade).",
        "- exemplos: 2–3 comentários copiados literalmente da lista (sem nomes).",
        "- ideia: 1 vídeo que responde essa dúvida, seguindo as regras de roteiro do Léo (título estilo YouTube BR; "
        "gancho = fala dos primeiros 15 s em 1ª pessoa com número logo de cara; 4–7 tópicos com números). Fatos "
        "da vida do Léo fora do briefing vão em a_conferir (vazio se não houver).",
    ])


def gerar(g=None, client=None, cfg=None):
    """Coleta, agrupa e grava as ideias. Devolve a lista de análises novas."""
    import gerador
    cfg = cfg or R.config()
    if g is None:
        from yt_common import Google
        g = Google()
    coments = coletar(g, cfg)
    if len(coments["leo"]) + len(coments["ref"]) < 3:
        raise ValueError("Poucos comentários com perguntas ainda. Tente de novo quando os vídeos tiverem mais comentários.")
    ja = existentes()
    r, resp = gerador._chamar(client, cfg, schema(), pedido(coments, ja), max_tokens=16000)
    vistos = {re.sub(r"\W+", " ", (a.get("pergunta") or "").lower()).strip() for a in ja.values()}
    novos, dia = [], now().strftime("%Y%m%d")
    for gr in r["grupos"]:
        chave = re.sub(r"\W+", " ", gr["pergunta"].lower()).strip()
        if gr["pessoas"] < 2 or chave in vistos:
            continue
        vistos.add(chave)
        aid = f"publico-{dia}-{hashlib.sha1(chave.encode()).hexdigest()[:6]}"
        i = gr["ideia"]
        ideia = {"id": f"{aid}-1", "titulo": i["titulo"].strip(), "gancho": i["gancho"].strip(),
                 "topicos": [t.strip() for t in i["topicos"] if t.strip()], "origem": "comentarios"}
        if i.get("a_conferir", "").strip():
            ideia["a_conferir"] = i["a_conferir"].strip()
        a = {"origem": "comentarios", "relevancia": 5, "tema": gr["tema"], "pergunta": gr["pergunta"].strip(),
             "pessoas": gr["pessoas"], "exemplos": [e.strip() for e in gr["exemplos"] if e.strip()][:3],
             "criado_em": now().isoformat(), "ideias": [ideia]}
        write_json(R.paths()["analises"] / f"{aid}.json", a)
        novos.append(a)
    u = resp.usage
    log(f"{len(novos)} ideia(s) nova(s) a partir dos comentários — tokens: {u.input_tokens} entrada, {u.output_tokens} saída")
    return novos


def main():
    from yt_common import load_env
    import gerador
    load_env()
    ok, motivo = gerador.disponivel()
    if not ok:
        log(f"Ideias dos comentários desligadas: {motivo}")
        return
    try:
        gerar()
    except ValueError as e:
        log(str(e))


if __name__ == "__main__":
    main()
