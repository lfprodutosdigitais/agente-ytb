"""FASE 3 — Painel: gera ./referencias/painel.html, referencias.csv e ideias.md.

Uso: python3 scripts/painel.py
- painel.html abre direto no navegador (sem servidor). Avaliações (👍/👎, motivo, status, URL do vídeo postado)
  ficam no navegador; o botão "Exportar avaliações" baixa avaliacoes.json.
- Na rodada seguinte, este script (e o aprendizado) pega o avaliacoes.json mais novo de ~/Downloads e copia
  para ./referencias/avaliacoes.json.
"""
import csv
import json
import shutil
from pathlib import Path

from yt_common import log, now, out_dir, read_json

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
        })
    avaliacoes = read_json(import_downloaded_ratings(o), {}) or {}
    return {"gerado_em": now().isoformat(), "temas": temas, "refs": refs,
            "avaliacoes": avaliacoes.get("ideias", avaliacoes), "cortes": {"longo": cfg["corte_longo"], "short": cfg["corte_short"]}}


def prioridade(r):
    return (r["score"] or 0) * (r["relevancia"] or 0) * (1.25 if r["tema_validado"] else 1)


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
    L = ["# Ideias", "", f"_Gerado em {now():%Y-%m-%d}. Ordem: score × relevância (tema validado vale 25% a mais)._", ""]
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Painel de Referências — Grana Própria</title>
<style>
:root{--bg:#f6f7f4;--card:#ffffff;--fg:#1b211c;--muted:#5d665c;--line:#dde2d8;--accent:#1f7a4a;--accent-soft:#e3f1e7;
--hot:#c2410c;--hot-soft:#fdebe1;--warn:#8a6100;--warn-soft:#fbf1d6;--chip:#eef1ea;--up:#1f7a4a;--down:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#111512;--card:#181e19;--fg:#e7ece5;--muted:#9aa597;--line:#2a332b;
--accent:#6fcf97;--accent-soft:#1b2d21;--hot:#fb923c;--hot-soft:#33200f;--warn:#f2c94c;--warn-soft:#2e2711;--chip:#212a22;
--up:#6fcf97;--down:#f87171;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px}
@media (max-width:700px){header{position:static}.filters label{flex:1 1 40%}.filters select,.filters input{width:100%}}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:20px;margin:0 0 8px}
h2{font-size:17px;margin:28px 0 12px;display:flex;flex-wrap:wrap;gap:4px 8px;align-items:baseline}
h2 small{color:var(--muted);font-weight:400;font-size:13px}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.filters label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:2px}
select,input,button,textarea{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
button{cursor:pointer}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
@media (prefers-color-scheme:dark){button.primary{color:#0d1a12}}
main{padding:0 16px 60px}
.temas{display:flex;flex-wrap:wrap;gap:8px}
.tema{background:var(--accent-soft);border-radius:10px;padding:8px 12px}
.tema b{display:block}
.tema span{font-size:12px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr));gap:14px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;min-width:0}
.thumb{display:block;aspect-ratio:16/9;background:var(--chip);position:relative}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.badge{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.72);color:#fff;font-weight:700;border-radius:6px;padding:2px 8px;font-size:13px}
.badge.hot{left:auto;right:8px;background:var(--hot);color:#fff}
.body{padding:12px;display:flex;flex-direction:column;gap:8px;min-width:0}
.meta{display:flex;flex-wrap:wrap;gap:6px;font-size:12px}
.chip{background:var(--chip);border-radius:999px;padding:2px 8px}
.chip.val{background:var(--accent-soft);color:var(--accent);font-weight:600}
.chip.rel{font-weight:600}
.title{font-weight:600;color:inherit;text-decoration:none}
.title:hover{text-decoration:underline}
.hook{font-style:italic;color:var(--muted);border-left:3px solid var(--line);padding-left:8px;font-size:14px}
details{font-size:14px}
details summary{cursor:pointer;color:var(--muted)}
.idea{border-top:1px dashed var(--line);padding-top:10px;display:flex;flex-direction:column;gap:6px}
.idea h4{margin:0;font-size:15px}
.idea .id{font-size:11px;color:var(--muted);font-family:ui-monospace,Menlo,monospace}
.idea ul{margin:0;padding-left:18px;font-size:14px}
.conferir{background:var(--warn-soft);color:var(--warn);border-radius:8px;padding:6px 8px;font-size:13px}
.actions{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.vote{min-width:44px}
.vote.on.up{background:var(--up);color:#fff;border-color:var(--up)}
.vote.on.down{background:var(--down);color:#fff;border-color:var(--down)}
.actions input,.actions select{flex:1 1 140px;min-width:0}
.empty{color:var(--muted);padding:12px 0}
.count{font-size:12px;color:var(--muted)}
.toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--fg);color:var(--bg);padding:8px 14px;border-radius:8px;opacity:0;transition:opacity .2s}
.toast.show{opacity:1}
</style>
</head>
<body>
<header><div class="wrap">
  <h1>Painel de Referências — Grana Própria</h1>
  <div class="filters">
    <label>Canal<select id="f-canal"><option value="">Todos</option></select></label>
    <label>Tema<select id="f-tema"><option value="">Todos</option></select></label>
    <label>Formato<select id="f-formato"><option value="">Todos</option><option value="longo">Longo</option><option value="short">Short</option></select></label>
    <label>Score mínimo<input id="f-score" type="number" min="0" step="0.5" value="0" style="width:90px"></label>
    <label>Relevância mínima<select id="f-rel"><option value="0">Todas</option><option value="3" selected>3+</option><option value="4">4+</option><option value="5">5</option></select></label>
    <label>Mostrar<select id="f-ideias"><option value="">Todas as referências</option><option value="com">Só com ideias</option></select></label>
    <label>&nbsp;<button class="primary" id="exportar">Exportar avaliações</button></label>
    <label>&nbsp;<button id="importar">Importar…</button></label>
    <input type="file" id="arquivo" accept=".json" hidden>
  </div>
</div></header>
<main class="wrap">
  <h2>Temas validados <small>mesmo tema outlier em 2+ canais nos últimos 90 dias</small></h2>
  <div class="temas" id="temas"></div>
  <h2>Em alta <small>crescendo 3x mais rápido que vídeos de idade parecida do mesmo canal</small><span class="count" id="n-alta"></span></h2>
  <div class="grid" id="alta"></div>
  <h2>Referências <small>ordem: score × relevância</small><span class="count" id="n-refs"></span></h2>
  <div class="grid" id="refs"></div>
</main>
<div class="toast" id="toast"></div>
<script id="dados" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('dados').textContent);
const KEY = 'grana-painel-avaliacoes';
const STATUS = ['ideia','roteiro','gravado','postado'];
let AV = {};
try { AV = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { AV = {}; }
// avaliações vindas do arquivo entram se forem mais novas que as do navegador
for (const [id, a] of Object.entries(D.avaliacoes || {})) {
  if (!AV[id] || (a.atualizado || '') > (AV[id].atualizado || '')) AV[id] = a;
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(AV)); } catch (e) {} }
function toast(t) { const el = document.getElementById('toast'); el.textContent = t; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 1800); }
function setAv(id, patch) { AV[id] = {...(AV[id] || {}), ...patch, atualizado: new Date().toISOString()}; save(); }
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const prio = r => (r.score || 0) * (r.relevancia || 0) * (r.tema_validado ? 1.25 : 1);

function fillSelect(id, values) {
  const s = document.getElementById(id);
  [...new Set(values.filter(Boolean))].sort().forEach(v => s.insertAdjacentHTML('beforeend', `<option>${esc(v)}</option>`));
}
fillSelect('f-canal', D.refs.map(r => r.canal));
fillSelect('f-tema', D.refs.map(r => r.tema));

function ideaHTML(i) {
  const a = AV[i.id] || {};
  const st = a.status || 'ideia';
  return `<div class="idea" data-id="${esc(i.id)}">
    <span class="id">${esc(i.id)}</span>
    <h4>${esc(i.titulo)}</h4>
    <details><summary>Gancho e tópicos</summary>
      <div class="hook">${esc(i.gancho)}</div>
      <ul>${(i.topicos || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>
    </details>
    ${i.a_conferir ? `<div class="conferir">⚠️ Conferir: ${esc(i.a_conferir)}</div>` : ''}
    <div class="actions">
      <button class="vote up ${a.voto === 'up' ? 'on' : ''}" data-v="up" title="Gostei">👍</button>
      <button class="vote down ${a.voto === 'down' ? 'on' : ''}" data-v="down" title="Não gostei">👎</button>
      <input class="motivo" placeholder="motivo (opcional)" value="${esc(a.motivo)}">
    </div>
    <div class="actions">
      <select class="status">${STATUS.map(s => `<option ${s === st ? 'selected' : ''}>${s}</option>`).join('')}</select>
      <input class="url" placeholder="URL do meu vídeo (quando postado)" value="${esc(a.url)}" ${st === 'postado' ? '' : 'hidden'}>
    </div>
  </div>`;
}

function cardHTML(r) {
  return `<article class="card">
    <a class="thumb" href="${esc(r.url)}" target="_blank" rel="noopener">
      <img loading="lazy" src="${esc(r.thumb || ('https://i.ytimg.com/vi/' + r.id + '/hqdefault.jpg'))}" alt="" onerror="this.style.visibility='hidden'">
      <span class="badge">${r.score}x</span>
      ${r.em_alta ? `<span class="badge hot">em alta ${r.velocidade_x ? r.velocidade_x + 'x' : ''}</span>` : ''}
    </a>
    <div class="body">
      <div class="meta">
        <span class="chip">${esc(r.canal)}${r.idioma === 'en' ? ' · EN' : ''}</span>
        <span class="chip rel">relevância ${r.relevancia ?? '—'}/5</span>
        ${r.tema ? `<span class="chip ${r.tema_validado ? 'val' : ''}">${esc(r.tema)}${r.tema_validado ? ' ✓' : ''}</span>` : ''}
        <span class="chip">${esc(r.data)} · ${(r.views || 0).toLocaleString('pt-BR')} views</span>
      </div>
      <a class="title" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.titulo)}</a>
      ${r.gancho ? `<div class="hook">“${esc(r.gancho)}”</div>` : ''}
      ${r.clique || r.assistiram || r.estrutura.length ? `<details><summary>Por que funcionou</summary>
        ${r.clique ? `<p><b>Por que clicaram:</b> ${esc(r.clique)}</p>` : ''}
        ${r.assistiram ? `<p><b>Por que assistiram:</b> ${esc(r.assistiram)}</p>` : ''}
        ${r.estrutura.length ? `<p><b>Estrutura:</b></p><ul>${r.estrutura.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
      </details>` : (r.motivo ? `<div class="count">${esc(r.motivo)}</div>` : '')}
      ${r.ideias.length ? `<div class="count">${r.ideias.length} ideias para o seu canal</div>` : ''}
      ${r.ideias.map(ideaHTML).join('')}
    </div>
  </article>`;
}

function filtered() {
  const c = document.getElementById('f-canal').value, t = document.getElementById('f-tema').value,
        f = document.getElementById('f-formato').value, s = parseFloat(document.getElementById('f-score').value) || 0,
        rel = parseInt(document.getElementById('f-rel').value), com = document.getElementById('f-ideias').value;
  return D.refs.filter(r => (!c || r.canal === c) && (!t || r.tema === t) && (!f || r.formato === f)
    && (r.score || 0) >= s && (rel === 0 || (r.relevancia || 0) >= rel) && (!com || r.ideias.length));
}

function render() {
  const temas = document.getElementById('temas');
  temas.innerHTML = D.temas.length ? D.temas.map(t => `<div class="tema"><b>${esc(t.tema)}</b><span>${t.canais.map(esc).join(' · ')} · ${t.videos.length} vídeos</span></div>`).join('')
    : '<div class="empty">Nenhum ainda: aparecem quando o mesmo tema vira outlier em 2+ canais.</div>';
  const list = filtered();
  const alta = list.filter(r => r.em_alta).sort((a, b) => (b.velocidade_x || 0) - (a.velocidade_x || 0));
  const refs = list.filter(r => !r.em_alta).sort((a, b) => prio(b) - prio(a));
  document.getElementById('alta').innerHTML = alta.map(cardHTML).join('') || '<div class="empty">Nada em alta com esses filtros.</div>';
  document.getElementById('refs').innerHTML = refs.map(cardHTML).join('') || '<div class="empty">Nada com esses filtros.</div>';
  document.getElementById('n-alta').textContent = `(${alta.length})`;
  document.getElementById('n-refs').textContent = `(${refs.length})`;
}

document.querySelector('.filters').addEventListener('input', e => { if (e.target.id && e.target.id.startsWith('f-')) render(); });
document.querySelector('main').addEventListener('click', e => {
  const b = e.target.closest('.vote'); if (!b) return;
  const box = b.closest('.idea'), id = box.dataset.id, v = b.dataset.v;
  const cur = (AV[id] || {}).voto;
  setAv(id, {voto: cur === v ? null : v});
  box.querySelectorAll('.vote').forEach(x => x.classList.toggle('on', x.dataset.v === AV[id].voto));
});
document.querySelector('main').addEventListener('change', e => {
  const box = e.target.closest('.idea'); if (!box) return;
  const id = box.dataset.id;
  if (e.target.classList.contains('status')) {
    setAv(id, {status: e.target.value});
    box.querySelector('.url').hidden = e.target.value !== 'postado';
  } else if (e.target.classList.contains('motivo')) setAv(id, {motivo: e.target.value});
  else if (e.target.classList.contains('url')) setAv(id, {url: e.target.value.trim()});
});

document.getElementById('exportar').addEventListener('click', () => {
  const ideias = {};
  for (const r of D.refs) for (const i of r.ideias) if (AV[i.id]) ideias[i.id] = {...AV[i.id], titulo: i.titulo, referencia: r.id, canal: r.canal, tema: r.tema};
  for (const [id, a] of Object.entries(AV)) if (!ideias[id]) ideias[id] = a;
  const blob = new Blob([JSON.stringify({exportado_em: new Date().toISOString(), ideias}, null, 2)], {type: 'application/json'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'avaliacoes.json';
  document.body.appendChild(a); a.click(); a.remove();
  toast(`${Object.keys(ideias).length} avaliações exportadas para Downloads`);
});
document.getElementById('importar').addEventListener('click', () => document.getElementById('arquivo').click());
document.getElementById('arquivo').addEventListener('change', async e => {
  const f = e.target.files[0]; if (!f) return;
  try {
    const j = JSON.parse(await f.text());
    for (const [id, a] of Object.entries(j.ideias || j)) if (!AV[id] || (a.atualizado || '') >= (AV[id].atualizado || '')) AV[id] = a;
    save(); render(); toast('Avaliações importadas');
  } catch (err) { toast('Arquivo inválido'); }
});
render();
</script>
</body>
</html>
"""


def write_html(data, path):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")


def main():
    cfg = R.config()
    o = out_dir()
    data = build_data(cfg)
    write_html(data, o / "painel.html")
    write_csv(data, o / "referencias.csv")
    write_ideas_md(data, o / "ideias.md")
    n_ideias = sum(len(r["ideias"]) for r in data["refs"])
    log(f"Painel: {o / 'painel.html'} — {len(data['refs'])} referências, {n_ideias} ideias, "
        f"{len(data['temas'])} temas validados")
    log(f"Também: {o / 'referencias.csv'} e {o / 'ideias.md'}")


if __name__ == "__main__":
    main()
