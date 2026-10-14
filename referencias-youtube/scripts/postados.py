"""Relatório dos vídeos do Léo: números aos 2, 7 e 30 dias contra a média dele, onde o público saiu e a lição.

- Vídeos: os longos do Léo dos últimos `relatorio_dias` dias (meu-canal-dados.json) + toda ideia marcada "postado"
  com link no painel (busca os dados do vídeo se ainda não estiverem no meu-canal-dados.json).
- Janela de N dias = do dia da publicação até o dia N; só é guardada quando o YouTube já fechou os números
  (~3 dias de atraso). Antes disso aparece como "parcial".
- Quedas de retenção -> seção do roteiro (pela fração de palavras: a fala tem ritmo quase constante).
- Lição (Claude): o que funcionou, o que mudar e uma regra nova; as regras entram no contexto das próximas ideias.

Saída: ./referencias/meus-videos.json (números, refeito a cada atualização) e ./referencias/licoes-videos.json
(lições, preservadas).
"""
import json
from datetime import timedelta

from yt_common import fmt_time, log, median, now, out_dir, parse_date, read_json, write_json

import referencias as R

JANELAS = (2, 7, 30)
ATRASO_DIAS = 3  # o YouTube Analytics leva ~2–3 dias para fechar os números de um dia
PONTOS_CURVA = 50  # a curva média é reamostrada nesses pontos


def arquivo():
    return out_dir() / "meus-videos.json"


def arquivo_licoes():
    return out_dir() / "licoes-videos.json"


def _vid(url):
    import aprendizado
    return aprendizado._video_id_from_url(url)


def postadas(ratings):
    """video_id -> id da ideia, para ideias com status postado + link."""
    return {_vid(a.get("url")): iid for iid, a in (ratings or {}).items()
            if a.get("status") == "postado" and _vid(a.get("url"))}


def lista_videos(g, cfg, ratings):
    """Vídeos longos do Léo para o relatório (mais novo primeiro)."""
    import meu_canal
    dados = read_json(out_dir() / "meu-canal-dados.json", {}) or {}
    own = {v["video_id"]: v for v in dados.get("videos") or []}
    pend = [vid for vid in postadas(ratings) if vid not in own]
    if pend:
        for vid, d in meu_canal.video_details(g, pend).items():
            d["formato"] = "short" if (d.get("duration_s") or 0) <= cfg.get("short_max_segundos", 180) else "longo"
            own[vid] = d
    lim = now() - timedelta(days=cfg.get("relatorio_dias", 120))
    out = [v for v in own.values() if v.get("formato") != "short" and v.get("published_at")
           and (parse_date(v["published_at"]) >= lim or v["video_id"] in postadas(ratings))]
    return sorted(out, key=lambda v: v["published_at"], reverse=True), own


def janela(g, vid, pub, dias, cache, hoje):
    """Métricas dos primeiros `dias` dias; ('parcial', m) se o YouTube ainda não fechou, None se nem começou."""
    import meu_canal
    key = f"{vid}:j{dias}"
    if key in cache:
        return "ok", cache[key]
    fim = pub.date() + timedelta(days=dias - 1)
    fim_real = min(fim, hoje - timedelta(days=1))
    if fim_real < pub.date():
        return None, None
    m = meu_canal.metrics_window(g, vid, pub.date().isoformat(), fim_real.isoformat(), with_impressions=True)
    m.pop("impressions_error", None)
    if fim <= hoje - timedelta(days=ATRASO_DIAS):
        cache[key] = m
        return "ok", m
    return "parcial", m


def curva(g, vid, pub, hoje, cache):
    """Curva de retenção do vídeo inteiro até hoje (refeita no máximo 1x por dia)."""
    import meu_canal
    key = f"{vid}:curva"
    c = cache.get(key)
    if c and c.get("dia") == hoje.isoformat():
        return c["pontos"]
    pts = meu_canal.retention_curve(g, vid, pub.date().isoformat(), hoje.isoformat())
    cache[key] = {"dia": hoje.isoformat(), "pontos": pts}
    return pts


def curva_media(curvas):
    """Média ponto a ponto das curvas (reamostradas), para comparar um vídeo com o padrão do Léo."""
    import meu_canal
    curvas = [c for c in curvas if c]
    if not curvas:
        return []
    xs = [round(i / (PONTOS_CURVA - 1), 4) for i in range(PONTOS_CURVA)]
    return [[x, round(sum(meu_canal.ratio_at(c, x) for c in curvas) / len(curvas), 4)] for x in xs]


def secoes_nas_quedas(rot, quedas):
    """Para cada queda (pos 0–1 do vídeo), a seção do roteiro naquele ponto, pela fração de palavras."""
    if not rot or not rot.get("secoes"):
        return quedas
    pal = [len(s["fala"].split()) for s in rot["secoes"]]
    total = sum(pal) or 1
    limites, acc = [], 0
    for s, n in zip(rot["secoes"], pal):
        acc += n
        limites.append((acc / total, s["nome"]))
    out = []
    for q in quedas:
        nome = next((nome for lim, nome in limites if q["pos"] <= lim), limites[-1][1])
        out.append({**q, "secao": nome})
    return out


def _br(x, casas=1):
    return f"{x:.{casas}f}".replace(".", ",")


def diagnostico(r, med):
    """Leitura rápida e determinística: onde está o problema (ou o acerto)."""
    d = []
    j = r.get("d7") or r.get("d2") or {}
    mj = med.get("d7" if r.get("d7") else "d2") or {}
    if j.get("ctr") is not None and mj.get("ctr"):
        x = j["ctr"] / mj["ctr"]
        if x < 0.8:
            d.append(("ruim", f"Poucos cliques: CTR {_br(j['ctr'])}% contra {_br(mj['ctr'])}% da média — título/thumb."))
        elif x > 1.2:
            d.append(("bom", f"Título/thumb funcionaram: CTR {_br(j['ctr'])}% contra {_br(mj['ctr'])}% da média."))
    g, mg = (r.get("retencao") or {}).get("ret_gancho"), med.get("ret_gancho")
    if g is not None and mg:
        if g < mg - 0.05:
            d.append(("ruim", f"Gancho segurou menos: {g:.0%} ainda assistindo aos 30 s (média {mg:.0%})."))
        elif g > mg + 0.05:
            d.append(("bom", f"Gancho forte: {g:.0%} ainda assistindo aos 30 s (média {mg:.0%})."))
    p, mp = j.get("averageViewPercentage"), mj.get("averageViewPercentage")
    if p is not None and mp:
        if p < mp * 0.85:
            d.append(("ruim", f"Assistiram menos do vídeo: {p:.0f}% em média (média do canal {mp:.0f}%)."))
        elif p > mp * 1.15:
            d.append(("bom", f"Prendeu até o fim: {p:.0f}% assistido em média (média do canal {mp:.0f}%)."))
    x = j.get("x_mediana")
    if x is not None:
        d.insert(0, ("bom" if x >= 1 else "ruim", f"{_br(x)}x as views que um vídeo do Léo costuma ter no mesmo período."))
    return [{"tipo": t, "texto": s} for t, s in d]


def atualizar(g=None, cfg=None):
    """Mede todos os vídeos do relatório e grava meus-videos.json. Devolve o relatório."""
    import meu_canal
    cfg = cfg or R.config()
    if g is None:
        from yt_common import Google
        g = Google()
    ratings = R.ratings_atuais()
    ideia_de = postadas(ratings)
    videos, own = lista_videos(g, cfg, ratings)
    cache_f = out_dir() / "cache" / "meus-videos.json"
    cache = read_json(cache_f, {}) or {}
    hoje = now().date()
    # janelas de todos os longos (inclusive antigos) para as medianas de comparação
    medidas = {}
    longos = [v for v in own.values() if v.get("formato") != "short" and v.get("published_at")]
    for v in longos:
        pub = parse_date(v["published_at"])
        medidas[v["video_id"]] = {d: janela(g, v["video_id"], pub, d, cache, hoje) for d in JANELAS}
    rel_videos, curvas = [], {}
    for v in longos:
        curvas[v["video_id"]] = curva(g, v["video_id"], parse_date(v["published_at"]), hoje, cache)
    analises = R.load_analyses()
    import roteiro as RT
    for v in videos:
        vid, pub = v["video_id"], parse_date(v["published_at"])
        r = {"video_id": vid, "titulo": v.get("title"), "publicado": v["published_at"][:10],
             "thumb": v.get("thumbnail"), "duracao_s": v.get("duration_s"), "idade_dias": (hoje - pub.date()).days,
             "ideia_id": ideia_de.get(vid)}
        for d in JANELAS:
            st, m = medidas[vid][d]
            if not m:
                continue
            outros = [medidas[o][d][1] for o in medidas if o != vid and medidas[o][d][0] == "ok"]
            med_views = median([x.get("views") for x in outros])
            r[f"d{d}"] = {**{k: m.get(k) for k in ("views", "estimatedMinutesWatched", "averageViewDuration",
                                                    "averageViewPercentage", "subscribersGained", "impressions", "ctr")},
                          "parcial": st == "parcial", "mediana_views": med_views,
                          "x_mediana": round(m["views"] / med_views, 2) if med_views and m.get("views") else None}
        c = curvas.get(vid) or []
        r["retencao"] = meu_canal.retention_summary(c, v.get("duration_s"), False)
        r["curva"] = [[p[0], p[1]] for p in c]
        rot = read_json(RT.pasta() / f"{r['ideia_id']}.json", None) if r["ideia_id"] else None
        r["tem_roteiro"] = bool(rot)
        r["retencao"]["quedas"] = secoes_nas_quedas(rot, r["retencao"].get("quedas") or [])
        if r["ideia_id"]:
            ref = r["ideia_id"].rsplit("-", 1)[0]
            r["ideia_titulo"] = next((i["titulo"] for i in (analises.get(ref) or {}).get("ideias") or []
                                      if i["id"] == r["ideia_id"]), None)
        rel_videos.append(r)
    outros_curvas = [curvas[o] for o in curvas]
    med = {f"d{d}": {k: median([medidas[o][d][1].get(k) for o in medidas if medidas[o][d][0] == "ok"])
                     for k in ("views", "averageViewPercentage", "ctr")} for d in JANELAS}
    med["ret_gancho"] = median([meu_canal.retention_summary(curvas[o], own[o].get("duration_s"), False).get("ret_gancho")
                                for o in curvas if curvas[o]])
    for r in rel_videos:
        r["diagnostico"] = diagnostico(r, med)
    rel = {"atualizado_em": now().isoformat(), "medianas": med, "curva_media": curva_media(outros_curvas),
           "videos": rel_videos}
    write_json(cache_f, cache)
    write_json(arquivo(), rel)
    log(f"Relatório dos vídeos: {len(rel_videos)} vídeo(s) → {arquivo()}")
    return rel


# ---------------------------------------------------------------- lição (Claude)

SCHEMA_LICAO = {"type": "object", "properties": {
    "resumo": {"type": "string"},
    "funcionou": {"type": "array", "items": {"type": "string"}},
    "mudar": {"type": "array", "items": {"type": "string"}},
    "regra": {"type": "string"}},
    "required": ["resumo", "funcionou", "mudar", "regra"], "additionalProperties": False}


def _trechos(vid, titulo, quedas, dur):
    """O que o Léo falava em cada queda (±20 s), pela legenda do próprio vídeo."""
    import transcricao
    try:
        p = transcricao.fetch(vid, titulo)
    except Exception:
        return []
    if not p:
        return []
    cues = transcricao.read_cues(p)
    out = []
    for q in quedas:
        t = q["pos"] * (dur or 0)
        txt = " ".join(x for s, x in cues if t - 20 <= s <= t + 20)
        if txt:
            out.append(f"[{fmt_time(t)}] {txt}")
    abertura = transcricao.first_seconds(cues, 30)
    return ([f"[0:00–0:30] {abertura}"] if abertura else []) + out


def licao(video_id, client=None, cfg=None):
    import gerador
    cfg = cfg or R.config()
    rel = read_json(arquivo(), {}) or {}
    r = next((v for v in rel.get("videos") or [] if v["video_id"] == video_id), None)
    if not r:
        raise ValueError("Vídeo fora do relatório. Clique em “Atualizar números” primeiro.")
    quedas = (r.get("retencao") or {}).get("quedas") or []
    numeros = {k: r.get(k) for k in ("titulo", "publicado", "idade_dias", "duracao_s", "d2", "d7", "d30")}
    numeros["retencao"] = {k: v for k, v in (r.get("retencao") or {}).items() if k != "quedas"}
    linhas = [
        "## Vídeo do Léo já publicado", json.dumps(numeros, ensure_ascii=False, indent=1),
        "## Média do canal do Léo (mesmas janelas)", json.dumps(rel.get("medianas"), ensure_ascii=False),
        "## Leitura automática", *[f"- {d['texto']}" for d in r.get("diagnostico") or []],
        "## Onde o público saiu", *[f"- {q['em']}: caiu {q['queda']:.0%}" + (f" (seção “{q['secao']}”)" if q.get("secao") else "")
                                     for q in quedas],
        "## O que o Léo falava nesses pontos", *_trechos(video_id, r.get("titulo"), quedas, r.get("duracao_s")),
        "## Tarefa",
        "Explique para o Léo, em português simples, o que esse vídeo ensina. resumo: 2 frases com o veredito "
        "(foi bem ou não, e por quê: clique, gancho ou meio do vídeo). funcionou e mudar: 1–3 itens concretos cada, "
        "citando o momento do vídeo. regra: UMA regra prática e específica para os próximos roteiros/títulos "
        "(ex.: 'mostrar a planilha antes de 0:15'). Não invente números que não estão acima.",
    ]
    out, _ = gerador._chamar(client, cfg, SCHEMA_LICAO, "\n".join(linhas), max_tokens=8000)
    licoes = read_json(arquivo_licoes(), {}) or {}
    licoes[video_id] = {**out, "titulo": r.get("titulo"), "gerado_em": now().isoformat(),
                        "idade_dias": r.get("idade_dias")}
    write_json(arquivo_licoes(), licoes)
    log(f"Lição do vídeo {video_id}: {out['regra']}")
    return licoes[video_id]


def regras_aprendidas(n=8):
    """Regras das lições mais recentes, para o contexto das próximas ideias e roteiros."""
    licoes = read_json(arquivo_licoes(), {}) or {}
    ult = sorted(licoes.values(), key=lambda x: x.get("gerado_em", ""), reverse=True)[:n]
    return [f"{x['regra']} (vídeo “{x.get('titulo')}”)" for x in ult if x.get("regra")]


def main():
    import sys
    from yt_common import load_env
    load_env()
    rel = atualizar()
    if "--licoes" in sys.argv:
        import gerador
        ok, motivo = gerador.disponivel()
        licoes = read_json(arquivo_licoes(), {}) or {}
        for r in rel["videos"]:
            # lição quando a janela de 7 dias fecha, e de novo aos 30
            fase = 30 if r.get("d30") and not r["d30"]["parcial"] else 7 if r.get("d7") and not r["d7"]["parcial"] else 0
            if ok and fase and (licoes.get(r["video_id"]) or {}).get("idade_dias", 0) < fase:
                try:
                    licao(r["video_id"])
                except Exception as e:  # nunca derruba a rodada
                    log(f"  lição {r['video_id']}: {e}")
        if not ok:
            log(f"Lições pelo Claude desligadas: {motivo}")


if __name__ == "__main__":
    main()
