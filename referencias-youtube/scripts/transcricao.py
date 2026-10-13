"""Transcrição via legenda nativa do YouTube (yt-dlp), sem Whisper e sem Apify.

Saída: {out}/transcricoes/{video_id}.md com timestamps [m:ss] por bloco.
Uso direto: python3 scripts/transcricao.py VIDEO_ID [VIDEO_ID...]
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from yt_common import fmt_time, log, out_dir

LANG_PREF = ["pt-BR", "pt", "pt-orig", "pt-PT", "en", "en-US", "en-orig", "en-GB"]
TS = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s+-->")


def _secs(m):
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def parse_vtt(text):
    """VTT -> lista de (segundos, texto), sem as repetições das legendas automáticas."""
    cues, cur_t, buf = [], None, []
    for line in text.splitlines():
        m = TS.match(line.strip())
        if m:
            if cur_t is not None and buf:
                cues.append((cur_t, " ".join(buf)))
            cur_t, buf = _secs(m), []
            continue
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and cur_t is not None:
            buf.append(line)
    if cur_t is not None and buf:
        cues.append((cur_t, " ".join(buf)))

    # legendas automáticas repetem a linha anterior: guarda só o texto novo
    out, prev = [], ""
    for t, txt in cues:
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt == prev or not txt:
            continue
        if prev and txt.startswith(prev):
            new = txt[len(prev):].strip()
        else:
            # remove sobreposição de sufixo/prefixo entre linhas
            new = txt
            for k in range(min(len(prev), len(txt)), 0, -1):
                if prev.endswith(txt[:k]) and k > 10:
                    new = txt[k:].strip()
                    break
        if new:
            out.append((t, new))
        prev = txt
    return out


def to_markdown(video_id, cues, lang, title=None, block_secs=15):
    lines = [f"# {title or video_id}", "", f"- video_id: {video_id}", f"- url: https://www.youtube.com/watch?v={video_id}",
             f"- legenda: {lang}", ""]
    block_start, block = None, []
    for t, txt in cues:
        if block_start is None:
            block_start = t
        if t - block_start >= block_secs and block:
            lines.append(f"[{fmt_time(block_start)}] " + " ".join(block))
            block_start, block = t, []
        block.append(txt)
    if block:
        lines.append(f"[{fmt_time(block_start)}] " + " ".join(block))
    return "\n".join(lines) + "\n"


def first_seconds(cues, secs=15):
    return " ".join(txt for t, txt in cues if t < secs).strip()


def _pick(files):
    def rank(p):
        lang = p.name.split(".")[-2]
        return LANG_PREF.index(lang) if lang in LANG_PREF else len(LANG_PREF) + (0 if lang.startswith(("pt", "en")) else 50)
    files = sorted(files, key=rank)
    return files[0] if files else None


def fetch(video_id, title=None, force=False):
    """Retorna o caminho do .md ou None se o vídeo não tem legenda pt/en."""
    dest = out_dir() / "transcricoes" / f"{video_id}.md"
    if dest.exists() and not force:
        return dest
    if not shutil.which("yt-dlp"):
        raise SystemExit("yt-dlp não encontrado. Instale: pip install -U yt-dlp")
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
               "--sub-langs", "pt.*,pt,en.*,en,-live_chat", "--sub-format", "vtt",
               "--no-warnings", "-q", "-o", f"{tmp}/%(id)s.%(ext)s",
               f"https://www.youtube.com/watch?v={video_id}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        files = list(Path(tmp).glob("*.vtt"))
        # prefere legenda manual (sem -orig) e pt antes de en
        f = _pick(files)
        if not f:
            log(f"  sem legenda pt/en para {video_id}" + (f" ({r.stderr.strip()[:200]})" if r.returncode else ""))
            return None
        lang = f.name.split(".")[-2]
        cues = parse_vtt(f.read_text(encoding="utf-8", errors="replace"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(to_markdown(video_id, cues, lang, title), encoding="utf-8")
    return dest


def read_cues(md_path):
    """Lê de volta um .md de transcrição -> lista de (segundos, texto)."""
    cues = []
    for line in Path(md_path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\[(\d+):(\d{2})\]\s(.*)", line)
        if m:
            cues.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3)))
    return cues


if __name__ == "__main__":
    for vid in sys.argv[1:]:
        print(fetch(vid, force=True))
