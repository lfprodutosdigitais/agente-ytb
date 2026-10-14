"""FASE 4 — Aprendizado: avaliações (👍/👎) + resultado dos vídeos postados -> pesos e aprendizados.md.

Uso: python3 scripts/aprendizado.py
1. Lê ./referencias/avaliacoes.json (importa o mais novo de ~/Downloads, se houver).
2. Para ideias com status "postado" e URL: puxa do YouTube Analytics as métricas do vídeo do Léo aos 7 e 30 dias
   e compara com a mediana dos outros vídeos dele na mesma janela e com a retenção no gancho.
3. Calcula pesos por canal de referência e por tema (👍 e acertos sobem; 👎 e fracassos descem) em
   ./referencias/pesos.json — usados para ordenar candidatos e o painel.
4. Gera ./referencias/aprendizados.md (parte numérica + blocos <!-- CLAUDE:... --> preservados entre rodadas).
"""
import re
from datetime import timedelta

from yt_common import log, median, now, out_dir, parse_date, read_json, write_json

import referencias as R

JANELAS = (7, 30)
ACERTO = 1.0  # vídeo postado com views >= mediana do Léo na mesma janela conta como acerto
PADROES = [  # padrões de título/gancho para medir o que é aprovado
    ("número em R$", re.compile(r"R\$\s?\d")),
    ("conta/simulação", re.compile(r"\b(conta|simula|fiz a conta|calcul)", re.I)),
    ("teste/desafio feito na tela", re.compile(r"\b(testei|criei|tentei|vou criar|ao vivo|em \d+ (dias|horas))", re.I)),
    ("lista numerada", re.compile(r"^\W*\d+\s|\b\d+ (leis|sinais|rendas|erros|passos|produtos|neg[oó]cios)\b", re.I)),
    ("história do Léo (motoboy/R$ 3 mil/R$ 35 mil)", re.compile(r"motoboy|R\$\s?3 MIL|R\$\s?35 MIL|se eu ganhasse|se fosse CLT", re.I)),
    ("CNPJ rico / PF x PJ", re.compile(r"CNPJ", re.I)),
    ("golpe/guru", re.compile(r"golpe|guru|pir[aâ]mide", re.I)),
]


def load_ratings():
    from painel import import_downloaded_ratings
    o = out_dir()
    av = read_json(import_downloaded_ratings(o), {}) or {}
    return av.get("ideias", av)


def ideas_index():
    """id da ideia -> (ideia, análise, id do vídeo de referência, vídeo de referência)."""
    videos = read_json(R.paths()["videos"], {}) or {}
    idx = {}
    for vid, a in R.load_analyses().items():
        for i in a.get("ideias") or []:
            idx[i["id"]] = (i, a, vid, videos.get(vid, {}))
    return idx


def _video_id_from_url(url):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def own_window_metrics(g, video_id, published, days, cache):
    """Métricas do vídeo do Léo nos primeiros `days` dias (cacheado quando a janela já fechou)."""
    import meu_canal
    key = f"{video_id}:{days}"
    if key in cache:
        return cache[key]
    end = published + timedelta(days=days - 1)
    if end.date() >= now().date():
        return None  # janela ainda aberta
    m = meu_canal.metrics_window(g, video_id, published.date().isoformat(), end.date().isoformat(), with_impressions=False)
    curve = meu_canal.retention_curve(g, video_id, published.date().isoformat(), end.date().isoformat())
    m["retencao"] = meu_canal.retention_summary(curve, cache.get(f"{video_id}:dur") or 600, False)
    cache[key] = m
    return m


def posted_results(ratings, g=None):
    """Para cada ideia postada: métricas aos 7/30 dias vs mediana do Léo na mesma janela."""
    posted = {iid: a for iid, a in ratings.items() if a.get("status") == "postado" and _video_id_from_url(a.get("url"))}
    if not posted:
        return []
    o = out_dir()
    dados = read_json(o / "meu-canal-dados.json", {}) or {}
    own = {v["video_id"]: v for v in dados.get("videos", [])}
    if g is None:
        from yt_common import Google
        g = Google()
    cache_f = o / "cache" / "postados.json"
    cache = read_json(cache_f, {}) or {}
    for v in own.values():
        cache[f"{v['video_id']}:dur"] = v.get("duration_s")
    results = []
    for iid, a in posted.items():
        vid = _video_id_from_url(a["url"])
        v = own.get(vid)
        if not v:
            log(f"  {iid}: vídeo {vid} não está no meu-canal-dados.json (rode meu_canal.py)")
            continue
        pub = parse_date(v["published_at"])
        r = {"ideia": iid, "video_id": vid, "titulo": v["title"], "publicado": v["published_at"][:10]}
        for d in JANELAS:
            m = own_window_metrics(g, vid, pub, d, cache)
            if not m:
                continue
            # mediana dos OUTROS vídeos do Léo na mesma janela (mesmo formato)
            others = []
            for o_id, ov in own.items():
                if o_id == vid or ov.get("formato") != v.get("formato"):
                    continue
                om = own_window_metrics(g, o_id, parse_date(ov["published_at"]), d, cache)
                if om:
                    others.append(om)
            med_views = median([x.get("views") for x in others])
            med_hook = median([(x.get("retencao") or {}).get("ret_gancho") for x in others])
            r[f"d{d}"] = {
                "views": m.get("views"), "mediana_views": med_views,
                "x_mediana": round(m["views"] / med_views, 2) if med_views and m.get("views") is not None else None,
                "pct_assistida": m.get("averageViewPercentage"), "inscritos": m.get("subscribersGained"),
                "ret_gancho": (m.get("retencao") or {}).get("ret_gancho"), "mediana_ret_gancho": med_hook,
            }
        results.append(r)
    write_json(cache_f, cache)
    return results


def outcome(r):
    """+1 acerto, -1 abaixo, 0 sem dado — usa a janela mais longa disponível."""
    for d in reversed(JANELAS):
        x = (r.get(f"d{d}") or {}).get("x_mediana")
        if x is not None:
            return 1 if x >= ACERTO else -1
    return 0


def compute_weights(ratings, idx, results):
    """Peso = 0,5 + (positivos + 1) / (positivos + negativos + 2): taxa de aprovação suavizada, entre 0,5 e 1,5.
    Começa em 1,0 (neutro) e só se afasta com evidência. Acerto/erro de vídeo postado vale 2 votos."""
    tally = {"canal": {}, "tema": {}}
    def add(kind, key, pos, neg):
        if not key:
            return
        t = tally[kind].setdefault(key, {"up": 0, "down": 0, "acertos": 0, "erros": 0})
        t["up"] += pos
        t["down"] += neg
    for iid, a in ratings.items():
        i, an, vid, v = idx.get(iid, (None, {}, None, {}))
        canal = a.get("canal") or v.get("canal")
        tema = a.get("tema") or an.get("tema")
        if a.get("voto") in ("up", "down"):
            up = a["voto"] == "up"
            add("canal", canal, int(up), int(not up))
            add("tema", tema, int(up), int(not up))
    for r in results:
        a = ratings.get(r["ideia"], {})
        i, an, vid, v = idx.get(r["ideia"], (None, {}, None, {}))
        res = outcome(r)
        for kind, key in (("canal", a.get("canal") or v.get("canal")), ("tema", a.get("tema") or an.get("tema"))):
            if key and res:
                t = tally[kind].setdefault(key, {"up": 0, "down": 0, "acertos": 0, "erros": 0})
                t["acertos" if res > 0 else "erros"] += 1
    pesos = {}
    for kind, d in tally.items():
        pesos[kind] = {}
        for key, t in d.items():
            pos, neg = t["up"] + 2 * t["acertos"], t["down"] + 2 * t["erros"]
            pesos[kind][key] = {**t, "peso": round(0.5 + (pos + 1) / (pos + neg + 2), 2)}
    return pesos


def pattern_stats(ratings, idx):
    out = []
    for nome, rx in PADROES:
        up = down = 0
        for iid, a in ratings.items():
            if a.get("voto") not in ("up", "down"):
                continue
            i = idx.get(iid, ({},))[0] or {}
            text = f"{a.get('titulo') or i.get('titulo', '')} {i.get('gancho', '')}"
            if rx.search(text):
                up += a["voto"] == "up"
                down += a["voto"] == "down"
        if up or down:
            out.append({"padrao": nome, "up": up, "down": down})
    return sorted(out, key=lambda p: -(p["up"] - p["down"]))


def render(ratings, idx, results, pesos, padroes, prev):
    up = sum(1 for a in ratings.values() if a.get("voto") == "up")
    down = sum(1 for a in ratings.values() if a.get("voto") == "down")
    st = {}
    for a in ratings.values():
        st[a.get("status", "ideia")] = st.get(a.get("status", "ideia"), 0) + 1
    L = ["# Aprendizados", "",
         f"_Atualizado em {now():%Y-%m-%d}. {len(ratings)} ideias avaliadas: {up} 👍 · {down} 👎. Status: "
         + ", ".join(f"{k} {v}" for k, v in sorted(st.items())) + "._", "",
         "Peso = 0,5 + (👍 + 2×acertos + 1) ÷ (todos os votos + 2): começa em 1,0 e vai de 0,5 a 1,5 conforme a evidência. "
         "Acerto = vídeo postado com views ≥ "
         "mediana do Léo na mesma janela (7/30 dias). O peso multiplica a prioridade no painel e nos candidatos.", ""]
    for kind, nome in (("canal", "Por canal de referência"), ("tema", "Por tema")):
        L += [f"## {nome}", "", "| | 👍 | 👎 | Acertos | Erros | Peso |", "|---|---|---|---|---|---|"]
        for key, t in sorted(pesos.get(kind, {}).items(), key=lambda kv: -kv[1]["peso"]):
            L.append(f"| {key} | {t['up']} | {t['down']} | {t['acertos']} | {t['erros']} | {t['peso']} |")
        L.append("")
    if padroes:
        L += ["## Padrões de título e gancho", "", "| Padrão | 👍 | 👎 |", "|---|---|---|"]
        L += [f"| {p['padrao']} | {p['up']} | {p['down']} |" for p in padroes]
        L.append("")
    L += ["## Vídeos postados a partir de ideias", ""]
    if results:
        L += ["| Ideia | Vídeo | 7 dias (x mediana) | 30 dias (x mediana) | Ret. gancho vs mediana | Resultado |",
              "|---|---|---|---|---|---|"]
        for r in results:
            d7, d30 = r.get("d7") or {}, r.get("d30") or {}
            rg = d30 or d7
            ret = (f"{rg['ret_gancho'] * 100:.0f}% vs {rg['mediana_ret_gancho'] * 100:.0f}%"
                   if rg.get("ret_gancho") is not None and rg.get("mediana_ret_gancho") else "—")
            res = {1: "✅ acerto", -1: "❌ abaixo", 0: "⏳ aguardando"}[outcome(r)]
            L.append(f"| `{r['ideia']}` | [{r['titulo'][:50]}](https://youtu.be/{r['video_id']}) | "
                     f"{d7.get('x_mediana', '—')} | {d30.get('x_mediana', '—')} | {ret} | {res} |")
    else:
        L.append("_Nenhuma ideia marcada como postado com URL ainda. No painel: status → postado e cole a URL._")
    L.append("")
    motivos = [(iid, a) for iid, a in ratings.items() if a.get("motivo")]
    if motivos:
        L += ["## Motivos escritos", ""]
        L += [f"- {'👍' if a.get('voto') == 'up' else '👎'} `{iid}` {a.get('titulo', '')}: _{a['motivo']}_" for iid, a in motivos]
        L.append("")
    L += ["## O que funcionou e o que não", ""] + R_block("analise", "Claude: resumir o que os 👍/👎, motivos e vídeos "
          "postados dizem — por tema, padrão de gancho, padrão de título e canal de referência. Regras concretas para as "
          "próximas ideias (o que repetir, o que evitar).", prev)
    return "\n".join(L) + "\n"


def R_block(key, instr, prev):
    import meu_canal
    return meu_canal.block(key, instr, prev)


def main(g=None):
    import meu_canal
    o = out_dir()
    ratings = load_ratings()
    if not ratings:
        log("Sem avaliações ainda: no painel, dê 👍/👎 e clique em 'Exportar avaliações'.")
    idx = ideas_index()
    results = posted_results(ratings, g)
    pesos = compute_weights(ratings, idx, results)
    padroes = pattern_stats(ratings, idx)
    write_json(o / "pesos.json", {"gerado_em": now().isoformat(), **pesos})
    md = o / "aprendizados.md"
    prev = meu_canal.previous_blocks(md.read_text(encoding="utf-8")) if md.exists() else {}
    md.write_text(render(ratings, idx, results, pesos, padroes, prev), encoding="utf-8")
    log(f"OK: {md} — {len(ratings)} avaliações, {len(results)} vídeos postados acompanhados")
    for kind in ("canal", "tema"):
        top = sorted(pesos.get(kind, {}).items(), key=lambda kv: -kv[1]["peso"])
        if top:
            log(f"  pesos por {kind}: " + ", ".join(f"{k} {t['peso']}" for k, t in top))


if __name__ == "__main__":
    main()
