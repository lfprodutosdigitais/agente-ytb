"""FASE 3 — Painel: gera ./referencias/painel.html, referencias.csv e ideias.md.

Uso:
  python3 scripts/painel.py           # só gera os arquivos (abre como arquivo: avaliações ficam no navegador)
  python3 scripts/painel.py --abrir   # gera e abre com um servidor local (127.0.0.1): avaliações salvas direto
                                      # em ./referencias/avaliacoes.json e "Adicionar referência" coleta na hora
- Aberto como arquivo, "Adicionar referência" entra numa fila que vai junto no "Exportar avaliações" e é
  processada na próxima rodada (este script lê o avaliacoes.json e coleta os canais/vídeos pedidos).
"""
import argparse
import csv
import json
import secrets
import shutil
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from yt_common import log, now, out_dir, read_json, write_json

import referencias as R

STATUS = ["ideia", "roteiro", "gravado", "postado"]


def import_downloaded_ratings(o):
    """Copia o avaliacoes*.json mais recente de ~/Downloads se for mais novo que o da pasta referencias."""
    dest = o / "avaliacoes.json"
    downloads = sorted((Path.home() / "Downloads").glob("avaliacoes*.json"), key=lambda p: p.stat().st_mtime)
    if downloads and (not dest.exists() or downloads[-1].stat().st_mtime > dest.stat().st_mtime):
        try:
            json.loads(downloads[-1].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log(f"aviso: {downloads[-1]} inválido, ignorado")
            return dest
        shutil.copy2(downloads[-1], dest)
        log(f"Avaliações importadas de {downloads[-1]}")
    return dest


def build_data(cfg):
    p = R.paths()
    o = p["out"]
    videos = read_json(p["videos"], {}) or {}
    hist = read_json(p["historico"], {}) or {}
    R.compute(videos, hist, cfg)
    analyses = R.load_analyses()
    cands, _, _ = R.candidates(videos, analyses, cfg)
    temas = R.validated_themes(videos, analyses, cfg)
    temas_validados = {t["tema"] for t in temas}
    refs = []
    for v in cands:
        a = analyses.get(v["video_id"], {})
        rel = a.get("relevancia")
        refs.append({
            "id": v["video_id"], "url": v.get("url"), "titulo": v.get("titulo"), "canal": v.get("canal"),
            "idioma": v.get("idioma"), "formato": v.get("formato"), "data": (v.get("data") or "")[:10],
            "views": v.get("views"), "comentarios": v.get("comentarios"), "thumb": v.get("thumbnail"),
            "score": v.get("score") or 0, "outlier_score": v.get("outlier_score"), "em_alta": bool(v.get("em_alta")),
            "velocidade_x": v.get("velocidade_x"), "relevancia": rel, "tema": a.get("tema"),
            "tema_validado": a.get("tema") in temas_validados,
            "motivo": a.get("motivo_relevancia"),
            "gancho": a.get("gancho_literal") or a.get("gancho"),
            "estrutura": a.get("estrutura") or [], "clique": a.get("por_que_clicaram"),
            "assistiram": a.get("por_que_assistiram"), "ideias": a.get("ideias") or [],
            "peso": v.get("peso", 1.0), "manual": bool(v.get("manual")),
        })
    publico = [{
        "id": aid, "publico": True, "canal": "Comentários do público", "tema": a.get("tema"), "relevancia": 5,
        "score": min(8, 2 + (a.get("pessoas") or 0) / 2), "pergunta": a.get("pergunta"), "pessoas": a.get("pessoas"),
        "exemplos": a.get("exemplos") or [], "ideias": a.get("ideias") or [], "titulo": a.get("pergunta"),
        "criado_em": a.get("criado_em"),
    } for aid, a in sorted(analyses.items()) if a.get("origem") == "comentarios"]
    avaliacoes = read_json(o / "avaliacoes.json", {}) or {}
    return {"gerado_em": now().isoformat(), "temas": temas, "refs": refs, "publico": publico,
            "avaliacoes": avaliacoes.get("ideias", avaliacoes),
            "fila_processada": read_json(o / "fila-processada.json", []) or [],
            "gerador": gerador_disponivel(),
            "imagem": thumbs_info(),
            "meus_videos": read_json(o / "meus-videos.json", None),
            "licoes": read_json(o / "licoes-videos.json", {}) or {},
            "cortes": {"longo": cfg["corte_longo"], "short": cfg["corte_short"]}}


def thumbs_info():
    import thumbs
    ok, motivo = thumbs.gemini_disponivel()
    feitos = sorted(f.stem for f in thumbs.pasta().glob("*.json")) if thumbs.pasta().exists() else []
    import roteiro
    rots = sorted(f.stem for f in roteiro.pasta().glob("*.json")) if roteiro.pasta().exists() else []
    return {"ok": ok, "motivo": motivo, "fotos": len(thumbs.fotos()), "pasta_fotos": str(thumbs.pasta_fotos()),
            "feitos": feitos, "roteiros": rots}


def gerador_disponivel():
    import gerador
    return gerador.disponivel()[0]


def merge_ratings(o, incoming):
    """Junta avaliações vindas do painel com as salvas (vence a mais recente por ideia)."""
    dest = o / "avaliacoes.json"
    cur = read_json(dest, {}) or {}
    ideias = cur.get("ideias", {}) if "ideias" in cur else cur
    for iid, a in (incoming or {}).items():
        if not isinstance(a, dict):
            continue
        if iid not in ideias or (a.get("atualizado") or "") >= (ideias[iid].get("atualizado") or ""):
            ideias[iid] = a
    out = {"exportado_em": now().isoformat(), "ideias": ideias}
    write_json(dest, out)
    return out


def process_saved_queue(cfg, o):
    """Pedidos de 'Adicionar referência' feitos com o painel aberto como arquivo (vieram no export)."""
    dest = o / "avaliacoes.json"
    av = read_json(dest, {}) or {}
    fila = av.pop("adicionar", None)
    if not fila:
        return []
    done_f = o / "fila-processada.json"
    feitas = read_json(done_f, []) or []
    pend = [x for x in fila if x.get("entrada") and x["entrada"] not in feitas]
    if pend:
        log(f"Processando {len(pend)} referência(s) pedida(s) no painel...")
        try:
            R.process_queue(cfg, pend)
        except SystemExit as e:  # sem autorização do Google, por exemplo
            log(f"  não deu para processar agora: {e}")
            return []
        feitas += [x["entrada"] for x in pend]
        write_json(done_f, feitas[-200:])
    write_json(dest, av)
    return pend


def prioridade(r):
    return (r["score"] or 0) * (r["relevancia"] or 0) * (1.25 if r["tema_validado"] else 1) * (r.get("peso") or 1)


def write_csv(data, path):
    cols = ["id", "url", "titulo", "canal", "idioma", "formato", "data", "views", "comentarios", "score",
            "outlier_score", "em_alta", "velocidade_x", "relevancia", "tema", "tema_validado", "n_ideias"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: abre certo no Excel/Numbers
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in sorted(data["refs"], key=prioridade, reverse=True):
            w.writerow([len(r["ideias"]) if c == "n_ideias" else r.get(c) for c in cols])


def write_ideas_md(data, path):
    av = data["avaliacoes"]
    L = ["# Ideias", "", f"_Gerado em {now():%Y-%m-%d}. Ordem: score × relevância × peso aprendido (tema validado vale 25% a mais)._", ""]
    if data["temas"]:
        L += ["## Temas validados (outlier em 2+ canais nos últimos 90 dias)", ""]
        L += [f"- **{t['tema']}** — {', '.join(t['canais'])}" for t in data["temas"]]
        L += [""]
    n = 0
    for r in sorted([r for r in data["refs"] if r["ideias"]], key=prioridade, reverse=True):
        for i in r["ideias"]:
            n += 1
            a = av.get(i["id"], {})
            voto = {"up": " 👍", "down": " 👎"}.get(a.get("voto"), "")
            L += [f"## {n}. {i['titulo']}{voto}", "",
                  f"- id: `{i['id']}` · status: {a.get('status', 'ideia')}"
                  + (f" · vídeo: {a['url']}" if a.get("url") else ""),
                  f"- referência: [{r['titulo']}]({r['url']}) — {r['canal']} · {r['score']}x · relevância {r['relevancia']}"
                  f" · {r['tema']}{' · TEMA VALIDADO' if r['tema_validado'] else ''}",
                  f"- **gancho:** {i.get('gancho', '')}"]
            L += [f"  - {t}" for t in i.get("topicos", [])]
            if i.get("a_conferir"):
                L.append(f"- ⚠️ **conferir:** {i['a_conferir']}")
            L.append("")
    if not n:
        L.append("_Nenhuma ideia ainda: rode a análise da Fase 2._")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Grana Própria — Referências</title>
<style>
/* Visual: vidro (glass) no estilo Apple sobre um fundo com luzes suaves na cor da marca */
:root{
  --bg:#eef3ef; --fg:#111813; --muted:rgba(17,24,19,.62); --faint:rgba(17,24,19,.42);
  --glass:rgba(255,255,255,.58); --glass-strong:rgba(255,255,255,.78); --glass-soft:rgba(255,255,255,.38);
  --stroke:rgba(255,255,255,.75); --stroke-dark:rgba(17,24,19,.08); --hl:rgba(255,255,255,.9);
  --shadow:0 10px 40px rgba(22,40,30,.10), 0 1px 2px rgba(22,40,30,.06);
  --accent:#16a34a; --accent-ink:#ffffff; --accent-soft:rgba(22,163,74,.14);
  --hot:#f97316; --hot-soft:rgba(249,115,22,.16); --warn:#9a6700; --warn-soft:rgba(234,179,8,.18);
  --up:#16a34a; --down:#e11d48;
  --blob1:#4ade80; --blob2:#22d3ee; --blob3:#fbbf24; --blob4:#a78bfa;
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --display:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --r-xl:26px; --r-lg:20px; --r-md:14px; --blur:saturate(180%) blur(22px);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#070b09; --fg:#eef4ef; --muted:rgba(238,244,239,.66); --faint:rgba(238,244,239,.42);
  --glass:rgba(28,34,30,.52); --glass-strong:rgba(34,42,37,.78); --glass-soft:rgba(255,255,255,.06);
  --stroke:rgba(255,255,255,.12); --stroke-dark:rgba(255,255,255,.06); --hl:rgba(255,255,255,.14);
  --shadow:0 12px 44px rgba(0,0,0,.45), 0 1px 2px rgba(0,0,0,.4);
  --accent:#34d399; --accent-ink:#04140b; --accent-soft:rgba(52,211,153,.16);
  --hot:#fb923c; --hot-soft:rgba(251,146,60,.18); --warn:#facc15; --warn-soft:rgba(250,204,21,.12);
  --up:#34d399; --down:#fb7185; --blob1:#15803d; --blob2:#0e7490; --blob3:#a16207; --blob4:#6d28d9; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#070b09; --fg:#eef4ef; --muted:rgba(238,244,239,.66); --faint:rgba(238,244,239,.42);
  --glass:rgba(28,34,30,.52); --glass-strong:rgba(34,42,37,.78); --glass-soft:rgba(255,255,255,.06);
  --stroke:rgba(255,255,255,.12); --stroke-dark:rgba(255,255,255,.06); --hl:rgba(255,255,255,.14);
  --shadow:0 12px 44px rgba(0,0,0,.45), 0 1px 2px rgba(0,0,0,.4);
  --accent:#34d399; --accent-ink:#04140b; --accent-soft:rgba(52,211,153,.16);
  --hot:#fb923c; --hot-soft:rgba(251,146,60,.18); --warn:#facc15; --warn-soft:rgba(250,204,21,.12);
  --up:#34d399; --down:#fb7185; --blob1:#15803d; --blob2:#0e7490; --blob3:#a16207; --blob4:#6d28d9; color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
html,body{margin:0}
body{background:var(--bg);color:var(--fg);font:15px/1.5 var(--font);-webkit-font-smoothing:antialiased;min-height:100vh}
.sky{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.sky i{position:absolute;border-radius:50%;filter:blur(90px);opacity:.55}
.sky i:nth-child(1){width:46vw;height:46vw;left:-10vw;top:-12vw;background:var(--blob1)}
.sky i:nth-child(2){width:40vw;height:40vw;right:-8vw;top:8vh;background:var(--blob2);opacity:.4}
.sky i:nth-child(3){width:36vw;height:36vw;left:30vw;bottom:-14vw;background:var(--blob3);opacity:.32}
.sky i:nth-child(4){width:28vw;height:28vw;right:10vw;bottom:10vh;background:var(--blob4);opacity:.22}
.glass{background:var(--glass);-webkit-backdrop-filter:var(--blur);backdrop-filter:var(--blur);
  border:1px solid var(--stroke);box-shadow:var(--shadow), inset 0 1px 0 var(--hl)}
@media (prefers-reduced-transparency:reduce){.glass{background:var(--glass-strong);-webkit-backdrop-filter:none;backdrop-filter:none}.sky{display:none}}
.page{position:relative;z-index:1;max-width:1240px;margin:0 auto;padding:16px 16px 80px}
header.bar{position:sticky;top:max(10px, env(safe-area-inset-top, 0px));z-index:10;border-radius:var(--r-xl);padding:14px 16px;display:flex;flex-direction:column;gap:12px}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;justify-content:space-between}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.logo{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;background:linear-gradient(145deg,var(--accent),#0d9488);color:#fff;font:700 18px var(--display);box-shadow:inset 0 1px 0 rgba(255,255,255,.35)}
h1{font:650 19px/1.2 var(--display);letter-spacing:-.01em;margin:0}
.sub{font-size:12.5px;color:var(--muted)}
.stats{display:flex;flex-wrap:wrap;gap:6px}
.stat{font-size:12.5px;padding:5px 10px;border-radius:999px;background:var(--glass-soft);border:1px solid var(--stroke-dark);font-variant-numeric:tabular-nums}
.stat b{font-weight:650}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pill,select.pill,input.pill{appearance:none;-webkit-appearance:none;font:inherit;font-size:13.5px;color:var(--fg);background:var(--glass-soft);
  border:1px solid var(--stroke-dark);border-radius:999px;padding:7px 14px;min-width:0;outline:none}
select.pill{padding-right:30px;background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
  background-position:calc(100% - 15px) 52%,calc(100% - 10px) 52%;background-size:5px 5px;background-repeat:no-repeat}
.pill:focus-visible,button:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button{font:inherit;cursor:pointer;color:inherit}
.btn{border-radius:999px;padding:8px 16px;font-weight:600;font-size:13.5px;border:1px solid var(--stroke-dark);background:var(--glass-soft)}
.btn:hover{background:var(--glass)}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:transparent;box-shadow:0 6px 18px var(--accent-soft)}
.btn.primary:hover{filter:brightness(1.05)}
.spacer{flex:1}
.flabel{font-size:12px;color:var(--muted);margin-right:-2px}
h2{font:650 21px/1.2 var(--display);letter-spacing:-.015em;margin:34px 4px 4px;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}
h2 .n{font:500 13px var(--font);color:var(--faint)}
.hint{margin:0 4px 14px;color:var(--muted);font-size:13.5px}
.temas{display:flex;flex-wrap:wrap;gap:10px}
.tema{border-radius:var(--r-lg);padding:12px 16px;display:flex;flex-direction:column;gap:2px}
.tema b{font:650 15px var(--display)}
.tema span{font-size:12.5px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr));gap:16px;align-items:start}
.card{border-radius:var(--r-xl);padding:10px;display:flex;flex-direction:column;gap:10px;min-width:0}
.thumb{display:block;position:relative;aspect-ratio:16/9;border-radius:18px;overflow:hidden;background:var(--glass-soft)}
.thumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .5s ease}
.card:hover .thumb img{transform:scale(1.03)}
.badges{position:absolute;inset:10px 10px auto 10px;display:flex;justify-content:space-between;gap:6px}
.badge{font:700 13px var(--display);padding:4px 10px;border-radius:999px;color:#fff;background:rgba(10,14,12,.55);
  -webkit-backdrop-filter:blur(12px) saturate(160%);backdrop-filter:blur(12px) saturate(160%);border:1px solid rgba(255,255,255,.22);font-variant-numeric:tabular-nums}
.badge.hot{background:rgba(249,115,22,.82)}
.badge.man{background:rgba(124,58,237,.78)}
.body{padding:2px 6px 6px;display:flex;flex-direction:column;gap:9px;min-width:0}
.meta{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12px;padding:3px 9px;border-radius:999px;background:var(--glass-soft);border:1px solid var(--stroke-dark);white-space:nowrap}
.chip.val{background:var(--accent-soft);color:var(--accent);border-color:transparent;font-weight:600}
.chip.rel{font-weight:650}
.title{font:620 16px/1.32 var(--display);letter-spacing:-.01em;color:inherit;text-decoration:none}
.title:hover{text-decoration:underline;text-underline-offset:3px}
.hook{font-size:14px;color:var(--muted);font-style:italic;padding:8px 12px;border-radius:var(--r-md);background:var(--glass-soft);border:1px solid var(--stroke-dark)}
details>summary{cursor:pointer;color:var(--muted);font-size:13.5px;list-style:none;display:flex;align-items:center;gap:6px}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"›";display:inline-block;transition:transform .2s;font-size:16px;line-height:1}
details[open]>summary::before{transform:rotate(90deg)}
details p,details ul{font-size:14px;margin:8px 0 0}
details ul{padding-left:18px}
.count{font-size:12.5px;color:var(--faint)}
.idea{border-radius:var(--r-lg);padding:12px;background:var(--glass-soft);border:1px solid var(--stroke-dark);display:flex;flex-direction:column;gap:8px}
.idea .id{font:500 11px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--faint)}
.idea h4{margin:0;font:620 15px/1.35 var(--display)}
.conferir{font-size:12.5px;color:var(--warn);background:var(--warn-soft);border-radius:12px;padding:7px 10px}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.row input,.row select{flex:1 1 150px}
.vote{width:40px;height:34px;border-radius:999px;border:1px solid var(--stroke-dark);background:var(--glass)}
.vote.on.up{background:var(--up);border-color:transparent}
.vote.on.down{background:var(--down);border-color:transparent}
.empty{color:var(--muted);padding:6px 4px}
/* folha de adicionar */
.scrim{position:fixed;inset:0;z-index:30;background:rgba(5,10,7,.28);display:grid;place-items:center;padding:16px}
.sheet{width:min(520px,100%);border-radius:var(--r-xl);padding:22px;display:flex;flex-direction:column;gap:14px;background:var(--glass-strong)}
.sheet h3{margin:0;font:650 19px var(--display)}
.sheet p{margin:0;color:var(--muted);font-size:13.5px}
.sheet input[type=text]{width:100%;font-size:15px;padding:12px 16px}
.check{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--muted)}
.status{font-size:13.5px;min-height:20px}
.job{font-size:13.5px;color:var(--muted)}
.sheet.wide{width:min(980px,100%);max-height:calc(100vh - 32px);overflow:auto}
.sheet > *{flex-shrink:0}
.tgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:12px}
.tcard{border-radius:var(--r-lg);padding:12px;display:flex;flex-direction:column;gap:8px;background:var(--glass);border:1px solid var(--stroke-dark)}
.tcard .ttxt{font:800 20px var(--display);letter-spacing:-.01em}
.tcard dl{margin:0;font-size:13px;display:grid;grid-template-columns:auto 1fr;gap:4px 8px}
.tcard dt{color:var(--faint)}
.tcard dd{margin:0}
.tcard img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:12px;display:block}
.tcard .imgs{display:flex;flex-direction:column;gap:8px}
.rtit{display:flex;flex-direction:column;gap:6px;margin:0;padding:0;list-style:none}
.rtit li{border-radius:12px;padding:8px 12px;background:var(--glass);border:1px solid var(--stroke-dark);font-size:14px}
.rtit li.rec{border-color:var(--accent)}
.rtit small{display:block;color:var(--muted);font-size:12.5px}
.rsec{border-radius:var(--r-lg);padding:12px 14px;background:var(--glass);border:1px solid var(--stroke-dark);display:flex;flex-direction:column;gap:6px}
.rsec h4{margin:0;font-size:15px}
.rsec .t{color:var(--faint);font-variant-numeric:tabular-nums;margin-right:6px}
.rsec .obj{color:var(--muted);font-size:12.5px}
.rsec .fala{white-space:pre-wrap;font-size:14.5px;line-height:1.55}
.rsec .tela{font-size:13px;color:var(--muted)}
.pbloco{border-radius:var(--r-lg);padding:12px 14px;background:var(--glass);border:1px solid var(--stroke-dark);display:flex;flex-direction:column;gap:8px}
a.btn{text-decoration:none;color:inherit;display:inline-flex;align-items:center}
.fthumb.pub{display:flex;flex-direction:column;align-items:center;justify-content:center;aspect-ratio:16/9;border-radius:14px;background:var(--accent-soft);gap:0;text-align:center}
.fthumb.pub span{font-size:26px;line-height:1}
.fthumb.pub b{font:800 22px var(--display)}
.fthumb.pub small{font-size:11.5px;color:var(--muted)}
.quote{margin:6px 0;padding:6px 10px;border-left:3px solid var(--accent);background:var(--glass);border-radius:0 10px 10px 0;font-size:13.5px;color:var(--muted)}
#r-texto{width:100%;border-radius:14px;padding:12px 14px;font:inherit;font-size:14.5px;line-height:1.5;border:1px solid var(--stroke-dark);background:var(--glass);color:var(--fg);resize:vertical}
.nota{font:800 30px var(--display)}
.prob{border-radius:14px;padding:10px 12px;background:var(--glass);border:1px solid var(--stroke-dark);display:flex;flex-direction:column;gap:6px;font-size:13.5px}
.prob .de{color:var(--muted);text-decoration:line-through;text-decoration-color:var(--down)}
.prob .para{background:var(--accent-soft);border-radius:10px;padding:6px 10px}
.tp{position:fixed;inset:0;z-index:60;background:#000;color:#fff;display:flex;flex-direction:column}
.tp-scroll{flex:1;overflow:hidden;position:relative}
.tp-txt{padding:38vh 7vw 70vh;max-width:1100px;margin:0 auto;font-size:var(--tpf,48px);line-height:1.45;font-weight:600;letter-spacing:.005em}
.tp-txt .tsec{display:block;font-size:max(14px,calc(var(--tpf,48px) * .33));color:#8a8a8a;font-weight:500;margin:1.2em 0 .3em;letter-spacing:.02em}
.tp-txt .tela{display:block;font-size:max(14px,calc(var(--tpf,48px) * .33));color:#facc15;font-weight:500;margin-top:.3em}
.tp.espelho .tp-txt{transform:scaleX(-1)}
.tp-linha{position:fixed;left:0;right:0;top:38vh;height:calc(var(--tpf,48px) * 1.5);border-top:2px solid rgba(250,204,21,.55);pointer-events:none}
.tp-prog{height:4px;background:#222}
.tp-prog i{display:block;height:100%;width:0;background:#22c55e}
.tp-count{position:fixed;inset:0;display:grid;place-items:center;font:800 30vh var(--display);color:#fff;background:rgba(0,0,0,.6)}
.tp-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:center;padding:10px 12px max(10px, env(safe-area-inset-bottom));background:#111;border-top:1px solid #222}
.tp-bar button{background:#262626;color:#fff;border:1px solid #333;border-radius:999px;padding:9px 16px;font-weight:600;font-size:15px;min-width:44px;min-height:40px}
.tp-bar button[aria-pressed="true"]{background:#14532d;border-color:#22c55e}
.tp-grp{display:inline-flex;align-items:center;gap:6px;color:#ccc;font-size:14px}
.tp-tempo{color:#aaa;font-variant-numeric:tabular-nums;font-size:14px}
.pbloco .cab{display:flex;justify-content:space-between;align-items:center;gap:8px}
.pbloco pre{margin:0;white-space:pre-wrap;font:inherit;font-size:14px;line-height:1.5;max-height:340px;overflow:auto}
.abthumbs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(200px,100%),1fr));gap:8px}
.abthumbs img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px;display:block}
.mv{border-radius:var(--r-xl);padding:16px;display:flex;flex-direction:column;gap:12px}
.mv-top{display:grid;grid-template-columns:160px 1fr;gap:14px;align-items:start}
.mv-top img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:12px}
.mv-top h4{margin:0 0 4px;font-size:16px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.kpi{border-radius:14px;padding:10px 12px;background:var(--glass);border:1px solid var(--stroke-dark);font-size:13px}
.kpi b{display:block;font-size:12px;color:var(--faint);font-weight:600;margin-bottom:4px}
.kpi .v{font:700 19px var(--display);font-variant-numeric:tabular-nums}
.kpi .s{color:var(--muted);font-variant-numeric:tabular-nums}
.diag{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:4px;font-size:13.5px}
.chart{position:relative}
.chart svg{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--stroke-dark);stroke-width:1}
.chart .ax{fill:var(--faint);font-size:11px}
.chart .l-este{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round}
.chart .l-media{fill:none;stroke:var(--muted);stroke-width:2;stroke-dasharray:5 4;stroke-linejoin:round}
.chart .queda{fill:var(--down);stroke:var(--bg);stroke-width:2}
.chart .qlab{fill:var(--fg);font-size:11px}
.chart .cross{stroke:var(--faint);stroke-width:1}
.legend{display:flex;gap:14px;font-size:12.5px;color:var(--muted);align-items:center}
.legend i{display:inline-block;width:18px;height:0;border-top:2px solid var(--accent);vertical-align:middle;margin-right:6px}
.legend i.m{border-top:2px dashed var(--muted)}
.tip{position:absolute;pointer-events:none;background:var(--glass-strong);border:1px solid var(--stroke-dark);border-radius:10px;padding:6px 9px;font-size:12.5px;white-space:nowrap;transform:translate(-50%,-110%);box-shadow:0 4px 14px rgba(0,0,0,.12)}
.licao{border-radius:14px;padding:12px 14px;background:var(--accent-soft);font-size:14px;display:flex;flex-direction:column;gap:6px}
.licao ul{margin:0;padding-left:18px}
.mv-top.so{grid-template-columns:1fr}
@media (max-width:640px){.mv-top{grid-template-columns:1fr}.kpis{gap:6px}.kpi{padding:8px;font-size:11.5px}.kpi .v{font-size:16px}}
.status.err{color:var(--down)}
.status.ok{color:var(--accent)}
.spin{width:16px;height:16px;border-radius:50%;border:2px solid var(--stroke-dark);border-top-color:var(--accent);display:inline-block;vertical-align:-3px;animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.toast{position:fixed;left:50%;bottom:max(18px, env(safe-area-inset-bottom, 0px));transform:translate(-50%,20px);opacity:0;transition:.25s;z-index:40;border-radius:999px;padding:10px 18px;font-size:14px;max-width:calc(100% - 32px)}
.toast.show{opacity:1;transform:translate(-50%,0)}
.mode{font-size:12px;color:var(--faint)}
.tabs{display:flex;gap:4px;padding:4px;border-radius:999px;background:var(--glass-soft);border:1px solid var(--stroke-dark);width:max-content;max-width:100%;overflow-x:auto}
.tab{border:0;background:transparent;border-radius:999px;padding:7px 16px;font-weight:600;font-size:14px;color:var(--muted);white-space:nowrap}
.tab[aria-selected="true"]{background:var(--glass-strong);color:var(--fg);box-shadow:0 2px 10px rgba(0,0,0,.08)}
.tab .n{font-weight:500;color:var(--faint);margin-left:4px}
.feed{display:flex;flex-direction:column;gap:14px;max-width:860px}
.fcard{border-radius:var(--r-xl);padding:14px;display:grid;grid-template-columns:168px 1fr;gap:16px;align-items:start;transition:transform .32s ease, opacity .32s ease}
.fcard.leave-up{transform:translateX(40px);opacity:0}
.fcard.leave-down{transform:translateX(-40px);opacity:0}
.fcard.enter{animation:enter .45s ease}
@keyframes enter{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.fthumb{display:block;aspect-ratio:16/9;border-radius:14px;overflow:hidden;background:var(--glass-soft)}
.fthumb img{width:100%;height:100%;object-fit:cover;display:block}
.fbody{display:flex;flex-direction:column;gap:9px;min-width:0}
.fbody h3{margin:0;font:650 18px/1.3 var(--display);letter-spacing:-.012em;text-wrap:balance}
.rank{font:600 12px var(--font);color:var(--faint)}
.chip.new{background:linear-gradient(90deg,rgba(124,58,237,.18),rgba(14,165,233,.18));color:var(--fg);border-color:transparent;font-weight:600}
.yesno{display:flex;gap:10px;margin-top:2px}
.big{flex:1;min-height:46px;border-radius:16px;font:650 16px var(--display);border:1px solid var(--stroke-dark);display:flex;align-items:center;justify-content:center;gap:8px}
.big.no{background:var(--glass-soft)}
.big.no:hover{background:rgba(225,29,72,.12);border-color:rgba(225,29,72,.35)}
.big.yes{background:var(--accent);color:var(--accent-ink);border-color:transparent}
.big.yes:hover{filter:brightness(1.06)}
.skeleton{border-radius:var(--r-xl);padding:18px;display:flex;align-items:center;gap:12px;color:var(--muted);font-size:14px}
.aprov{border-radius:var(--r-lg);padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.aprov h4{margin:0;font:620 16px/1.35 var(--display)}
.toast .undo{margin-left:12px;border:0;background:transparent;color:var(--accent);font-weight:700}
@media (max-width:720px){.fcard{grid-template-columns:1fr}.fthumb{max-height:170px}.tabs{width:100%}.tab{flex:1;padding:7px 6px;font-size:13.5px}}
@media (max-width:720px){.fthumb.pub{aspect-ratio:auto;flex-direction:row;gap:8px;padding:10px 14px;justify-content:flex-start}.fthumb.pub span{font-size:20px}.fthumb.pub b{font-size:18px}}
@media (max-width:520px){.tabs{display:grid;grid-template-columns:1fr 1fr;border-radius:20px}.tab{padding:8px 4px}}
@media (max-width:720px){header.bar{position:relative;top:0}.filters .pill{flex:1 1 44%}.flabel{display:none}h2{font-size:19px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<div class="sky" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
<div class="page">
  <header class="bar glass">
    <div class="top">
      <div class="brand">
        <div class="logo">G</div>
        <div><h1>Referências do Grana Própria</h1><div class="sub" id="sub"></div></div>
      </div>
      <div class="stats" id="stats"></div>
      <div class="job" id="job" role="status" aria-live="polite" hidden></div>
      <div class="row">
        <button class="btn primary" id="btn-add">＋ Adicionar referência</button>
        <button class="btn" id="exportar" title="Baixa avaliacoes.json para a pasta Downloads">Exportar avaliações</button>
        <button class="btn" id="importar">Importar…</button>
        <input type="file" id="arquivo" accept=".json" hidden>
      </div>
    </div>
    <nav class="tabs" role="tablist" aria-label="Seções">
      <button class="tab" role="tab" data-view="ideias" aria-selected="true">Ideias para gravar</button>
      <button class="tab" role="tab" data-view="aprovadas" aria-selected="false">Aprovadas<span class="n" id="n-aprov"></span></button>
      <button class="tab" role="tab" data-view="videos" aria-selected="false">Meus vídeos</button>
      <button class="tab" role="tab" data-view="refs" aria-selected="false">Referências</button>
    </nav>
    <div class="filters" id="filters" role="group" aria-label="Filtros" hidden>
      <span class="flabel">Filtrar</span>
      <select class="pill" id="f-canal" aria-label="Canal"><option value="">Todos os canais</option></select>
      <select class="pill" id="f-tema" aria-label="Tema"><option value="">Todos os temas</option></select>
      <select class="pill" id="f-formato" aria-label="Formato"><option value="">Longos e Shorts</option><option value="longo">Só longos</option><option value="short">Só Shorts</option></select>
      <select class="pill" id="f-rel" aria-label="Relevância"><option value="0">Qualquer relevância</option><option value="3" selected>Relevância 3+</option><option value="4">Relevância 4+</option><option value="5">Relevância 5</option></select>
      <select class="pill" id="f-ideias" aria-label="Mostrar"><option value="">Todas as referências</option><option value="com">Só com ideias</option><option value="sem">Sem avaliação</option></select>
      <select class="pill" id="f-score" aria-label="Score mínimo"><option value="0">Qualquer score</option><option value="3">3x ou mais</option><option value="5">5x ou mais</option><option value="10">10x ou mais</option></select>
      <span class="spacer"></span>
      <span class="mode" id="mode"></span>
    </div>
  </header>

  <div id="view-ideias">
    <h2>Ideias para gravar</h2>
    <p class="hint" id="hint-ideias">Só diga <b>Sim</b> ou <b>Não</b>. O que você recusa sai da lista e outra entra no lugar; o sistema aprende com o padrão dos seus “nãos”.</p>
    <div class="row" style="margin-bottom:12px" id="com-row"><button class="btn" id="btn-com">💬 Buscar ideias nos comentários</button><span class="status" id="com-status"></span></div>
    <div class="feed" id="feed"></div>
  </div>

  <div id="view-aprovadas" hidden>
    <h2>Aprovadas</h2>
    <p class="hint">Acompanhe cada ideia até virar vídeo. Quando postar, cole o link: o sistema mede o vídeo aos 7 e 30 dias.</p>
    <div class="feed" id="aprovadas"></div>
  </div>

  <div id="view-videos" hidden>
    <h2>Meus vídeos</h2>
    <p class="hint">Cada vídeo do Léo aos 2, 7 e 30 dias contra a média dele, onde o público saiu e o que aprender. <span id="mv-quando"></span></p>
    <div class="row" style="margin-bottom:12px"><button class="btn" id="mv-atualizar">Atualizar números</button><span class="status" id="mv-status"></span></div>
    <div class="feed" id="meus-videos"></div>
  </div>

  <div id="view-refs" hidden>
  <section id="sec-manual" hidden>
    <h2>Adicionadas por você <span class="n" id="n-manual"></span></h2>
    <p class="hint">Vídeos que você colou à mão. Entram na próxima análise para ganhar nota e ideias.</p>
    <div class="grid" id="manual"></div>
  </section>

  <h2>Temas validados</h2>
  <p class="hint">O mesmo tema virou outlier em 2 ou mais canais nos últimos 90 dias.</p>
  <div class="temas" id="temas"></div>

  <h2>Em alta <span class="n" id="n-alta"></span></h2>
  <p class="hint">Crescendo 3x mais rápido que vídeos de idade parecida do mesmo canal.</p>
  <div class="grid" id="alta"></div>

  <h2>Referências <span class="n" id="n-refs"></span></h2>
  <p class="hint">Ordem: score × relevância × peso aprendido com as suas avaliações.</p>
  <div class="grid" id="refs"></div>
  </div>
</div>

<div class="scrim" id="scrim" hidden>
  <form class="sheet glass" id="form-add" autocomplete="off">
    <h3>Adicionar referência</h3>
    <p>Cole o <b>@ do canal</b>, o <b>link do canal</b> ou o <b>link de um vídeo</b>. Canal entra na lista e é coletado; vídeo entra como referência avulsa. Com a chave da Anthropic, o Claude já dá a nota e escreve as ideias.</p>
    <input class="pill" type="text" id="entrada" placeholder="@canal, youtube.com/@canal ou youtu.be/…" aria-label="Canal ou vídeo">
    <label class="check"><input type="checkbox" id="en"> Canal em inglês (adaptar para o Brasil)</label>
    <div class="status" id="status" role="status" aria-live="polite"></div>
    <div class="row" style="justify-content:flex-end">
      <button type="button" class="btn" id="cancelar">Fechar</button>
      <button type="submit" class="btn primary" id="enviar">Adicionar</button>
    </div>
  </form>
</div>
<div class="scrim" id="tscrim" hidden>
  <div class="sheet glass wide" role="dialog" aria-modal="true" aria-labelledby="t-tit">
    <h3 id="t-tit">Thumb</h3>
    <p id="t-sub"></p>
    <div class="status" id="t-status" role="status" aria-live="polite"></div>
    <div class="tgrid" id="t-grid"></div>
    <div class="row" style="justify-content:flex-end">
      <button type="button" class="btn" id="t-novos">Outros 3 conceitos</button>
      <button type="button" class="btn primary" id="t-fechar">Fechar</button>
    </div>
  </div>
</div>
<div class="scrim" id="rscrim" hidden>
  <div class="sheet glass wide" role="dialog" aria-modal="true" aria-labelledby="r-tit">
    <h3 id="r-tit">Roteiro</h3>
    <div class="tabs" id="r-modo" role="tablist" aria-label="Qual roteiro">
      <button class="tab" role="tab" data-m="claude" aria-selected="true">Roteiro do Claude</button>
      <button class="tab" role="tab" data-m="meu" aria-selected="false">✍️ Meu roteiro</button>
    </div>
    <div id="r-claude" style="display:flex;flex-direction:column;gap:12px">
      <p id="r-sub" style="margin:0"></p>
      <div class="status" id="r-status" role="status" aria-live="polite"></div>
      <div id="r-corpo" style="display:flex;flex-direction:column;gap:12px"></div>
    </div>
    <div id="r-meu" hidden style="display:flex;flex-direction:column;gap:10px">
      <p style="margin:0">Cole o roteiro que você escreveu (um parágrafo por bloco). O sistema confere as regras de retenção do canal e o Claude aponta o trecho exato e uma versão reescrita.</p>
      <textarea id="r-texto" rows="10" aria-label="Seu roteiro" placeholder="Cole aqui o seu roteiro…"></textarea>
      <div class="row"><button type="button" class="btn primary" id="r-revisar">Revisar com o Claude</button><span class="status" id="r-mstatus" role="status" aria-live="polite"></span></div>
      <div id="r-rev" style="display:flex;flex-direction:column;gap:10px"></div>
    </div>
    <div class="row" style="justify-content:flex-end">
      <button type="button" class="btn" id="r-tp">🎬 Teleprompter</button>
      <button type="button" class="btn" id="r-copiar" hidden>Copiar roteiro</button>
      <a class="btn" id="r-baixar" hidden download>Baixar .md</a>
      <button type="button" class="btn" id="r-refazer">Refazer</button>
      <button type="button" class="btn primary" id="r-fechar">Fechar</button>
    </div>
  </div>
</div>
<div class="tp" id="tp" hidden role="dialog" aria-label="Teleprompter">
  <div class="tp-prog"><i id="tp-prog"></i></div>
  <div class="tp-scroll" id="tp-scroll"><div class="tp-txt" id="tp-txt"></div></div>
  <div class="tp-linha" aria-hidden="true"></div>
  <div class="tp-count" id="tp-count" hidden></div>
  <div class="tp-bar">
    <button type="button" id="tp-play" aria-label="Iniciar ou pausar (espaço)">▶︎ Iniciar</button>
    <span class="tp-grp"><button type="button" id="tp-lento" aria-label="Mais devagar">−</button><span id="tp-vel"></span><button type="button" id="tp-rapido" aria-label="Mais rápido">+</button></span>
    <span class="tp-grp"><button type="button" id="tp-menor" aria-label="Letra menor">A−</button><button type="button" id="tp-maior" aria-label="Letra maior">A+</button></span>
    <button type="button" id="tp-espelho" aria-pressed="false">Espelhar</button>
    <span id="tp-tempo" class="tp-tempo"></span>
    <button type="button" id="tp-sair" aria-label="Sair (Esc)">Sair</button>
  </div>
</div>
<div class="scrim" id="pscrim" hidden>
  <div class="sheet glass wide" role="dialog" aria-modal="true" aria-labelledby="p-tit">
    <h3 id="p-tit">Material de publicação</h3>
    <p>Cole o link do vídeo já enviado ao YouTube (pode ser <b>não listado</b>). O sistema lê a legenda do vídeo e escreve descrição, capítulos, comentário fixado e tags.</p>
    <div class="row"><input class="pill" type="text" id="p-link" placeholder="youtu.be/… ou youtube.com/watch?v=…" aria-label="Link do vídeo">
      <button type="button" class="btn primary" id="p-gerar">Gerar</button></div>
    <details id="p-links-box"><summary>Links fixos da descrição (grupo, Instagram…) — entram em todo vídeo</summary>
      <textarea class="pill" id="p-links" rows="4" style="width:100%;margin-top:8px;border-radius:14px;padding:10px 14px;font:inherit" placeholder="👉 Grupo VIP: https://…&#10;📸 Instagram: https://instagram.com/…"></textarea>
      <div class="row" style="margin-top:6px"><button type="button" class="btn" id="p-salvar-links">Salvar links</button></div></details>
    <div class="status" id="p-status" role="status" aria-live="polite"></div>
    <div id="p-corpo" style="display:flex;flex-direction:column;gap:12px"></div>
    <div class="row" style="justify-content:flex-end"><a class="btn" id="p-baixar" hidden download>Baixar .txt</a><button type="button" class="btn primary" id="p-fechar">Fechar</button></div>
  </div>
</div>
<div class="toast glass" id="toast" role="status" aria-live="polite"><span id="toast-msg"></span><button class="undo" id="undo" hidden>Desfazer</button></div>

<script id="dados" type="application/json">__DATA__</script>
<script>
const SERVER = location.protocol.startsWith('http');
const TOKEN = '__TOKEN__';
let D = JSON.parse(document.getElementById('dados').textContent);
const KEY = 'grana-painel-avaliacoes', QKEY = 'grana-painel-fila';
const STATUS = ['ideia','roteiro','gravado','postado'];
const ls = {get(k, d){try{return JSON.parse(localStorage.getItem(k)) ?? d}catch(e){return d}}, set(k, v){try{localStorage.setItem(k, JSON.stringify(v))}catch(e){}}};
let AV = ls.get(KEY, {});
let FILA = ls.get(QKEY, []);
function mergeAv(src){ for (const [id, a] of Object.entries(src || {})) if (!AV[id] || (a.atualizado || '') > (AV[id].atualizado || '')) AV[id] = a; }
mergeAv(D.avaliacoes);
FILA = FILA.filter(x => !(D.fila_processada || []).includes(x.entrada)); ls.set(QKEY, FILA);

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => (n ?? 0).toLocaleString('pt-BR');
const prio = r => (r.score || 0) * (r.relevancia || 0) * (r.tema_validado ? 1.25 : 1) * (r.peso || 1);
let undoFn = null;
function toast(t, ms=2600, undo=null){ const el = document.getElementById('toast'); document.getElementById('toast-msg').textContent = t;
  undoFn = undo; document.getElementById('undo').hidden = !undo; el.classList.add('show'); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('show'), ms); }
async function api(path, body){
  const r = await fetch(path, {method: body ? 'POST' : 'GET', headers: {'Content-Type': 'application/json', 'X-Token': TOKEN}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json().catch(() => ({ok: false, erro: 'Resposta inválida do servidor'}));
  if (!r.ok || j.ok === false) throw new Error(j.erro || 'Falhou');
  return j;
}
let saveT;
function persist(){ ls.set(KEY, AV); if (SERVER){ clearTimeout(saveT); saveT = setTimeout(() => api('/api/avaliacoes', {ideias: AV}).catch(e => toast('Não salvou: ' + e.message)), 500); } }
function setAv(id, patch){ AV[id] = {...(AV[id] || {}), ...patch, atualizado: new Date().toISOString()}; persist(); stats(); }

function fillSelects(){
  for (const [id, key] of [['f-canal','canal'],['f-tema','tema']]){
    const s = document.getElementById(id), keep = s.value;
    s.querySelectorAll('option:not(:first-child)').forEach(o => o.remove());
    [...new Set(D.refs.map(r => r[key]).filter(Boolean))].sort().forEach(v => s.insertAdjacentHTML('beforeend', `<option>${esc(v)}</option>`));
    s.value = keep;
  }
}
const refsTodas = () => D.refs.concat(D.publico || []);
function stats(){
  const ideias = refsTodas().flatMap(r => r.ideias);
  const ups = ideias.filter(i => (AV[i.id] || {}).voto === 'up').length, downs = ideias.filter(i => (AV[i.id] || {}).voto === 'down').length;
  const post = ideias.filter(i => (AV[i.id] || {}).status === 'postado').length;
  document.getElementById('stats').innerHTML = `<span class="stat"><b>${D.refs.length}</b> referências</span><span class="stat"><b>${ideias.length}</b> ideias</span>`
    + `<span class="stat">👍 <b>${ups}</b> · 👎 <b>${downs}</b></span><span class="stat"><b>${post}</b> postadas</span>`;
  document.getElementById('sub').textContent = 'Atualizado em ' + new Date(D.gerado_em).toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'});
  document.getElementById('mode').textContent = SERVER ? '● salvando automaticamente' : (FILA.length ? `${FILA.length} pedido(s) na fila · exporte para enviar` : 'aberto como arquivo · use Exportar');
}

function ideaHTML(i){
  const a = AV[i.id] || {}, st = a.status || 'ideia';
  return `<div class="idea" data-id="${esc(i.id)}">
    <span class="id">${esc(i.id)}</span>
    <h4>${esc(i.titulo)}</h4>
    <details><summary>Gancho e tópicos</summary><div class="hook" style="margin-top:8px">${esc(i.gancho)}</div>
      <ul>${(i.topicos || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul></details>
    ${i.a_conferir ? `<div class="conferir">⚠️ Conferir: ${esc(i.a_conferir)}</div>` : ''}
    <div class="row">
      <button class="vote up ${a.voto === 'up' ? 'on' : ''}" data-v="up" aria-label="Gostei">👍</button>
      <button class="vote down ${a.voto === 'down' ? 'on' : ''}" data-v="down" aria-label="Não gostei">👎</button>
      <input class="pill motivo" placeholder="motivo (opcional)" value="${esc(a.motivo)}" aria-label="Motivo">
    </div>
    <div class="row">
      <select class="pill status" aria-label="Status">${STATUS.map(s => `<option ${s === st ? 'selected' : ''}>${s}</option>`).join('')}</select>
      <input class="pill url" placeholder="link do vídeo postado" value="${esc(a.url)}" ${st === 'postado' ? '' : 'hidden'} aria-label="Link do vídeo">
    </div>
  </div>`;
}
function cardHTML(r){
  const thumb = r.thumb || `https://i.ytimg.com/vi/${r.id}/hqdefault.jpg`;
  return `<article class="card glass">
    <a class="thumb" href="${esc(r.url)}" target="_blank" rel="noopener">
      <img loading="lazy" src="${esc(thumb)}" alt="" onerror="this.style.visibility='hidden'">
      <div class="badges"><span class="badge">${r.manual && !r.score ? 'manual' : r.score + 'x'}</span>
        ${r.em_alta ? `<span class="badge hot">em alta${r.velocidade_x ? ' ' + r.velocidade_x + 'x' : ''}</span>` : (r.manual ? '<span class="badge man">você</span>' : '')}</div>
    </a>
    <div class="body">
      <div class="meta">
        <span class="chip">${esc(r.canal)}${r.idioma === 'en' ? ' · EN' : ''}</span>
        <span class="chip rel">${r.relevancia ? 'relevância ' + r.relevancia + '/5' : 'sem nota ainda'}</span>
        ${r.tema ? `<span class="chip ${r.tema_validado ? 'val' : ''}">${esc(r.tema)}${r.tema_validado ? ' ✓' : ''}</span>` : ''}
        <span class="chip">${esc(r.data)} · ${fmt(r.views)} views</span>
      </div>
      <a class="title" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.titulo)}</a>
      ${r.gancho ? `<div class="hook">“${esc(r.gancho)}”</div>` : ''}
      ${(r.clique || r.assistiram || r.estrutura.length) ? `<details><summary>Por que funcionou</summary>
        ${r.clique ? `<p><b>Por que clicaram:</b> ${esc(r.clique)}</p>` : ''}
        ${r.assistiram ? `<p><b>Por que assistiram:</b> ${esc(r.assistiram)}</p>` : ''}
        ${r.estrutura.length ? `<ul>${r.estrutura.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}</details>`
        : (r.motivo ? `<div class="count">${esc(r.motivo)}</div>` : '')}
      ${r.ideias.length ? `<div class="count">${r.ideias.length} ideias para o seu canal</div>` : ''}
      ${r.ideias.map(ideaHTML).join('')}
    </div>
  </article>`;
}
function filtered(){
  const v = id => document.getElementById(id).value;
  const c = v('f-canal'), t = v('f-tema'), f = v('f-formato'), s = parseFloat(v('f-score')) || 0, rel = parseInt(v('f-rel')), m = v('f-ideias');
  return D.refs.filter(r => (!c || r.canal === c) && (!t || r.tema === t) && (!f || r.formato === f) && (r.manual || (r.score || 0) >= s)
    && (r.manual || rel === 0 || (r.relevancia || 0) >= rel) && (m !== 'com' || r.ideias.length)
    && (m !== 'sem' || (r.ideias.length && r.ideias.some(i => !(AV[i.id] || {}).voto))));
}
function render(){
  const list = filtered();
  const man = list.filter(r => r.manual);
  const alta = list.filter(r => r.em_alta && !r.manual).sort((a, b) => (b.velocidade_x || 0) - (a.velocidade_x || 0));
  const refs = list.filter(r => !r.em_alta && !r.manual).sort((a, b) => prio(b) - prio(a));
  document.getElementById('sec-manual').hidden = !man.length;
  document.getElementById('manual').innerHTML = man.map(cardHTML).join('');
  document.getElementById('n-manual').textContent = man.length;
  document.getElementById('temas').innerHTML = D.temas.length ? D.temas.map(t => `<div class="tema glass"><b>${esc(t.tema)}</b><span>${t.canais.map(esc).join(' · ')} · ${t.videos.length} vídeos</span></div>`).join('')
    : '<div class="empty">Nenhum ainda.</div>';
  document.getElementById('alta').innerHTML = alta.map(cardHTML).join('') || '<div class="empty">Nada em alta com esses filtros.</div>';
  document.getElementById('refs').innerHTML = refs.map(cardHTML).join('') || '<div class="empty">Nada com esses filtros.</div>';
  document.getElementById('n-alta').textContent = alta.length;
  document.getElementById('n-refs').textContent = refs.length;
  stats();
}

document.querySelector('.filters').addEventListener('input', render);
document.querySelector('.page').addEventListener('click', e => {
  const b = e.target.closest('.vote'); if (!b) return;
  const box = b.closest('.idea'), id = box.dataset.id, v = b.dataset.v;
  setAv(id, {voto: (AV[id] || {}).voto === v ? null : v});
  box.querySelectorAll('.vote').forEach(x => x.classList.toggle('on', x.dataset.v === AV[id].voto));
});
document.querySelector('.page').addEventListener('change', e => {
  const box = e.target.closest('.idea'); if (!box) return;
  const id = box.dataset.id;
  if (e.target.classList.contains('status')) { setAv(id, {status: e.target.value}); box.querySelector('.url').hidden = e.target.value !== 'postado'; if (e.target.value === 'postado') box.querySelector('.url').focus(); }
  else if (e.target.classList.contains('motivo')) setAv(id, {motivo: e.target.value});
  else if (e.target.classList.contains('url')) setAv(id, {url: e.target.value.trim()});
});

// adicionar referência
const scrim = document.getElementById('scrim'), statusEl = document.getElementById('status');
function openSheet(){ scrim.hidden = false; statusEl.textContent = ''; statusEl.className = 'status'; document.getElementById('entrada').focus(); }
function closeSheet(){ scrim.hidden = true; }
document.getElementById('btn-add').addEventListener('click', openSheet);
document.getElementById('cancelar').addEventListener('click', closeSheet);
scrim.addEventListener('click', e => { if (e.target === scrim) closeSheet(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !scrim.hidden) closeSheet(); });
document.getElementById('form-add').addEventListener('submit', async e => {
  e.preventDefault();
  const entrada = document.getElementById('entrada').value.trim(), lang = document.getElementById('en').checked ? 'en' : 'pt';
  if (!entrada) { statusEl.className = 'status err'; statusEl.textContent = 'Cole um @canal, o link do canal ou de um vídeo.'; return; }
  if (!SERVER) {
    if (!FILA.some(x => x.entrada === entrada)) FILA.push({entrada, lang, pedido: new Date().toISOString()});
    ls.set(QKEY, FILA); stats();
    statusEl.className = 'status ok';
    statusEl.textContent = 'Anotado. Vai junto no “Exportar avaliações” e é coletado na próxima rodada. Para adicionar na hora, abra o painel pelo atalho “Abrir painel”.';
    document.getElementById('entrada').value = '';
    return;
  }
  const btn = document.getElementById('enviar'); btn.disabled = true;
  statusEl.className = 'status'; statusEl.innerHTML = '<span class="spin"></span> Buscando no YouTube…';
  try {
    const r = await api('/api/adicionar', {entrada, lang});
    statusEl.className = 'status ok';
    statusEl.textContent = r.msg + (r.analisando ? ` O Claude está analisando ${r.analisando} vídeo(s); as ideias aparecem em “Ideias para gravar” conforme ficam prontas.` : (r.aviso ? ' ' + r.aviso : ''));
    await refresh();
    document.getElementById('entrada').value = '';
    if (r.analisando) acompanhar();
  } catch (err) { statusEl.className = 'status err'; statusEl.textContent = err.message; }
  finally { btn.disabled = false; }
});

async function refresh(){ D = (await api('/api/dados')).dados; mergeAv(D.avaliacoes); fillSelects(); renderAll(); }
const jobEl = document.getElementById('job');
let jobT = null, jobFeitos = 0;
async function acompanhar(){
  clearTimeout(jobT);
  let j;
  try { j = (await api('/api/status')).job; } catch (e) { jobT = setTimeout(acompanhar, 5000); return; }
  if (j.feitos > jobFeitos) { jobFeitos = j.feitos; await refresh().catch(() => {}); }
  if (j.rodando) {
    jobEl.hidden = false;
    jobEl.innerHTML = `<span class="spin"></span> Claude analisando ${j.atual} de ${j.total}: ${esc(j.titulo || '')}`;
    jobT = setTimeout(acompanhar, 3000);
  } else if (j.total) {
    jobFeitos = 0; jobEl.hidden = true;
    toast(`Pronto: ${j.ideias} ideias novas de ${j.feitos} vídeo(s)` + (j.erros.length ? ` · ${j.erros.length} erro(s): ${j.erros[0]}` : ''), 9000);
  }
}
if (SERVER) acompanhar();  // retoma o acompanhamento se a página for recarregada no meio

// exportar / importar
document.getElementById('exportar').addEventListener('click', () => {
  const ideias = {};
  for (const r of refsTodas()) for (const i of r.ideias) if (AV[i.id]) ideias[i.id] = {...AV[i.id], titulo: i.titulo, referencia: r.id, canal: r.canal, tema: r.tema};
  for (const [id, a] of Object.entries(AV)) if (!ideias[id]) ideias[id] = a;
  const out = {exportado_em: new Date().toISOString(), ideias};
  if (FILA.length) out.adicionar = FILA;
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], {type: 'application/json'}));
  a.download = 'avaliacoes.json'; document.body.appendChild(a); a.click(); a.remove();
  toast(`${Object.keys(ideias).length} avaliações exportadas${FILA.length ? ` + ${FILA.length} referência(s) para adicionar` : ''}`);
});
document.getElementById('importar').addEventListener('click', () => document.getElementById('arquivo').click());
document.getElementById('arquivo').addEventListener('change', async e => {
  const f = e.target.files[0]; if (!f) return;
  try { const j = JSON.parse(await f.text()); mergeAv(j.ideias || j); persist(); renderAll(); toast('Avaliações importadas'); }
  catch (err) { toast('Arquivo inválido'); }
});
// ---------- Ideias para gravar (Sim / Não) ----------
const FEED_N = 8;
let VIEW = ls.get('grana-painel-aba', 'ideias');
let GERANDO = 0;
function liveWeights(){
  const t = {canal: {}, tema: {}};
  for (const r of refsTodas()) for (const i of r.ideias) {
    const v = (AV[i.id] || {}).voto; if (!v) continue;
    for (const k of ['canal', 'tema']) { const key = r[k] || '—'; t[k][key] = t[k][key] || {u: 0, d: 0}; t[k][key][v === 'up' ? 'u' : 'd']++; }
  }
  const w = (k, key) => { const x = t[k][key]; return x ? 0.5 + (x.u + 1) / (x.u + x.d + 2) : 1; };
  return w;
}
function allIdeas(){ return refsTodas().flatMap(r => r.ideias.map(i => ({i, r}))); }
function pool(){
  const w = liveWeights();
  const base = r => (r.score || 1) * (r.relevancia || 3) * (r.tema_validado ? 1.25 : 1);
  return allIdeas().filter(({i}) => !(AV[i.id] || {}).voto)
    .map(x => ({...x, s: base(x.r) * w('canal', x.r.canal) * w('tema', x.r.tema)}))
    // ideias recém-criadas (✨) entram no topo, no lugar da que foi recusada; a mais nova primeiro
    .sort((a, b) => ((b.i.origem === 'gerada') - (a.i.origem === 'gerada')) || (b.i.gerada_em || '').localeCompare(a.i.gerada_em || '') || (b.s - a.s));
}
function feedCardPublico({i, r}, n){
  return `<article class="fcard glass" data-id="${esc(i.id)}">
    <div class="fthumb pub" aria-hidden="true"><span>💬</span><b>${r.pessoas || ''}</b><small>pessoas pediram</small></div>
    <div class="fbody">
      <span class="rank">#${n} · pedida nos comentários</span>
      <h3>${esc(i.titulo)}</h3>
      <div class="meta">${r.tema ? `<span class="chip">${esc(r.tema)}</span>` : ''}<span class="chip new">💬 público</span></div>
      <div class="hook">${esc(i.gancho)}</div>
      <details><summary>Ver o que pediram e o roteiro</summary>
        <p class="count" style="margin-top:8px"><b>${esc(r.pergunta)}</b></p>
        ${(r.exemplos || []).map(e => `<blockquote class="quote">“${esc(e)}”</blockquote>`).join('')}
        <ul>${(i.topicos || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>
        ${i.a_conferir ? `<div class="conferir" style="margin-top:8px">⚠️ ${esc(i.a_conferir)}</div>` : ''}</details>
      <div class="yesno"><button class="big no" data-v="down">👎 Não</button><button class="big yes" data-v="up">👍 Sim</button></div>
    </div>
  </article>`;
}
function feedCard({i, r}, n){
  if (r.publico) return feedCardPublico({i, r}, n);
  const thumb = r.thumb || `https://i.ytimg.com/vi/${r.id}/hqdefault.jpg`;
  return `<article class="fcard glass" data-id="${esc(i.id)}">
    <a class="fthumb" href="${esc(r.url)}" target="_blank" rel="noopener" title="Ver a referência"><img loading="lazy" src="${esc(thumb)}" alt="" onerror="this.style.visibility='hidden'"></a>
    <div class="fbody">
      <span class="rank">#${n} · baseada em ${esc(r.canal)} (${r.score ? r.score + 'x' : 'manual'})</span>
      <h3>${esc(i.titulo)}</h3>
      <div class="meta">${r.tema ? `<span class="chip ${r.tema_validado ? 'val' : ''}">${esc(r.tema)}${r.tema_validado ? ' ✓' : ''}</span>` : ''}
        ${i.origem === 'gerada' ? '<span class="chip new">✨ nova</span>' : ''}</div>
      <div class="hook">${esc(i.gancho)}</div>
      <details><summary>Ver roteiro${i.a_conferir ? ' e o que conferir' : ''}</summary>
        <ul>${(i.topicos || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>
        ${i.a_conferir ? `<div class="conferir" style="margin-top:8px">⚠️ ${esc(i.a_conferir)}</div>` : ''}
        <p class="count">Referência: <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.titulo)}</a></p></details>
      <div class="yesno"><button class="big no" data-v="down">👎 Não</button><button class="big yes" data-v="up">👍 Sim</button></div>
    </div>
  </article>`;
}
function renderFeed(){
  const p = pool();
  const el = document.getElementById('feed');
  const sk = Array.from({length: GERANDO}, () => '<div class="skeleton glass"><span class="spin"></span> Criando uma ideia nova a partir das referências…</div>').join('');
  el.innerHTML = p.length ? p.slice(0, FEED_N).map((x, k) => feedCard(x, k + 1)).join('') + sk
    : sk + '<div class="empty">Você avaliou todas as ideias. As próximas chegam com a análise das novas referências.</div>';
  document.getElementById('hint-ideias').innerHTML = `Só diga <b>Sim</b> ou <b>Não</b>. O que você recusa sai e outra entra no lugar${D.gerador ? ' — e uma ideia <b>nova</b> é criada na hora' : ''}. ${p.length} na fila.`;
}
function renderAprov(){
  const ups = allIdeas().filter(({i}) => (AV[i.id] || {}).voto === 'up')
    .sort((a, b) => STATUS.indexOf((AV[b.i.id] || {}).status || 'ideia') - STATUS.indexOf((AV[a.i.id] || {}).status || 'ideia'));
  document.getElementById('n-aprov').textContent = ups.length || '';
  document.getElementById('aprovadas').innerHTML = ups.map(({i, r}) => { const a = AV[i.id] || {}, st = a.status || 'ideia';
    return `<div class="aprov glass idea" data-id="${esc(i.id)}"><span class="rank">${esc(r.canal)} · ${esc(r.tema || '')}</span><h4>${esc(i.titulo)}</h4>
      <details><summary>Gancho e roteiro</summary><div class="hook" style="margin-top:8px">${esc(i.gancho)}</div><ul>${(i.topicos || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>
      ${i.a_conferir ? `<div class="conferir">⚠️ ${esc(i.a_conferir)}</div>` : ''}</details>
      <div class="row"><select class="pill status" aria-label="Status">${STATUS.map(s => `<option ${s === st ? 'selected' : ''}>${s}</option>`).join('')}</select>
        <input class="pill url" placeholder="link do vídeo postado" value="${esc(a.url)}" ${st === 'postado' ? '' : 'hidden'} aria-label="Link do vídeo">
        ${SERVER && D.gerador && ['gravado', 'postado'].includes(st) ? `<button class="btn" data-pub="1">📦 Publicação</button>` : ''}
        ${SERVER && D.gerador ? `<button class="btn" data-roteiro="1">📝 Roteiro${(D.imagem.roteiros || []).includes(i.id) ? ' ✓' : ''}</button>` : ''}
        ${SERVER && D.gerador ? `<button class="btn" data-thumb="1">🎨 Thumb${(D.imagem.feitos || []).includes(i.id) ? ' ✓' : ''}</button>` : ''}
        <button class="btn" data-tirar="1">Tirar</button></div></div>`; }).join('') || '<div class="empty">Nenhuma aprovada ainda. Diga “Sim” nas ideias que quer gravar.</div>';
}
function showView(v){
  VIEW = v; ls.set('grana-painel-aba', v);
  for (const x of ['ideias', 'aprovadas', 'videos', 'refs']) document.getElementById('view-' + x).hidden = x !== v;
  document.querySelectorAll('.tab').forEach(t => t.setAttribute('aria-selected', t.dataset.view === v));
  document.getElementById('filters').hidden = v !== 'refs';
  renderAll();
}
function renderAll(){ renderFeed(); renderAprov(); renderVideos(); render(); }
document.querySelector('.tabs').addEventListener('click', e => { const t = e.target.closest('.tab'); if (t) showView(t.dataset.view); });
document.getElementById('undo').addEventListener('click', () => { if (undoFn) undoFn(); document.getElementById('toast').classList.remove('show'); });

async function gerarNova(rejeitada){
  if (!SERVER || !D.gerador) return;
  GERANDO++; renderFeed();
  try {
    const res = await api('/api/gerar', {rejeitada, ideias: AV});
    const ref = D.refs.find(r => r.id === res.ref_id);
    if (ref) ref.ideias.push(res.ideia);
    toast('✨ Ideia nova criada: ' + res.ideia.titulo, 3500);
  } catch (err) { toast('Não deu para criar ideia nova agora: ' + err.message, 4000); }
  finally { GERANDO--; renderAll(); }
}
document.getElementById('feed').addEventListener('click', e => {
  const b = e.target.closest('.big'); if (!b) return;
  const card = b.closest('.fcard'), id = card.dataset.id, v = b.dataset.v;
  setAv(id, {voto: v});
  card.classList.add(v === 'up' ? 'leave-up' : 'leave-down');
  const undo = () => { setAv(id, {voto: null}); renderAll(); };
  toast(v === 'up' ? '👍 Foi para Aprovadas' : '👎 Removida', 3200, undo);
  setTimeout(() => { renderAll(); const f = document.querySelector('#feed .fcard:nth-child(' + FEED_N + ')'); if (f) f.classList.add('enter'); }, 300);
  if (v === 'down') gerarNova(id);
});
document.getElementById('aprovadas').addEventListener('click', e => {
  const t = e.target.closest('[data-thumb]'); if (t) return abrirThumb(t.closest('.idea').dataset.id);
  const ro = e.target.closest('[data-roteiro]'); if (ro) return abrirRoteiro(ro.closest('.idea').dataset.id);
  const pb = e.target.closest('[data-pub]'); if (pb) { const id = pb.closest('.idea').dataset.id; return abrirPub((AV[id] || {}).url || '', id); }
  const b = e.target.closest('[data-tirar]'); if (!b) return;
  const id = b.closest('.idea').dataset.id; setAv(id, {voto: null}); renderAll(); toast('Voltou para a fila');
});
document.getElementById('aprovadas').addEventListener('change', e => {
  const box = e.target.closest('.idea'); if (!box) return; const id = box.dataset.id;
  if (e.target.classList.contains('status')) { setAv(id, {status: e.target.value}); box.querySelector('.url').hidden = e.target.value !== 'postado'; renderAprov(); }
  else if (e.target.classList.contains('url')) setAv(id, {url: e.target.value.trim()});
});

// thumbs
const tscrim = document.getElementById('tscrim'), tStatus = document.getElementById('t-status');
let TID = null, TDADOS = null;
function tMsg(html, cls=''){ tStatus.className = 'status ' + cls; tStatus.innerHTML = html; }
function renderThumb(){
  const img = D.imagem || {};
  const aviso = !img.ok ? img.motivo : (!img.fotos ? `Sem fotos do Léo: a pessoa sai genérica. Coloque 3–4 fotos dele (rosto bem visível) em ${img.pasta_fotos}` : '');
  document.getElementById('t-sub').textContent = aviso || `Imagens com o rosto do Léo a partir de ${img.fotos} foto(s) em ${img.pasta_fotos}.`;
  document.getElementById('t-grid').innerHTML = (TDADOS?.conceitos || []).map((c, n) => {
    const ims = (TDADOS.imagens || {})[n] || [];
    return `<div class="tcard"><span class="rank">Conceito ${n + 1} · ${esc(c.nome)}</span>
      <div class="ttxt">${c.texto_thumb ? '“' + esc(c.texto_thumb) + '”' : '(sem texto)'}</div>
      <dl><dt>Rosto</dt><dd>${esc(c.expressao)}</dd><dt>Cena</dt><dd>${esc(c.cena)}</dd><dt>Cores</dt><dd>${esc(c.cores)}</dd><dt>Por quê</dt><dd>${esc(c.por_que)}</dd></dl>
      <div class="imgs">${ims.slice().reverse().map(f => `<a href="/thumbs/${encodeURIComponent(f)}" download="${esc(f)}" title="Baixar"><img src="/thumbs/${encodeURIComponent(f)}" alt="Thumb gerada do conceito ${n + 1}"></a>`).join('')}</div>
      <button class="btn ${ims.length ? '' : 'primary'}" data-img="${n}" ${img.ok ? '' : 'disabled'}>${ims.length ? 'Gerar outra imagem' : 'Gerar imagem'}</button></div>`; }).join('');
}
async function thumbApi(acao, extra={}){ return (await api('/api/thumb', {id: TID, acao, ...extra})).thumb; }
async function abrirThumb(id){
  TID = id; TDADOS = null; tscrim.hidden = false;
  const idea = allIdeas().find(x => x.i.id === id);
  document.getElementById('t-tit').textContent = 'Thumb: ' + (idea ? idea.i.titulo : '');
  renderThumb();
  try {
    TDADOS = await thumbApi('ver');
    if (!TDADOS) { tMsg('<span class="spin"></span> O Claude está estudando as thumbs que funcionaram…'); TDADOS = await thumbApi('conceitos'); }
    tMsg(''); renderThumb();
  } catch (err) { tMsg(esc(err.message), 'err'); }
}
document.getElementById('t-fechar').addEventListener('click', () => { tscrim.hidden = true; refresh().catch(() => {}); });
tscrim.addEventListener('click', e => { if (e.target === tscrim) document.getElementById('t-fechar').click(); });
document.getElementById('t-novos').addEventListener('click', async e => {
  e.target.disabled = true; tMsg('<span class="spin"></span> Criando outros 3 conceitos…');
  try { TDADOS = await thumbApi('conceitos'); tMsg(''); renderThumb(); } catch (err) { tMsg(esc(err.message), 'err'); }
  finally { e.target.disabled = false; }
});
document.getElementById('t-grid').addEventListener('click', async e => {
  const b = e.target.closest('[data-img]'); if (!b) return;
  b.disabled = true; b.innerHTML = '<span class="spin"></span> Gerando (até 1 min)…';
  try { TDADOS = await thumbApi('imagem', {n: +b.dataset.img}); tMsg(''); renderThumb(); }
  catch (err) { tMsg(esc(err.message), 'err'); renderThumb(); }
});

// roteiro (pacote do vídeo)
const rscrim = document.getElementById('rscrim'), rStatus = document.getElementById('r-status');
let RID = null, RDADOS = null;
const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
function rMsg(html, cls=''){ rStatus.className = 'status ' + cls; rStatus.innerHTML = html; }
function renderRoteiro(){
  const p = RDADOS, corpo = document.getElementById('r-corpo');
  for (const id of ['r-copiar', 'r-baixar']) document.getElementById(id).hidden = !p || RMODO !== 'claude';
  if (!p) { corpo.innerHTML = ''; return; }
  document.getElementById('r-baixar').href = `/roteiros/${encodeURIComponent(RID)}.md`;
  document.getElementById('r-sub').textContent = `~${p.minutos_estimados} min de fala (${p.palavras} palavras; alvo ${p.minutos_alvo} min) · Promessa: ${p.promessa}`;
  const box = (t, itens, cls='conferir') => itens.length ? `<div class="${cls}"><b>${t}</b><ul style="margin:6px 0 0;padding-left:18px">${itens.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '';
  corpo.innerHTML = `<h4 style="margin:0">Títulos</h4><ol class="rtit">${p.titulos.map((t, n) =>
      `<li class="${n === p.titulo_recomendado ? 'rec' : ''}">${n === p.titulo_recomendado ? '⭐ ' : ''}${esc(t.titulo)}<small>${esc(t.por_que)}</small></li>`).join('')}</ol>
    ${box('⚠️ Conferir antes de gravar', p.avisos)}${box('Fatos para o Léo confirmar', p.a_conferir)}${box('Separar antes de gravar', p.preparar, 'hint')}
    ${p.secoes.map(s => `<div class="rsec"><h4><span class="t">${mmss(s.inicio_s)}</span>${esc(s.nome)}</h4><div class="obj">${esc(s.objetivo)}</div>
      <div class="fala">${esc(s.fala)}</div><div class="tela">🎬 ${esc(s.na_tela)}</div></div>`).join('')}`;
}
async function roteiroApi(acao){ return (await api('/api/roteiro', {id: RID, acao})).roteiro; }
async function gerarRoteiro(){
  rMsg('<span class="spin"></span> O Claude está escrevendo o roteiro (1–2 min)…');
  document.getElementById('r-refazer').disabled = true;
  try {
    RDADOS = await roteiroApi('gerar'); rMsg(''); renderRoteiro();
    if (((AV[RID] || {}).status || 'ideia') === 'ideia') setAv(RID, {status: 'roteiro'});
  } catch (err) { rMsg(esc(err.message), 'err'); }
  finally { document.getElementById('r-refazer').disabled = false; }
}
let RMODO = 'claude', RMEU = null;
function modoRoteiro(m){
  RMODO = m;
  document.querySelectorAll('#r-modo .tab').forEach(t => t.setAttribute('aria-selected', t.dataset.m === m));
  document.getElementById('r-claude').hidden = m !== 'claude';
  document.getElementById('r-meu').hidden = m !== 'meu';
  for (const id of ['r-refazer']) document.getElementById(id).hidden = m !== 'claude';
  document.getElementById('r-copiar').hidden = m !== 'claude' || !RDADOS;
  document.getElementById('r-baixar').hidden = m !== 'claude' || !RDADOS;
  if (m === 'meu' && !RMEU) carregarMeu();
}
document.getElementById('r-modo').addEventListener('click', e => { const t = e.target.closest('.tab'); if (t) modoRoteiro(t.dataset.m); });
async function carregarMeu(){
  try { RMEU = (await api('/api/roteiro', {id: RID, acao: 'ver_meu'})).roteiro; } catch (e) { RMEU = null; }
  if (RMEU) { document.getElementById('r-texto').value = RMEU.texto; renderRevisao(); }
}
function renderRevisao(){
  const r = RMEU, el = document.getElementById('r-rev');
  if (!r || !r.problemas) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="row" style="align-items:baseline;gap:12px"><span class="nota">${r.nota}/10</span><span>${esc(r.resumo)}</span></div>
    <p class="hint" style="margin:0">~${String(r.minutos_estimados).replace('.', ',')} min de fala (${r.palavras} palavras)</p>
    ${r.avisos.length ? `<div class="conferir"><b>⚠️ Regras do canal</b><ul style="margin:6px 0 0;padding-left:18px">${r.avisos.map(a => `<li>${esc(a)}</li>`).join('')}</ul></div>` : ''}
    <div class="prob"><b>Gancho sugerido (primeiros 15 s)</b><div class="para">${esc(r.gancho_sugerido)}</div></div>
    ${r.problemas.map(p => `<div class="prob"><b>${esc(p.em)} · ${esc(p.problema)}</b><div class="de">“${esc(p.trecho)}”</div><div class="para">${esc(p.sugestao)}</div></div>`).join('')}
    ${r.pontos_fortes.length ? `<div class="prob"><b>✅ O que já está bom</b><ul style="margin:0;padding-left:18px">${r.pontos_fortes.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}`;
}
document.getElementById('r-revisar').addEventListener('click', async e => {
  const st = document.getElementById('r-mstatus'), texto = document.getElementById('r-texto').value;
  e.target.disabled = true; st.className = 'status'; st.innerHTML = '<span class="spin"></span> Revisando (até 1 min)…';
  try { RMEU = (await api('/api/roteiro', {id: RID, acao: 'revisar', texto})).roteiro; st.textContent = ''; renderRevisao(); }
  catch (err) { st.className = 'status err'; st.textContent = err.message; }
  finally { e.target.disabled = false; }
});

// teleprompter
const tp = document.getElementById('tp'), tpScroll = document.getElementById('tp-scroll');
const TP = {wpm: +(ls.get('tp-wpm', 150)), fonte: +(ls.get('tp-fonte', 48)), espelho: ls.get('tp-espelho', false), rodando: false, palavras: 1, raf: 0, ult: 0, pos: 0, lock: null};
function tpSecoes(){
  if (RMODO === 'meu') {
    const t = document.getElementById('r-texto').value.trim();
    return t ? t.split(/\n\s*\n|\n(?=\S)/).map(x => x.trim()).filter(Boolean).map(f => ({nome: '', fala: f, na_tela: ''})) : [];
  }
  return (RDADOS && RDADOS.secoes) || [];
}
function tpAplicar(){
  tp.style.setProperty('--tpf', TP.fonte + 'px'); tp.classList.toggle('espelho', !!TP.espelho);
  document.getElementById('tp-espelho').setAttribute('aria-pressed', !!TP.espelho);
  document.getElementById('tp-vel').textContent = `${TP.wpm} palavras/min`;
  ls.set('tp-wpm', TP.wpm); ls.set('tp-fonte', TP.fonte); ls.set('tp-espelho', TP.espelho);
}
function tpTempo(){
  const max = tpScroll.scrollHeight - tpScroll.clientHeight, f = max > 0 ? TP.pos / max : 0;
  document.getElementById('tp-prog').style.width = (f * 100) + '%';
  const total = TP.palavras / TP.wpm * 60;
  document.getElementById('tp-tempo').textContent = `${mmss(Math.round(f * total))} / ${mmss(Math.round(total))}`;
}
function tpPasso(t){
  if (!TP.rodando) return;
  const dt = TP.ult ? (t - TP.ult) / 1000 : 0; TP.ult = t;
  const max = tpScroll.scrollHeight - tpScroll.clientHeight;
  TP.pos = Math.min(max, TP.pos + dt * (TP.wpm / 60) * (max / TP.palavras));
  tpScroll.scrollTop = TP.pos; tpTempo();
  if (TP.pos >= max) return tpPausar();
  TP.raf = requestAnimationFrame(tpPasso);
}
async function tpIniciar(){
  if (TP.rodando) return;
  const c = document.getElementById('tp-count');
  if (TP.pos === 0) { c.hidden = false; for (const n of [3, 2, 1]) { c.textContent = n; await new Promise(r => setTimeout(r, 700)); } c.hidden = true; }
  TP.rodando = true; TP.ult = 0; document.getElementById('tp-play').textContent = '❚❚ Pausar';
  try { TP.lock = await navigator.wakeLock?.request('screen'); } catch (e) {}
  TP.raf = requestAnimationFrame(tpPasso);
}
function tpPausar(){ TP.rodando = false; cancelAnimationFrame(TP.raf); document.getElementById('tp-play').textContent = '▶︎ Continuar'; TP.lock?.release?.().catch(() => {}); TP.lock = null; }
function abrirTp(){
  const sec = tpSecoes();
  if (!sec.length) { toast(RMODO === 'meu' ? 'Cole o seu roteiro primeiro' : 'Gere o roteiro primeiro'); return; }
  document.getElementById('tp-txt').innerHTML = sec.map(s => `${s.nome ? `<span class="tsec">${s.inicio_s != null ? mmss(s.inicio_s) + ' · ' : ''}${esc(s.nome)}</span>` : ''}${esc(s.fala).replace(/\n/g, '<br>')}${s.na_tela ? `<span class="tela">🎬 ${esc(s.na_tela)}</span>` : ''}`).join('<br>');
  TP.palavras = Math.max(1, sec.reduce((n, s) => n + s.fala.split(/\s+/).length, 0));
  TP.pos = 0; tp.hidden = false; tpScroll.scrollTop = 0; document.getElementById('tp-play').textContent = '▶︎ Iniciar';
  tpAplicar(); tpTempo();
  tp.requestFullscreen?.().catch(() => {});
}
function fecharTp(){ tpPausar(); tp.hidden = true; if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); }
document.getElementById('r-tp').addEventListener('click', abrirTp);
document.getElementById('tp-play').addEventListener('click', () => TP.rodando ? tpPausar() : tpIniciar());
document.getElementById('tp-scroll').addEventListener('click', () => TP.rodando ? tpPausar() : tpIniciar());
document.getElementById('tp-lento').addEventListener('click', () => { TP.wpm = Math.max(60, TP.wpm - 10); tpAplicar(); tpTempo(); });
document.getElementById('tp-rapido').addEventListener('click', () => { TP.wpm = Math.min(260, TP.wpm + 10); tpAplicar(); tpTempo(); });
document.getElementById('tp-menor').addEventListener('click', () => { TP.fonte = Math.max(24, TP.fonte - 6); tpAplicar(); });
document.getElementById('tp-maior').addEventListener('click', () => { TP.fonte = Math.min(110, TP.fonte + 6); tpAplicar(); });
document.getElementById('tp-espelho').addEventListener('click', () => { TP.espelho = !TP.espelho; tpAplicar(); });
document.getElementById('tp-sair').addEventListener('click', fecharTp);
document.addEventListener('keydown', e => {
  if (tp.hidden) return;
  const k = {' ': () => TP.rodando ? tpPausar() : tpIniciar(), Escape: fecharTp, ArrowUp: () => document.getElementById('tp-rapido').click(),
    ArrowDown: () => document.getElementById('tp-lento').click(), '+': () => document.getElementById('tp-maior').click(),
    '-': () => document.getElementById('tp-menor').click(), m: () => document.getElementById('tp-espelho').click()}[e.key];
  if (k) { e.preventDefault(); k(); }
});
tpScroll.addEventListener('wheel', e => { e.preventDefault(); const max = tpScroll.scrollHeight - tpScroll.clientHeight; TP.pos = Math.max(0, Math.min(max, TP.pos + e.deltaY)); tpScroll.scrollTop = TP.pos; tpTempo(); }, {passive: false});

async function abrirRoteiro(id){
  RID = id; RDADOS = null; RMEU = null; rscrim.hidden = false; renderRoteiro();
  document.getElementById('r-texto').value = ''; document.getElementById('r-rev').innerHTML = ''; modoRoteiro('claude');
  const idea = allIdeas().find(x => x.i.id === id);
  document.getElementById('r-tit').textContent = 'Roteiro: ' + (idea ? idea.i.titulo : '');
  document.getElementById('r-sub').textContent = '';
  try { RDADOS = await roteiroApi('ver'); if (RDADOS) { rMsg(''); renderRoteiro(); } else await gerarRoteiro(); }
  catch (err) { rMsg(esc(err.message), 'err'); }
}
document.getElementById('r-refazer').addEventListener('click', gerarRoteiro);
document.getElementById('r-fechar').addEventListener('click', () => { rscrim.hidden = true; refresh().catch(() => {}); });
rscrim.addEventListener('click', e => { if (e.target === rscrim) document.getElementById('r-fechar').click(); });
document.getElementById('r-copiar').addEventListener('click', async () => {
  const p = RDADOS; if (!p) return;
  const txt = p.secoes.map(s => `[${mmss(s.inicio_s)}] ${s.nome.toUpperCase()}\n${s.fala}\n(Na tela: ${s.na_tela})`).join('\n\n');
  try { await navigator.clipboard.writeText(txt); toast('Roteiro copiado'); } catch (e) { toast('Não deu para copiar; use “Baixar .md”'); }
});

// meus vídeos
const pct = x => x == null ? '—' : Math.round(x * 100) + '%';
function kpi(r, d, med){
  const j = r['d' + d]; if (!j) return `<div class="kpi"><b>${d} dias</b><span class="s">ainda não chegou</span></div>`;
  const m = (med || {})['d' + d] || {};
  const x = j.x_mediana != null ? `${j.x_mediana.toFixed(1).replace('.', ',')}x a média` : 'sem média ainda';
  return `<div class="kpi"><b>${d} dias${j.parcial ? ' · parcial' : ''}</b><span class="v">${fmt(j.views)}</span> views <span class="s">(${x})</span>
    <div class="s">CTR ${j.ctr != null ? j.ctr.toFixed(1).replace('.', ',') + '%' : '—'}${m.ctr ? ` · média ${m.ctr.toFixed(1).replace('.', ',')}%` : ''}</div>
    <div class="s">Assistido ${j.averageViewPercentage != null ? Math.round(j.averageViewPercentage) + '%' : '—'}${m.averageViewPercentage ? ` · média ${Math.round(m.averageViewPercentage)}%` : ''}</div>
    <div class="s">+${fmt(j.subscribersGained || 0)} inscritos</div></div>`;
}
function chart(r, media){
  const c = r.curva || []; if (c.length < 2) return '<p class="hint">Curva de retenção ainda não disponível.</p>';
  const W = innerWidth < 640 ? 340 : 600, H = innerWidth < 640 ? 200 : 190, L = 34, B = 22, T = 18, dur = r.duracao_s || 0;
  const ymax = Math.max(1, ...c.map(p => p[1]), ...(media || []).map(p => p[1]));
  const X = x => L + x * (W - L - 6), Y = y => T + (1 - y / ymax) * (H - T - B);
  const path = pts => pts.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join('');
  const grid = [0.25, 0.5, 0.75, 1].map(g => `<line class="grid" x1="${L}" x2="${W - 6}" y1="${Y(g)}" y2="${Y(g)}"/><text class="ax" x="${L - 6}" y="${Y(g) + 4}" text-anchor="end">${g * 100}%</text>`).join('');
  const xt = (W < 400 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1]).map(f => `<text class="ax" x="${X(f)}" y="${H - 6}" text-anchor="${f === 0 ? 'start' : f === 1 ? 'end' : 'middle'}">${mmss(Math.round(f * dur))}</text>`).join('');
  const qs = ((r.retencao || {}).quedas || []).map(q => { const y = (c.find(p => p[0] >= q.pos) || c[c.length - 1])[1];
    return `<circle class="queda" cx="${X(q.pos)}" cy="${Y(y)}" r="5"/><text class="qlab" x="${X(q.pos)}" y="${Y(y) - 10}" text-anchor="middle">${esc(q.em)}</text>`; }).join('');
  return `<div class="chart" data-vid="${esc(r.video_id)}"><div class="legend"><span><i></i>Este vídeo</span>${media && media.length ? '<span><i class="m"></i>Média do Léo</span>' : ''}<span><span style="color:var(--down)">●</span> queda forte</span></div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Retenção ao longo do vídeo: quanto do público ainda assistia em cada momento">${grid}${xt}
    ${media && media.length ? `<path class="l-media" d="${path(media)}"/>` : ''}<path class="l-este" d="${path(c)}"/>${qs}
    <line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/><rect x="${L}" y="0" width="${W - L}" height="${H}" fill="transparent"/></svg>
    <div class="tip" hidden></div></div>`;
}
let mvNarrow = innerWidth < 640;
addEventListener('resize', () => { if ((innerWidth < 640) !== mvNarrow) { mvNarrow = innerWidth < 640; renderVideos(); } });
function renderVideos(){
  const el = document.getElementById('meus-videos'), rel = D.meus_videos;
  document.getElementById('mv-atualizar').hidden = !SERVER;
  document.getElementById('mv-quando').textContent = rel ? `Atualizado em ${new Date(rel.atualizado_em).toLocaleString('pt-BR')}.` : '';
  if (!rel || !rel.videos.length) { el.innerHTML = `<div class="empty">${rel ? 'Nenhum vídeo longo recente.' : 'Ainda sem números. Clique em “Atualizar números”.'}</div>`; return; }
  el.innerHTML = rel.videos.map(r => { const li = (D.licoes || {})[r.video_id];
    const quedas = ((r.retencao || {}).quedas || []).map(q => `${esc(q.em)} (−${Math.round(q.queda * 100)} pts${q.secao ? ', seção “' + esc(q.secao) + '”' : ''})`).join(' · ');
    return `<div class="mv glass"><div class="mv-top${r.thumb ? '' : ' so'}">${r.thumb ? `<img src="${esc(r.thumb)}" alt="" loading="lazy">` : ''}
      <div><h4>${esc(r.titulo)}</h4><div class="hint">Publicado em ${esc(r.publicado.split('-').reverse().join('/'))} · ${r.idade_dias} dias${r.ideia_titulo ? ' · ideia: ' + esc(r.ideia_titulo) : ''}</div>
      <ul class="diag" style="margin-top:8px">${(r.diagnostico || []).map(d => `<li>${d.tipo === 'bom' ? '✅' : '⚠️'} ${esc(d.texto)}</li>`).join('')}</ul></div></div>
      <div class="kpis">${[2, 7, 30].map(d => kpi(r, d, rel.medianas)).join('')}</div>
      ${chart(r, rel.curva_media)}
      <p class="hint" style="margin:0">Ainda assistindo aos 30 s: <b>${pct((r.retencao || {}).ret_gancho)}</b> (média ${pct((rel.medianas || {}).ret_gancho)})${quedas ? ' · Onde mais saíram: ' + quedas : ''}${r.ideia_id && !r.tem_roteiro ? ' · Gere o roteiro pelo painel para ver em qual seção o público saiu.' : ''}</p>
      ${li ? `<div class="licao"><b>O que aprender</b><div>${esc(li.resumo)}</div>
        ${li.funcionou.length ? `<div>✅ Funcionou<ul>${li.funcionou.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
        ${li.mudar.length ? `<div>🔧 Mudar<ul>${li.mudar.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
        <div>📌 <b>Regra para os próximos:</b> ${esc(li.regra)}</div></div>` : ''}
      ${SERVER && D.gerador ? `<div class="row"><button class="btn" data-licao="${esc(r.video_id)}">${li ? 'Refazer a lição' : 'O que aprender com este vídeo'}</button><button class="btn" data-pubv="${esc(r.video_id)}" data-ideia="${esc(r.ideia_id || '')}">📦 Publicação</button></div>` : ''}</div>`; }).join('');
}
const mvStatus = document.getElementById('mv-status');
document.getElementById('mv-atualizar').addEventListener('click', async e => {
  e.target.disabled = true; mvStatus.className = 'status'; mvStatus.innerHTML = '<span class="spin"></span> Buscando no YouTube Analytics…';
  try { await api('/api/meus-videos', {acao: 'atualizar'}); await refresh(); mvStatus.textContent = ''; }
  catch (err) { mvStatus.className = 'status err'; mvStatus.textContent = err.message; }
  finally { e.target.disabled = false; }
});
document.getElementById('meus-videos').addEventListener('click', async e => {
  const pv = e.target.closest('[data-pubv]'); if (pv) return abrirPub('https://youtu.be/' + pv.dataset.pubv, pv.dataset.ideia || null);
  const b = e.target.closest('[data-licao]'); if (!b) return;
  b.disabled = true; b.innerHTML = '<span class="spin"></span> O Claude está lendo os números e a fala do vídeo…';
  try { await api('/api/meus-videos', {acao: 'licao', video_id: b.dataset.licao}); await refresh(); }
  catch (err) { toast(err.message, 6000); b.disabled = false; b.textContent = 'Tentar de novo'; }
});
document.getElementById('meus-videos').addEventListener('pointermove', e => {
  const box = e.target.closest('.chart'); if (!box) return;
  const svg = box.querySelector('svg'), tip = box.querySelector('.tip'), cross = box.querySelector('.cross');
  const r = (D.meus_videos.videos || []).find(v => v.video_id === box.dataset.vid); if (!r) return;
  const pt = svg.getBoundingClientRect(), W = svg.viewBox.baseVal.width, L = 34, f = Math.min(1, Math.max(0, ((e.clientX - pt.left) / pt.width * W - L) / (W - L - 6)));
  const near = arr => (arr || []).reduce((a, p) => Math.abs(p[0] - f) < Math.abs(a[0] - f) ? p : a, (arr || [[0, null]])[0]);
  const p = near(r.curva), m = near(D.meus_videos.curva_media);
  const x = L + p[0] * (W - L - 6); cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
  tip.hidden = false; tip.style.left = (x / W * 100) + '%'; tip.style.top = '30px';
  tip.innerHTML = `<b>${mmss(Math.round(p[0] * (r.duracao_s || 0)))}</b> · este vídeo ${pct(p[1])}${m && m[1] != null ? ` · média ${pct(m[1])}` : ''}`;
});
document.getElementById('meus-videos').addEventListener('pointerleave', () => {
  document.querySelectorAll('#meus-videos .tip').forEach(t => t.hidden = true);
  document.querySelectorAll('#meus-videos .cross').forEach(c => c.setAttribute('visibility', 'hidden'));
}, true);

// ideias dos comentários
document.getElementById('com-row').hidden = !(SERVER && D.gerador);
document.getElementById('btn-com').addEventListener('click', async e => {
  const st = document.getElementById('com-status');
  e.target.disabled = true; st.className = 'status'; st.innerHTML = '<span class="spin"></span> Lendo os comentários do canal e das referências (até 1 min)…';
  try { const r = await api('/api/comentarios', {}); await refresh(); st.className = 'status ok';
    st.textContent = r.novas ? `${r.novas} ideia(s) nova(s) pedidas pelo público, já na lista.` : 'Nenhuma dúvida nova que se repete. Tente de novo daqui a uns dias.'; }
  catch (err) { st.className = 'status err'; st.textContent = err.message; }
  finally { e.target.disabled = false; }
});

// material de publicação
const pscrim = document.getElementById('pscrim'), pStatus = document.getElementById('p-status');
let PIDEIA = null, PDADOS = null;
function pMsg(html, cls=''){ pStatus.className = 'status ' + cls; pStatus.innerHTML = html; }
function bloco(titulo, texto, extra=''){ return `<div class="pbloco"><div class="cab"><b>${titulo}</b><button class="btn" data-copiar="${esc(texto)}">Copiar</button></div><pre>${esc(texto)}</pre>${extra}</div>`; }
async function renderPub(){
  const p = PDADOS, corpo = document.getElementById('p-corpo'), bx = document.getElementById('p-baixar');
  bx.hidden = !p; if (!p) { corpo.innerHTML = ''; return; }
  bx.href = `/publicacao/${encodeURIComponent(p.video_id)}.txt`;
  let ab = '';
  if (p.ideia_id) {
    try { const t = (await api('/api/thumb', {id: p.ideia_id, acao: 'ver'})).thumb;
      const ims = t ? Object.values(t.imagens || {}).map(l => l[l.length - 1]).filter(Boolean) : [];
      ab = ims.length ? `<div class="pbloco"><b>Thumbs para o teste A/B</b><div class="abthumbs">${ims.map(f => `<a href="/thumbs/${encodeURIComponent(f)}" download><img src="/thumbs/${encodeURIComponent(f)}" alt="Thumb ${esc(f)}"></a>`).join('')}</div>
        <p class="hint" style="margin:0">No YouTube Studio: Detalhes do vídeo → Miniatura → <b>Testar e comparar</b> → suba ${ims.length > 1 ? 'estas ' + ims.length : 'esta e mais 2'}. O YouTube escolhe a que der mais tempo assistido.</p></div>`
        : `<div class="pbloco"><b>Thumbs para o teste A/B</b><p class="hint" style="margin:0">Gere as imagens no botão 🎨 Thumb da ideia (uma por conceito) e suba as 3 em <b>Testar e comparar</b> no YouTube Studio.</p></div>`;
    } catch (e) {}
  }
  corpo.innerHTML = (p.avisos.length ? `<div class="conferir">${p.avisos.map(esc).join('<br>')}</div>` : '') +
    bloco('Descrição (com capítulos, links e hashtags)', p.descricao_completa) +
    bloco('Comentário fixado', p.comentario_fixado) + bloco('Tags', p.tags.join(', ')) + ab;
}
async function abrirPub(link, ideia){
  PIDEIA = ideia; PDADOS = null; pscrim.hidden = false; pMsg(''); renderPub();
  document.getElementById('p-link').value = link || '';
  try { const r = await api('/api/publicacao', {acao: 'links'}); document.getElementById('p-links').value = r.links || '';
    document.getElementById('p-links-box').open = !r.links; } catch (e) {}
  if (link) { try { PDADOS = (await api('/api/publicacao', {acao: 'ver', video: link})).publicacao; renderPub(); } catch (e) {} }
}
document.getElementById('p-gerar').addEventListener('click', async e => {
  const link = document.getElementById('p-link').value.trim(); if (!link) { pMsg('Cole o link do vídeo.', 'err'); return; }
  e.target.disabled = true; pMsg('<span class="spin"></span> Lendo a legenda do vídeo e escrevendo (até 1 min)…');
  try { PDADOS = (await api('/api/publicacao', {acao: 'gerar', video: link, ideia: PIDEIA})).publicacao; pMsg(''); renderPub(); }
  catch (err) { pMsg(esc(err.message), 'err'); }
  finally { e.target.disabled = false; }
});
document.getElementById('p-salvar-links').addEventListener('click', async () => {
  try { await api('/api/publicacao', {acao: 'salvar_links', links: document.getElementById('p-links').value}); toast('Links salvos: entram na próxima descrição'); }
  catch (err) { toast(err.message); }
});
document.getElementById('p-corpo').addEventListener('click', async e => {
  const b = e.target.closest('[data-copiar]'); if (!b) return;
  try { await navigator.clipboard.writeText(b.dataset.copiar); toast('Copiado'); } catch (err) { toast('Não deu para copiar; use “Baixar .txt”'); }
});
document.getElementById('p-fechar').addEventListener('click', () => { pscrim.hidden = true; });
pscrim.addEventListener('click', e => { if (e.target === pscrim) pscrim.hidden = true; });

fillSelects(); showView(VIEW);
</script>
</body>
</html>
"""


def write_html(data, path, token=""):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(HTML.replace("__DATA__", payload).replace("__TOKEN__", token), encoding="utf-8")


def generate(cfg, token=""):
    o = out_dir()
    data = build_data(cfg)
    write_html(data, o / "painel.html", token)
    write_csv(data, o / "referencias.csv")
    write_ideas_md(data, o / "ideias.md")
    return data


# ---------------------------------------------------------------- servidor local (opcional)

def start_analysis(job, ids, cfg, token, lock, analisar=None):
    """Analisa `ids` numa thread (rede fora do lock; grava e refaz o painel a cada vídeo)."""
    import gerador
    analisar = analisar or gerador.analisar
    if job.get("rodando"):
        ids = [i for i in ids if i not in job["fila"]]
        job["fila"] += ids
        job["total"] += len(ids)
        return
    videos = read_json(R.paths()["videos"], {}) or {}
    job.update(rodando=True, fila=list(ids), total=len(ids), feitos=0, atual=0, ideias=0, erros=[], titulo="")

    def run():
        try:
            while job["fila"]:
                vid = job["fila"].pop(0)
                job["atual"] += 1
                job["titulo"] = (videos.get(vid) or {}).get("titulo", vid)
                try:
                    with lock:
                        ratings = (read_json(out_dir() / "avaliacoes.json", {}) or {}).get("ideias") or {}
                    a = analisar(vid, ratings, None, cfg)
                    job["ideias"] += len(a.get("ideias") or [])
                except Exception as e:
                    job["erros"].append(f"{type(e).__name__}: {e}"[:200])
                    log(f"  {vid}: {job['erros'][-1]}")
                    if type(e).__name__ in ("AuthenticationError", "PermissionDeniedError", "NotFoundError"):
                        job["fila"].clear()
                with lock:
                    R.recalc(cfg, quiet=True)
                    generate(cfg, token)
                job["feitos"] += 1
        finally:
            job["rodando"] = False

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def make_handler(cfg, token, lock, job=None):
    o = out_dir()
    job = job if job is not None else {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def do_GET(self):
            if self.path in ("/", "/index.html", "/painel.html"):
                return self._send(200, (o / "painel.html").read_bytes(), "text/html; charset=utf-8")
            if self.path.startswith("/thumbs/"):
                import thumbs
                nome = urllib.parse.unquote(self.path[len("/thumbs/"):])
                f = thumbs.pasta() / nome
                if not thumbs.ARQUIVO_OK.match(nome) or not f.is_file():
                    return self._send(404, {"ok": False, "erro": "não encontrado"})
                return self._send(200, f.read_bytes(), "image/png" if nome.endswith(".png") else "image/jpeg")
            if self.path.startswith("/roteiros/"):
                import roteiro
                import thumbs
                nome = urllib.parse.unquote(self.path[len("/roteiros/"):])
                f = roteiro.pasta() / nome
                if not (nome.endswith(".md") and thumbs.ID_OK.match(nome[:-3])) or not f.is_file():
                    return self._send(404, {"ok": False, "erro": "não encontrado"})
                return self._send(200, f.read_bytes(), "text/markdown; charset=utf-8")
            if self.path.startswith("/publicacao/"):
                import publicacao
                nome = urllib.parse.unquote(self.path[len("/publicacao/"):])
                f = publicacao.pasta() / nome
                if not (nome.endswith(".txt") and publicacao.ID_RE.match(nome[:-4])) or not f.is_file():
                    return self._send(404, {"ok": False, "erro": "não encontrado"})
                return self._send(200, f.read_bytes(), "text/plain; charset=utf-8")
            if self.path == "/api/dados":
                if not self._authorized():
                    return self._send(403, {"ok": False, "erro": "não autorizado"})
                with lock:
                    data = build_data(cfg)
                return self._send(200, {"ok": True, "dados": data})
            if self.path == "/api/status":
                if not self._authorized():
                    return self._send(403, {"ok": False, "erro": "não autorizado"})
                return self._send(200, {"ok": True, "job": {k: job.get(k, d) for k, d in (
                    ("rodando", False), ("total", 0), ("feitos", 0), ("atual", 0), ("ideias", 0),
                    ("erros", []), ("titulo", ""))}})
            return self._send(404, {"ok": False, "erro": "não encontrado"})

        def do_POST(self):
            if not self._authorized():
                return self._send(403, {"ok": False, "erro": "não autorizado (recarregue a página)"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(400, {"ok": False, "erro": "pedido inválido"})
            if self.path == "/api/avaliacoes":
                with lock:
                    merge_ratings(o, body.get("ideias"))
                return self._send(200, {"ok": True})
            if self.path == "/api/gerar":
                import gerador
                ok, motivo = gerador.disponivel()
                if not ok:
                    return self._send(200, {"ok": False, "erro": motivo})
                try:
                    with lock:
                        av = merge_ratings(o, body.get("ideias"))["ideias"]
                        ref_id, ideia = gerador.gerar(av, rejeitada_id=body.get("rejeitada"), cfg=cfg)
                        generate(cfg, token)
                    return self._send(200, {"ok": True, "ref_id": ref_id, "ideia": ideia})
                except ValueError as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except Exception as e:  # rede, chave inválida, limite da API
                    return self._send(200, {"ok": False, "erro": f"{type(e).__name__}: {e}"[:300]})
            if self.path == "/api/thumb":
                import thumbs
                iid, acao = str(body.get("id") or ""), body.get("acao")
                if not thumbs.ID_OK.match(iid):
                    return self._send(400, {"ok": False, "erro": "ideia inválida"})
                try:
                    if acao == "ver":
                        return self._send(200, {"ok": True, "thumb": read_json(thumbs.pasta() / f"{iid}.json", None)})
                    if acao == "conceitos":
                        import gerador
                        ok, motivo = gerador.disponivel()
                        if not ok:
                            return self._send(200, {"ok": False, "erro": motivo})
                        return self._send(200, {"ok": True, "thumb": thumbs.conceitos(iid, cfg=cfg)})
                    if acao == "imagem":
                        ok, motivo = thumbs.gemini_disponivel()
                        if not ok:
                            return self._send(200, {"ok": False, "erro": motivo})
                        thumbs.imagem(iid, int(body.get("n") or 0), cfg=cfg)
                        return self._send(200, {"ok": True, "thumb": read_json(thumbs.pasta() / f"{iid}.json", None)})
                    return self._send(400, {"ok": False, "erro": "ação inválida"})
                except ValueError as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except Exception as e:  # rede, chave, limite da API
                    return self._send(200, {"ok": False, "erro": f"{type(e).__name__}: {e}"[:300]})
            if self.path == "/api/roteiro":
                import roteiro
                import thumbs
                iid, acao = str(body.get("id") or ""), body.get("acao")
                if not thumbs.ID_OK.match(iid):
                    return self._send(400, {"ok": False, "erro": "ideia inválida"})
                try:
                    if acao == "ver":
                        return self._send(200, {"ok": True, "roteiro": read_json(roteiro.pasta() / f"{iid}.json", None)})
                    if acao == "gerar":
                        import gerador
                        ok, motivo = gerador.disponivel()
                        if not ok:
                            return self._send(200, {"ok": False, "erro": motivo})
                        return self._send(200, {"ok": True, "roteiro": roteiro.gerar(iid, cfg=cfg)})
                    if acao == "ver_meu":
                        return self._send(200, {"ok": True, "roteiro": read_json(roteiro.pasta() / f"{iid}-meu.json", None)})
                    if acao == "revisar":
                        import gerador
                        ok, motivo = gerador.disponivel()
                        if not ok:
                            return self._send(200, {"ok": False, "erro": motivo})
                        return self._send(200, {"ok": True, "roteiro": roteiro.revisar(iid, str(body.get("texto") or ""), cfg=cfg)})
                    return self._send(400, {"ok": False, "erro": "ação inválida"})
                except ValueError as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except Exception as e:  # rede, chave, limite da API
                    return self._send(200, {"ok": False, "erro": f"{type(e).__name__}: {e}"[:300]})
            if self.path == "/api/meus-videos":
                import postados
                try:
                    if body.get("acao") == "atualizar":
                        postados.atualizar(cfg=cfg)
                    elif body.get("acao") == "licao":
                        import gerador
                        ok, motivo = gerador.disponivel()
                        if not ok:
                            return self._send(200, {"ok": False, "erro": motivo})
                        postados.licao(str(body.get("video_id") or ""), cfg=cfg)
                    else:
                        return self._send(400, {"ok": False, "erro": "ação inválida"})
                    with lock:
                        generate(cfg, token)
                    return self._send(200, {"ok": True})
                except (ValueError, SystemExit) as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except Exception as e:  # rede, token do Google, limite da API
                    return self._send(200, {"ok": False, "erro": f"{type(e).__name__}: {e}"[:300]})
            if self.path == "/api/publicacao":
                import publicacao
                acao = body.get("acao")
                try:
                    if acao == "links":
                        return self._send(200, {"ok": True, "links": publicacao.links_fixos()})
                    if acao == "salvar_links":
                        publicacao.salvar_links(str(body.get("links") or "")[:3000])
                        return self._send(200, {"ok": True})
                    vid = publicacao.video_id(str(body.get("video") or ""))
                    if acao == "ver":
                        return self._send(200, {"ok": True, "publicacao": read_json(publicacao.pasta() / f"{vid}.json", None)})
                    if acao == "gerar":
                        import gerador
                        import thumbs
                        ok, motivo = gerador.disponivel()
                        if not ok:
                            return self._send(200, {"ok": False, "erro": motivo})
                        ideia = body.get("ideia")
                        ideia = ideia if ideia and thumbs.ID_OK.match(str(ideia)) else None
                        return self._send(200, {"ok": True, "publicacao": publicacao.gerar(vid, ideia, cfg=cfg)})
                    return self._send(400, {"ok": False, "erro": "ação inválida"})
                except ValueError as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except Exception as e:  # rede, chave, limite da API
                    return self._send(200, {"ok": False, "erro": f"{type(e).__name__}: {e}"[:300]})
            if self.path == "/api/comentarios":
                import comentarios
                import gerador
                ok, motivo = gerador.disponivel()
                if not ok:
                    return self._send(200, {"ok": False, "erro": motivo})
                try:
                    novos = comentarios.gerar(cfg=cfg)
                    with lock:
                        generate(cfg, token)
                    return self._send(200, {"ok": True, "novas": len(novos)})
                except (ValueError, SystemExit) as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except Exception as e:  # rede, token do Google, limite da API
                    return self._send(200, {"ok": False, "erro": f"{type(e).__name__}: {e}"[:300]})
            if self.path == "/api/adicionar":
                try:
                    with lock:
                        res = R.add_reference(body.get("entrada", ""), cfg, "en" if body.get("lang") == "en" else "pt")
                        generate(cfg, token)
                        import gerador
                        ids = (gerador.a_analisar(cfg, res.get("handle"), res.get("id") if res["tipo"] == "video" else None)
                               if res.get("handle") or res["tipo"] == "video" else [])
                    ok, motivo = gerador.disponivel()
                    if ids and ok:
                        start_analysis(job, ids, cfg, token, lock)
                        res["analisando"] = len(ids)
                    elif ids:
                        res["aviso"] = f"Para o Claude dar a nota e escrever as ideias sozinho: {motivo}"
                    return self._send(200, {"ok": True, **res})
                except ValueError as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except SystemExit as e:
                    return self._send(200, {"ok": False, "erro": str(e)})
                except Exception as e:  # rede, cota da API etc.
                    return self._send(200, {"ok": False, "erro": f"Não consegui buscar agora: {e}"})
            return self._send(404, {"ok": False, "erro": "não encontrado"})

    return Handler


def refresh_meus_videos(cfg, token, lock, max_horas=12):
    """Atualiza os números dos vídeos do Léo em segundo plano se tiverem mais de `max_horas` (abre o painel na hora)."""
    import os
    import postados
    from yt_common import load_env, parse_date
    load_env()
    if not os.environ.get("GOOGLE_REFRESH_TOKEN"):
        return None
    rel = read_json(postados.arquivo(), None)
    if rel and (now() - parse_date(rel["atualizado_em"])).total_seconds() < max_horas * 3600:
        return None

    def run():
        try:
            postados.atualizar(cfg=cfg)
            with lock:
                generate(cfg, token)
        except (Exception, SystemExit) as e:  # sem rede/token: fica com os números anteriores
            log(f"Meus vídeos: não atualizou agora ({e})")
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def serve(cfg, port=8765, open_browser=True):
    token = secrets.token_urlsafe(24)
    generate(cfg, token)
    lock = threading.Lock()
    refresh_meus_videos(cfg, token, lock)
    handler = make_handler(cfg, token, lock)
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit("Nenhuma porta livre entre %d e %d." % (port, port + 19))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    log(f"Painel aberto em {url} — deixe esta janela aberta; Ctrl+C para fechar.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        generate(cfg)  # painel.html em modo arquivo volta a funcionar sem servidor
    return url


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--abrir", action="store_true", help="abre com servidor local (salva sozinho e adiciona na hora)")
    ap.add_argument("--porta", type=int, default=8765)
    args = ap.parse_args(argv)
    cfg = R.config()
    o = out_dir()
    import_downloaded_ratings(o)
    process_saved_queue(cfg, o)
    if args.abrir:
        serve(cfg, args.porta)
        return
    data = generate(cfg)
    n_ideias = sum(len(r["ideias"]) for r in data["refs"])
    log(f"Painel: {o / 'painel.html'} — {len(data['refs'])} referências, {n_ideias} ideias, "
        f"{len(data['temas'])} temas validados")
    log(f"Também: {o / 'referencias.csv'} e {o / 'ideias.md'}")


if __name__ == "__main__":
    main()
