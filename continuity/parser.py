"""Parse Kuku-style microdrama screenplays into timed beats.

Pure code, no AI: timestamps must be exact, so the model never writes them.
Every beat gets a stable ID like "E03-B017" that the LLM refers to instead.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

TIME_RE = re.compile(r"(\d+):(\d+(?:\.\d+)?)")
BEAT_HEAD_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
BEAT_TIME_RE = re.compile(r"Start:\s*\(([\d:.]+)\)\s*\|\s*End:\s*\(([\d:.]+)\)", re.I)
SCENE_RE = re.compile(r"^SCENE\s+(\d+)\s*$", re.I)
EP_NUM_RE = re.compile(r"(\d+)")


def to_sec(t: str) -> float:
    m = TIME_RE.search(t)
    if not m:
        raise ValueError(f"bad time: {t!r}")
    return int(m.group(1)) * 60 + float(m.group(2))


def fmt(sec: float) -> str:
    m, s = divmod(max(sec, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"


@dataclass
class Beat:
    id: str
    episode: int
    scene: int
    idx: int            # position within episode
    kind: str           # camera | action | dialogue | sfx | other
    speaker: str | None
    emotion: str | None
    start: float
    end: float
    text: str

    def short(self) -> str:
        if self.kind == "dialogue":
            emo = f" ({self.emotion})" if self.emotion else ""
            return f"{self.speaker}{emo}: {self.text}"
        return f"[{self.kind.upper()}] {self.text}"


@dataclass
class Scene:
    number: int
    heading: str
    start: float
    end: float


@dataclass
class Episode:
    number: int
    title: str
    logline: str
    continuity: str
    scenes: list[Scene] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [s.end for s in self.scenes] + [b.end for b in self.beats]
        return max(ends) if ends else 0.0


def _classify(head: str) -> tuple[str, str | None, str | None]:
    """'CAMERA' -> camera; 'JULIANA (Cold, Shocked)' -> dialogue."""
    h = head.strip()
    up = h.upper()
    if up == "CAMERA":
        return "camera", None, None
    if up == "ACTION":
        return "action", None, None
    if up.startswith("SOUND") or up in {"SFX", "MUSIC"}:
        return "sfx", None, None
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", h)
    if m:
        return "dialogue", m.group(1).strip(), m.group(2).strip()
    return "dialogue", h, None


def parse_episode(text: str, fallback_number: int = 0, title: str = "") -> Episode:
    lines = text.replace("\r\n", "\n").split("\n")
    meta = {}
    for ln in lines[:15]:
        for key in ("EPISODE NUMBER", "EPISODE LOGLINE", "CONTINUITY NOTES"):
            if ln.upper().startswith(key + ":"):
                meta[key] = ln.split(":", 1)[1].strip()
    num_m = EP_NUM_RE.search(meta.get("EPISODE NUMBER", ""))
    ep = Episode(
        number=int(num_m.group(1)) if num_m else fallback_number,
        title=title,
        logline=meta.get("EPISODE LOGLINE", ""),
        continuity=meta.get("CONTINUITY NOTES", ""),
    )

    scene_no, scene_head, scene_start, scene_end = 0, "", None, None
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i].strip()
        sm = SCENE_RE.match(ln)
        if sm:
            scene_no = int(sm.group(1))
            scene_head, scene_start, scene_end = "", None, None
            i += 1
            continue
        low = ln.lower()
        if low.startswith("scene heading:"):
            scene_head = ln.split(":", 1)[1].strip()
        elif low.startswith("scene start:"):
            scene_start = to_sec(ln.split(":", 1)[1])
        elif low.startswith("scene end:"):
            scene_end = to_sec(ln.split(":", 1)[1])
            ep.scenes.append(Scene(scene_no, scene_head, scene_start or 0.0, scene_end))
        bm = BEAT_HEAD_RE.match(ln)
        if bm and i + 1 < n:
            tm = BEAT_TIME_RE.search(lines[i + 1])
            if tm:
                body = []
                j = i + 2
                while j < n and lines[j].strip() and not BEAT_HEAD_RE.match(lines[j].strip()) \
                        and not SCENE_RE.match(lines[j].strip()):
                    body.append(lines[j].strip())
                    j += 1
                kind, spk, emo = _classify(bm.group(1))
                txt = " ".join(body).strip().strip('"').strip("“”")
                idx = len(ep.beats)
                ep.beats.append(Beat(
                    id=f"E{ep.number:02d}-B{idx:03d}", episode=ep.number, scene=scene_no,
                    idx=idx, kind=kind, speaker=spk, emotion=emo,
                    start=to_sec(tm.group(1)), end=to_sec(tm.group(2)), text=txt,
                ))
                i = j
                continue
        i += 1
    return ep


def load_show(path: str | Path | bytes) -> tuple[str, list[Episode]]:
    """Accepts a folder of .txt files, a .zip path, or zip bytes (Streamlit upload)."""
    files: list[tuple[str, str]] = []
    if isinstance(path, (bytes, bytearray)):
        zf = zipfile.ZipFile(io.BytesIO(path))
        files = [(nm, zf.read(nm).decode("utf-8", "replace")) for nm in zf.namelist()
                 if nm.lower().endswith(".txt") and not Path(nm).name.startswith(".")
                 and "__MACOSX" not in nm]
    else:
        p = Path(path)
        if p.is_file() and p.suffix.lower() == ".zip":
            return load_show(p.read_bytes())
        if p.is_file():
            files = [(p.name, p.read_text("utf-8", "replace"))]
        else:
            files = [(str(f), f.read_text("utf-8", "replace")) for f in sorted(p.rglob("*.txt"))
                     if not f.name.startswith(".")]
    if not files:
        raise ValueError("No .txt screenplay files found")
    show = Path(files[0][0]).parent.name or "show"
    eps = []
    for k, (nm, txt) in enumerate(sorted(files)):
        m = re.search(r"episode-(\d+)", Path(nm).name, re.I)
        ep = parse_episode(txt, fallback_number=int(m.group(1)) if m else k + 1, title=show)
        if ep.beats:
            eps.append(ep)
    eps.sort(key=lambda e: e.number)
    return show, eps


def episode_as_text(ep: Episode, include_camera: bool = True) -> str:
    """Compact, ID-tagged transcript that the LLM reads."""
    out = [f"=== EPISODE {ep.number} ({fmt(ep.duration)}) ===", f"Logline: {ep.logline}"]
    cur = None
    for b in ep.beats:
        if b.scene != cur:
            cur = b.scene
            sc = next((s for s in ep.scenes if s.number == cur), None)
            out.append(f"-- Scene {cur}: {sc.heading if sc else ''}")
        if b.kind == "camera" and not include_camera:
            continue
        out.append(f"{b.id} [{fmt(b.start)}-{fmt(b.end)}] {b.short()}")
    return "\n".join(out)


def beat_to_dict(b: Beat) -> dict:
    return asdict(b)
