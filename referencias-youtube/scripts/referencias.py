"""FASE 2 — Referências: coleta (Apify), histórico, outliers por vizinhança, em alta, engajamento,
transcrições e temas validados. A parte de julgamento (relevância, análise, ideias) é feita pelo Claude,
que grava ./referencias/analises/{video_id}.json — ver SKILL.md.

Uso:
  python3 scripts/referencias.py teste @primorico   # coleta 1 canal e mostra custo real + estimativa p/ todos
  python3 scripts/referencias.py coletar            # todos os canais de canais.txt, depois calcula
  python3 scripts/referencias.py calcular           # só recalcula scores com os dados já coletados
  python3 scripts/referencias.py transcrever        # baixa legendas dos candidatos com relevância >= 3
  python3 scripts/referencias.py temas              # recalcula temas validados a partir das análises
"""
import argparse
import re
import time
import urllib.parse
from datetime import timedelta

from yt_common import (SKILL_DIR, HttpError, http, load_config, load_env, log, median, now, out_dir,
                       parse_date, read_json, write_json)

APIFY = "https://api.apify.com/v2"
DEFAULTS = {
    "apify_actor": "streamers~youtube-scraper",
    "apify_input_extra": {},
    "max_longos": 50,
    "max_shorts": 0,
    "short_max_segundos": 180,
    "vizinhos": 10,
    "corte_longo": 3.0,
    "corte_short": 5.0,
    "idade_minima_dias": 7,
    "em_alta_fator": 3.0,
    "em_alta_max_dias": 30,
    "engajamento_peso": 0.25,
    "engajamento_bonus_max": 0.5,
    "relevancia_minima": 3,
    "tema_validado_canais": 2,
    "tema_validado_dias": 90,
}


def config():
    return {**DEFAULTS, **load_config()}


# ---------------------------------------------------------------- canais

def read_channels(path=None):
    """canais.txt: uma URL por linha; '| en' marca canal em inglês; '#' comenta."""
    path = path or SKILL_DIR / "canais.txt"
    chans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        lang = "en" if len(parts) > 1 and parts[1].lower().startswith("en") else "pt"
        handle = url.rstrip("/").split("/")[-1]
        chans.append({"url": url, "handle": handle, "lang": lang})
    return chans


def match_channel(arg, chans):
    a = arg.lower().lstrip("@")
    for c in chans:
        if c["handle"].lower().lstrip("@") == a or c["url"].lower() == arg.lower():
            return c
    return {"url": arg if arg.startswith("http") else f"https://www.youtube.com/@{a}",
            "handle": "@" + a, "lang": "pt"}


# ---------------------------------------------------------------- Apify

def apify_run(token, actor, run_input, timeout_s=1800):
    """Roda o actor, espera terminar e devolve (run, items)."""
    try:
        r = http("POST", f"{APIFY}/acts/{actor}/runs", params={"token": token, "waitForFinish": 60}, data=run_input)
    except HttpError as e:
        if e.status == 401:
            raise SystemExit("Token do Apify inválido. Copie de novo em https://console.apify.com/settings/integrations "
                             "e cole na linha APIFY_TOKEN= do arquivo ~/.claude/skills/referencias-youtube/.env "
                             "(sem espaços nem aspas).")
        if e.status == 402:
            raise SystemExit("Apify sem crédito/plano para rodar o actor. Veja https://console.apify.com/billing")
        raise
    run = r["data"]
    t0 = time.time()
    while run["status"] in ("READY", "RUNNING"):
        if time.time() - t0 > timeout_s:
            raise SystemExit(f"Apify demorou demais (run {run['id']}). Veja https://console.apify.com/actors/runs/{run['id']}")
        run = http("GET", f"{APIFY}/actor-runs/{run['id']}", params={"token": token, "waitForFinish": 60})["data"]
    if run["status"] != "SUCCEEDED":
        raise SystemExit(f"Apify terminou com status {run['status']}: https://console.apify.com/actors/runs/{run['id']}")
    items = http("GET", f"{APIFY}/datasets/{run['defaultDatasetId']}/items",
                 params={"token": token, "clean": "true", "format": "json"})
    # o custo final às vezes só consolida segundos depois do fim
    if not run.get("usageTotalUsd"):
        time.sleep(3)
        run = http("GET", f"{APIFY}/actor-runs/{run['id']}", params={"token": token})["data"]
    return run, items


def channel_input(chan, cfg):
    url = urllib.parse.quote(chan["url"], safe=":/@?=&")
    return {"startUrls": [{"url": url}], "maxResults": cfg["max_longos"], "maxResultsShorts": cfg["max_shorts"],
            "maxResultStreams": 0, **cfg["apify_input_extra"]}


def _first(d, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _int(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).strip().lower().replace(",", "").replace(" ", "")
    m = re.match(r"([\d.]+)([kmb])?", s)
    if not m:
        return None
    n = float(m.group(1))
    return int(n * {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2), 1))


def _duration(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    parts = [int(p) for p in str(x).split(":") if p.isdigit()]
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs if parts else None


def _video_id(item):
    vid = _first(item, "id", "videoId")
    if vid:
        return vid
    url = item.get("url") or ""
    m = re.search(r"(?:v=|/shorts/|youtu\.be/)([\w-]{11})", url)
    return m.group(1) if m else None


def normalize(item, chan, cfg):
    """Item do Apify -> registro padrão. Tolerante a variações de nome de campo do actor."""
    vid = _video_id(item)
    if not vid:
        return None
    url = item.get("url") or f"https://www.youtube.com/watch?v={vid}"
    dur = _duration(_first(item, "duration", "lengthSeconds", "durationSeconds"))
    typ = str(_first(item, "type", "videoType") or "").lower()
    is_short = "short" in typ or "/shorts/" in url or (not typ and dur is not None and dur <= cfg["short_max_segundos"])
    return {
        "video_id": vid,
        "url": url,
        "titulo": item.get("title"),
        "canal": _first(item, "channelName", "channelTitle") or chan["handle"],
        "canal_handle": chan["handle"],
        "canal_url": chan["url"],
        "idioma": chan["lang"],
        "formato": "short" if is_short else "longo",
        "views": _int(_first(item, "viewCount", "views")),
        "likes": _int(_first(item, "likes", "likeCount")),
        "comentarios": _int(_first(item, "commentsCount", "commentCount", "comments")),
        "data": _first(item, "date", "uploadDate", "publishedAt"),
        "duracao_s": dur,
        "thumbnail": _first(item, "thumbnailUrl", "thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "inscritos_canal": _int(_first(item, "numberOfSubscribers", "channelSubscribers", "subscriberCount")),
        "descricao": (item.get("text") or item.get("description") or "")[:1000],
    }


# ---------------------------------------------------------------- armazenamento

def paths():
    o = out_dir()
    return {"videos": o / "videos.json", "historico": o / "historico.json", "candidatos": o / "candidatos.json",
            "analises": o / "analises", "temas": o / "temas-validados.json", "custos": o / "custos-apify.json",
            "out": o}


def store(records, today=None):
    """Atualiza videos.json e grava snapshot de views do dia em historico.json."""
    p = paths()
    today = (today or now()).date().isoformat()
    videos = read_json(p["videos"], {}) or {}
    hist = read_json(p["historico"], {}) or {}
    for r in records:
        prev = videos.get(r["video_id"], {})
        videos[r["video_id"]] = {**prev, **{k: v for k, v in r.items() if v is not None}, "atualizado_em": today}
        if r.get("views") is not None:
            snaps = [s for s in hist.get(r["video_id"], []) if s["data"] != today]
            snaps.append({"data": today, "views": r["views"]})
            hist[r["video_id"]] = sorted(snaps, key=lambda s: s["data"])
    write_json(p["videos"], videos)
    write_json(p["historico"], hist)
    return videos, hist


def collect(chans, cfg, token):
    p = paths()
    custos = read_json(p["custos"], []) or []
    all_records, total_usd = [], 0.0
    for c in chans:
        log(f"Apify: {c['handle']} (até {cfg['max_longos']} longos"
            f"{' + %d Shorts' % cfg['max_shorts'] if cfg['max_shorts'] else ', sem Shorts'})...")
        run, items = apify_run(token, cfg["apify_actor"], channel_input(c, cfg))
        recs = [r for r in (normalize(i, c, cfg) for i in items) if r]
        if not cfg["max_shorts"]:  # Shorts desligados: descarta se o actor devolver mesmo assim
            recs = [r for r in recs if r["formato"] != "short"]
        usd = run.get("usageTotalUsd")
        total_usd += usd or 0
        custos.append({"data": now().isoformat(), "canal": c["handle"], "itens": len(recs), "usd": usd, "run": run["id"]})
        n_short = sum(r["formato"] == "short" for r in recs)
        log(f"  {len(recs)} vídeos ({len(recs) - n_short} longos, {n_short} Shorts) — custo: "
            f"{'US$ %.4f' % usd if usd is not None else 'n/d'}")
        if items and not recs:
            log(f"  ATENÇÃO: o actor devolveu {len(items)} itens mas nenhum foi reconhecido. Campos: {sorted(items[0])}")
        all_records += recs
    write_json(p["custos"], custos)
    store(all_records)
    return all_records, total_usd


# ---------------------------------------------------------------- métricas

def compute(videos, hist, cfg, today=None):
    """Outlier por vizinhança, engajamento, velocidade/em alta. Função pura (testável)."""
    today = today or now()
    by_group, by_chan = {}, {}
    for v in videos.values():
        if v.get("views") is None or not v.get("data"):
            continue
        v["_dt"] = parse_date(v["data"])
        by_group.setdefault((v["canal_handle"], v["formato"]), []).append(v)
        by_chan.setdefault(v["canal_handle"], []).append(v)

    # engajamento médio por canal
    eng_avg = {}
    for ch, vs in by_chan.items():
        ratios = [v["comentarios"] / v["views"] for v in vs if v.get("comentarios") is not None and v["views"]]
        eng_avg[ch] = sum(ratios) / len(ratios) if ratios else None

    # velocidade (views/dia entre os 2 últimos snapshots de datas diferentes)
    for v in videos.values():
        snaps = hist.get(v["video_id"], [])
        v["velocidade"] = None
        if len(snaps) >= 2:
            a, b = snaps[-2], snaps[-1]
            days = (parse_date(b["data"]) - parse_date(a["data"])).days
            if days > 0:
                v["velocidade"] = round((b["views"] - a["views"]) / days, 1)
    vel_avg = {}
    for ch, vs in by_chan.items():
        vels = [v["velocidade"] for v in vs if v.get("velocidade") is not None]
        vel_avg[ch] = sum(vels) / len(vels) if vels else None

    k = cfg["vizinhos"]
    for (ch, fmt), vs in by_group.items():
        corte = cfg["corte_short"] if fmt == "short" else cfg["corte_longo"]
        for v in vs:
            peers = sorted((o for o in vs if o is not v), key=lambda o: abs((o["_dt"] - v["_dt"]).total_seconds()))[:k]
            med = median([o["views"] for o in peers])
            v["idade_dias"] = (today - v["_dt"]).days
            v["mediana_vizinhos"] = med
            v["outlier_score"] = round(v["views"] / med, 2) if med else None
            v["jovem"] = v["idade_dias"] < cfg["idade_minima_dias"]
            v["outlier"] = bool(v["outlier_score"] and not v["jovem"] and v["outlier_score"] >= corte)
            # engajamento soma no score
            ea = eng_avg.get(ch)
            if ea and v.get("comentarios") is not None and v["views"]:
                v["engajamento_x"] = round((v["comentarios"] / v["views"]) / ea, 2)
            else:
                v["engajamento_x"] = None
            bonus = 0.0
            if v["engajamento_x"] and v["engajamento_x"] > 1:
                bonus = min((v["engajamento_x"] - 1) * cfg["engajamento_peso"], cfg["engajamento_bonus_max"])
            v["score"] = round((v["outlier_score"] or 0) * (1 + bonus), 2)
            # em alta
            va = vel_avg.get(ch)
            v["velocidade_x"] = round(v["velocidade"] / va, 2) if va and v.get("velocidade") is not None else None
            v["em_alta"] = bool(v["velocidade_x"] and v["idade_dias"] <= cfg["em_alta_max_dias"]
                                and v["velocidade_x"] >= cfg["em_alta_fator"])
    for v in videos.values():
        v.pop("_dt", None)
    return videos


def load_analyses():
    d = paths()["analises"]
    out = {}
    if d.exists():
        for f in d.glob("*.json"):
            a = read_json(f, None)
            if isinstance(a, dict):
                out[f.stem] = a
    return out


def candidates(videos, analyses, cfg):
    cands = [v for v in videos.values() if (v.get("outlier") or v.get("em_alta"))
             and (cfg["max_shorts"] or v.get("formato") != "short")]
    cands.sort(key=lambda v: -(v.get("score") or 0))
    pend_rel, pend_anal = [], []
    for v in cands:
        a = analyses.get(v["video_id"], {})
        v["relevancia"] = a.get("relevancia")
        v["analisado"] = bool(a.get("ideias"))
        if v["relevancia"] is None:
            pend_rel.append(v["video_id"])
        elif v["relevancia"] >= cfg["relevancia_minima"] and not v["analisado"]:
            pend_anal.append(v["video_id"])
    return cands, pend_rel, pend_anal


def validated_themes(videos, analyses, cfg, today=None):
    """Mesmo tema outlier em 2+ canais nos últimos N dias."""
    today = today or now()
    lim = today - timedelta(days=cfg["tema_validado_dias"])
    temas = {}
    for vid, a in analyses.items():
        v = videos.get(vid)
        tema = a.get("tema")
        if not v or not tema or not (v.get("outlier") or v.get("em_alta")):
            continue
        if not v.get("data") or parse_date(v["data"]) < lim:
            continue
        t = temas.setdefault(tema, {"tema": tema, "canais": set(), "videos": []})
        t["canais"].add(v["canal_handle"])
        t["videos"].append(vid)
    out = [{"tema": t["tema"], "canais": sorted(t["canais"]), "videos": t["videos"]}
           for t in temas.values() if len(t["canais"]) >= cfg["tema_validado_canais"]]
    return sorted(out, key=lambda t: -len(t["canais"]))


def recalc(cfg, quiet=False):
    p = paths()
    videos = read_json(p["videos"], {}) or {}
    hist = read_json(p["historico"], {}) or {}
    compute(videos, hist, cfg)
    write_json(p["videos"], videos)
    analyses = load_analyses()
    cands, pend_rel, pend_anal = candidates(videos, analyses, cfg)
    temas = validated_themes(videos, analyses, cfg)
    write_json(p["temas"], temas)
    write_json(p["candidatos"], {
        "gerado_em": now().isoformat(),
        "cortes": {"longo": cfg["corte_longo"], "short": cfg["corte_short"]},
        "pendentes_relevancia": pend_rel,
        "pendentes_analise": pend_anal,
        "temas_validados": temas,
        "candidatos": [{k: v.get(k) for k in (
            "video_id", "url", "titulo", "canal", "canal_handle", "idioma", "formato", "data", "idade_dias", "views",
            "comentarios", "duracao_s", "thumbnail", "mediana_vizinhos", "outlier_score", "engajamento_x", "score",
            "outlier", "velocidade", "velocidade_x", "em_alta", "relevancia", "analisado")} for v in cands],
    })
    if not quiet:
        n_out = sum(1 for v in cands if v.get("outlier"))
        n_alta = sum(1 for v in cands if v.get("em_alta"))
        log(f"{len(videos)} vídeos na base · {n_out} outliers · {n_alta} em alta · "
            f"{len(pend_rel)} aguardando nota de relevância · {len(pend_anal)} aguardando análise · "
            f"{len(temas)} temas validados")
        for v in cands[:10]:
            tag = "EM ALTA " if v.get("em_alta") else ""
            log(f"  {tag}{v['score']:>5}x  [{v['formato']}] {v['canal']}: {(v.get('titulo') or '')[:70]}")
    return cands


def transcribe(cfg):
    import transcricao
    videos = read_json(paths()["videos"], {}) or {}
    analyses = load_analyses()
    todo = [vid for vid, a in analyses.items()
            if (a.get("relevancia") or 0) >= cfg["relevancia_minima"] and vid in videos]
    for vid in todo:
        try:
            p = transcricao.fetch(vid, videos[vid].get("titulo"))
            log(f"  {vid}: {p or 'sem legenda pt/en'}")
        except Exception as e:
            log(f"  {vid}: falhou ({e})")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", choices=["teste", "coletar", "calcular", "transcrever", "temas"])
    ap.add_argument("canal", nargs="?")
    args = ap.parse_args()
    cfg = config()
    load_env()
    import os
    token = os.environ.get("APIFY_TOKEN")
    chans = read_channels()

    if args.acao in ("teste", "coletar"):
        if not token:
            raise SystemExit("Falta APIFY_TOKEN no .env da skill (~/.claude/skills/referencias-youtube/.env).")
        if args.acao == "teste":
            c = match_channel(args.canal or "@primorico", chans)
            recs, usd = collect([c], cfg, token)
            n = len(chans)
            log("")
            log(f"Teste com {c['handle']}: {len(recs)} vídeos coletados, custo real US$ {usd:.4f}.")
            if recs:
                log(f"Estimativa para os {n} canais de canais.txt: ~US$ {usd * n:.2f} por rodada "
                    f"(~US$ {usd * n * 4:.2f}/mês rodando 1x por semana).")
                log("Exemplos:")
                for r in recs[:3]:
                    log(f"  [{r['formato']}] {r['views']} views · {r['comentarios']} coment. · {r['data']} · {r['titulo']}")
        else:
            recs, usd = collect(chans, cfg, token)
            log(f"Rodada: {len(recs)} vídeos de {len(chans)} canais, custo US$ {usd:.4f}")
        recalc(cfg)
    elif args.acao in ("calcular", "temas"):
        recalc(cfg)
    elif args.acao == "transcrever":
        transcribe(cfg)


if __name__ == "__main__":
    main()
