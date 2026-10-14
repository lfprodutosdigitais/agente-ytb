"""Thumbnails para uma ideia aprovada.

1. Conceito (Claude): olha as thumbs que funcionaram (a referência da ideia, outros outliers do mesmo tema e as
   melhores do Léo) e propõe 3 conceitos: texto da thumb, expressão, cena, cores e o prompt da imagem.
2. Imagem (Gemini, opcional): gera a thumb 16:9 de um conceito, com o rosto do Léo a partir das fotos em
   ~/GranaPropria/fotos-leo/. Precisa de GEMINI_API_KEY no .env (aistudio.google.com → Get API key).

Saída: ./referencias/thumbs/{ideia_id}.json (conceitos) e {ideia_id}-{n}-{k}.png/.jpg (imagens).
"""
import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from yt_common import HttpError, http, load_env, log, now, out_dir, read_json, write_json

import referencias as R

MODELO_IMAGEM = "gemini-3-pro-image-preview"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
FOTO_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
            ".heic": "image/heic", ".heif": "image/heif"}
MAX_FOTOS = 4
ARQUIVO_OK = re.compile(r"^[\w-]+(-\d+)*\.(png|jpg)$")
ID_OK = re.compile(r"^[\w-]{1,80}$")

_CONCEITO = {"type": "object", "properties": {
    "nome": {"type": "string"},
    "texto_thumb": {"type": "string"},
    "expressao": {"type": "string"},
    "cena": {"type": "string"},
    "cores": {"type": "string"},
    "por_que": {"type": "string"},
    "prompt_imagem": {"type": "string"}},
    "required": ["nome", "texto_thumb", "expressao", "cena", "cores", "por_que", "prompt_imagem"],
    "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"conceitos": {"type": "array", "items": _CONCEITO}},
          "required": ["conceitos"], "additionalProperties": False}


def pasta():
    return out_dir() / "thumbs"


def pasta_fotos(cfg=None):
    cfg = cfg or R.config()
    return Path(os.path.expanduser(cfg.get("pasta_fotos") or str(out_dir().resolve().parent / "fotos-leo")))


def fotos(cfg=None):
    p = pasta_fotos(cfg)
    if not p.is_dir():
        return []
    return sorted(f for f in p.iterdir() if f.suffix.lower() in FOTO_EXT)[:MAX_FOTOS]


def gemini_disponivel():
    load_env()
    if not os.environ.get("GEMINI_API_KEY"):
        return False, "Falta a chave GEMINI_API_KEY no .env da skill (aistudio.google.com → Get API key)."
    return True, ""


def achar_ideia(ideia_id):
    """(ref_id, análise, ideia, vídeo de referência)."""
    for vid, a in R.load_analyses().items():
        for i in a.get("ideias") or []:
            if i.get("id") == ideia_id:
                v = (read_json(R.paths()["videos"], {}) or {}).get(vid, {})
                return vid, a, i, v
    raise ValueError("Ideia não encontrada. Atualize o painel.")


def baixar(url):
    """Bytes da imagem (com cache em disco); None se falhar."""
    if not url:
        return None
    c = out_dir() / "cache" / "thumbs" / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg")
    if c.exists():
        return c.read_bytes()
    try:
        data = http("GET", url, raw=True, timeout=30)
    except Exception as e:  # thumb fora do ar não impede o conceito
        log(f"  thumb {url}: {e}")
        return None
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_bytes(data)
    return data


def exemplos(ref_id, a, v):
    """[(legenda, url)]: a referência da ideia, até 3 outliers do mesmo tema e as 2 melhores do Léo."""
    out = [(f"REFERÊNCIA desta ideia ({v.get('canal')}, {v.get('outlier_score') or '?'}x a média do canal): "
            f"“{v.get('titulo')}”", v.get("thumbnail"))]
    videos = read_json(R.paths()["videos"], {}) or {}
    analyses = R.load_analyses()
    mesmos = [x for vid, x in videos.items() if vid != ref_id and x.get("outlier")
              and (analyses.get(vid) or {}).get("tema") == a.get("tema") and x.get("thumbnail")]
    for x in sorted(mesmos, key=lambda x: -(x.get("score") or 0))[:3]:
        out.append((f"Outlier do mesmo tema ({x.get('canal')}, {x.get('outlier_score')}x): “{x.get('titulo')}”",
                    x["thumbnail"]))
    meus = (read_json(out_dir() / "meu-canal-dados.json", {}) or {}).get("videos") or []
    for x in sorted((m for m in meus if m.get("thumbnail")), key=lambda m: -(m.get("views") or 0))[:2]:
        out.append((f"Thumb do LÉO que mais deu views ({x.get('views')} views): “{x.get('titulo')}”", x["thumbnail"]))
    return out


def pedido(ideia, ref_imgs, tem_fotos):
    blocos = []
    for legenda, data in ref_imgs:
        blocos.append({"type": "text", "text": legenda})
        blocos.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(data).decode()}})
    blocos.append({"type": "text", "text": "\n".join([
        "## Ideia aprovada pelo Léo (o vídeo que ele vai gravar)",
        f"Título: {ideia['titulo']}", f"Gancho: {ideia.get('gancho', '')}",
        "## Tarefa",
        "Olhe as thumbs acima: o que faz as referências serem clicadas (contraste, rosto, número, objeto, pouco "
        "texto) e o estilo que o Léo já usa. Proponha 3 conceitos de thumbnail BEM diferentes entre si para essa "
        "ideia, pensando no CLT que rola a página inicial do YouTube no celular:",
        "- texto_thumb: no máximo 4 palavras em português, que COMPLETAM o título (não repetem); vazio se a "
        "imagem fala sozinha. Número em R$ quando fizer sentido.",
        "- expressao: a cara do Léo (ex.: sério olhando para a câmera, surpreso apontando para o número).",
        "- cena: o que aparece e onde (Léo à direita, planilha com R$ 35 mil à esquerda...). Nada de logo de marca.",
        "- cores: 2–3 cores com contraste alto; por_que: 1 frase ligando às thumbs que funcionaram.",
        "- prompt_imagem: em INGLÊS, descrição completa da cena para um gerador de imagem (enquadramento, luz, "
        "fundo, objetos, cores, estilo foto realista de YouTube thumbnail). O texto em português vai literal entre "
        "aspas no prompt. " + ("Descreva a pessoa como 'the man from the reference photos'."
                               if tem_fotos else "Descreva a pessoa como 'a Brazilian man in his late 20s'."),
    ])})
    return blocos


def conceitos(ideia_id, client=None, cfg=None):
    """Gera 3 conceitos e grava em thumbs/{ideia_id}.json (mantém imagens já geradas)."""
    import gerador
    cfg = cfg or R.config()
    ref_id, a, ideia, v = achar_ideia(ideia_id)
    refs = [(leg, d) for leg, d in ((leg, baixar(u)) for leg, u in exemplos(ref_id, a, v)) if d]
    r, resp = gerador._chamar(client, cfg, SCHEMA, pedido(ideia, refs, bool(fotos(cfg))))
    dest = pasta() / f"{ideia_id}.json"
    salvo = read_json(dest, {}) or {}
    salvo.update(ideia_id=ideia_id, titulo=ideia["titulo"], gerado_em=now().isoformat(),
                 conceitos=[{k: c[k].strip() for k in _CONCEITO["properties"]} for c in r["conceitos"][:3]],
                 imagens={})
    write_json(dest, salvo)
    u = resp.usage
    log(f"Conceitos de thumb ({ideia_id}) — {len(refs)} thumbs de referência; tokens: {u.input_tokens} entrada, "
        f"{u.output_tokens} saída")
    return salvo


def _foto_bytes(f):
    """Foto reduzida para ~1024 px (iPhone tira fotos de 5 MB). No Mac usa o `sips`; fora dele manda como está."""
    if shutil.which("sips"):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "f.jpg"
            r = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "1024", str(f), "--out", str(dest)],
                               capture_output=True)
            if r.returncode == 0 and dest.exists():
                return dest.read_bytes(), "image/jpeg"
    return f.read_bytes(), FOTO_EXT[f.suffix.lower()]


def prompt_final(c, tem_fotos):
    texto = c.get("texto_thumb", "").strip()
    return "\n".join([
        "Create a YouTube thumbnail, 16:9, photorealistic, high contrast, sharp focus, readable on a phone screen.",
        c["prompt_imagem"],
        (f'The only text in the image is "{texto}" in Brazilian Portuguese, huge bold sans-serif letters with a thick '
         "outline, spelled exactly like that." if texto else "No text in the image."),
        f"Facial expression: {c['expressao']}. Colors: {c['cores']}.",
        ("Use the attached photos ONLY as identity reference: the man in the thumbnail must be the same person, "
         "with the same face, hair and skin tone." if tem_fotos else ""),
        "No logos, no watermarks, no YouTube interface elements.",
    ]).strip()


def chamar_gemini(prompt, fotos_bytes, cfg):
    load_env()
    partes = [{"text": prompt}] + [{"inline_data": {"mime_type": m, "data": base64.b64encode(b).decode()}}
                                   for b, m in fotos_bytes]
    corpo = {"contents": [{"role": "user", "parts": partes}],
             "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                  "imageConfig": {"aspectRatio": "16:9", "imageSize": cfg.get("tamanho_imagem", "2K")}}}
    modelo = cfg.get("modelo_imagem", MODELO_IMAGEM)
    try:
        r = http("POST", GEMINI.format(modelo), data=corpo, timeout=180,
                 headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    except HttpError as e:
        msg = {400: "a chave GEMINI_API_KEY é inválida ou o pedido foi recusado",
               403: "a chave não tem acesso a esse modelo",
               404: f"o modelo {modelo} não existe mais; troque `modelo_imagem` no config.json",
               429: "limite ou crédito do Google acabou (o modelo de imagem pede faturamento ativo no AI Studio)"}
        raise ValueError(f"Gemini: {msg.get(e.status, f'erro {e.status}')}. Detalhe: {e.body[:200]}") from None
    for cand in r.get("candidates") or []:
        for p in (cand.get("content") or {}).get("parts") or []:
            d = p.get("inlineData") or p.get("inline_data")
            if d and d.get("data"):
                return base64.b64decode(d["data"])
    motivo = ((r.get("candidates") or [{}])[0].get("finishReason") or (r.get("promptFeedback") or {}).get("blockReason")
              or "sem imagem na resposta")
    raise ValueError(f"O Gemini não devolveu imagem ({motivo}). Tente de novo ou mude o conceito.")


def _jpg_youtube(png):
    """Cópia 1280x720 em JPG (o YouTube aceita até 2 MB) usando o `sips` do Mac; None fora do Mac."""
    if not shutil.which("sips"):
        return None
    jpg = png.with_suffix(".jpg")
    r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", "-z", "720", "1280",
                        str(png), "--out", str(jpg)], capture_output=True)
    return jpg if r.returncode == 0 and jpg.exists() else None


def imagem(ideia_id, n, cfg=None, gemini=None):
    """Gera a imagem do conceito n (0–2). Devolve o nome do arquivo para mostrar/baixar."""
    cfg = cfg or R.config()
    dest = pasta() / f"{ideia_id}.json"
    salvo = read_json(dest, None)
    if not salvo or not 0 <= n < len(salvo.get("conceitos") or []):
        raise ValueError("Gere os conceitos de thumb primeiro.")
    fs = fotos(cfg)
    png_bytes = (gemini or chamar_gemini)(prompt_final(salvo["conceitos"][n], bool(fs)),
                                          [_foto_bytes(f) for f in fs], cfg)
    k = len(salvo.setdefault("imagens", {}).get(str(n)) or []) + 1
    png = pasta() / f"{ideia_id}-{n + 1}-{k}.png"
    png.write_bytes(png_bytes)
    jpg = _jpg_youtube(png)
    nome = (jpg or png).name
    salvo["imagens"].setdefault(str(n), []).append(nome)
    write_json(dest, salvo)
    log(f"Thumb gerada: {pasta() / nome}" + ("" if fs else " (sem fotos do Léo em fotos-leo/)"))
    return nome
