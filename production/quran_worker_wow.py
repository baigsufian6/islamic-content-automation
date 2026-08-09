#!/usr/bin/env python3
"""
Quran Production Worker — polished production edition.

Purpose
-------
This worker is the production brain behind the Quran short-video pipeline.
It can be invoked directly or by a Node/Express backend, and it is the ONLY
script the GitHub Actions workflow runs — there is no second, duplicate
implementation living inside the workflow YAML anymore.

The pipeline is deliberately deterministic and stateful:

    Quran Foundation
          ↓
    verified Quran index
          ↓
    cycle-aware passage planner
          ↓
    Saad Al-Ghamdi ayah audio
          ↓
    meaning-aware Pexels visual search
          ↓
    cinematic 9:16 composition
          ↓
    Arabic + English typography
          ↓
    hard media validation
          ↓
    clean publication package
          ↓
    progress commit

Important project rules preserved here:
- No manual Surah number is required.
- No manual ayah number is required.
- Cycle 1 follows Quran order.
- Later cycles select meaningful, contiguous passages without reusing an ayah.
- A section never crosses a Surah boundary.
- Saad Al-Ghamdi is the production reciter.
- One background video is used per section, not one visual per ayah.
- Visuals are meaning-directed and are not silently reused unless explicitly allowed.
- Progress advances only after the final MP4 passes validation.
- The publication package contains the MP4 + metadata, while heavy build files stay out.

Environment
-----------
Required:
  QF_CLIENT_ID
  QF_CLIENT_SECRET
  QF_ENV=production|prelive
  PEXELS_API_KEY

Optional:
  ALLOW_VISUAL_REUSE=1
  COMMIT_PROGRESS=1
  GIT_PUSH_PROGRESS=1
  GIT_REMOTE=origin
  GIT_BRANCH=main
  QURAN_TARGET_MIN=35
  QURAN_TARGET_MAX=65
  QURAN_LOG_LEVEL=INFO
  QURAN_FORCE_REINDEX=1
  QURAN_DRY_RUN=1
  QURAN_RENDER_PREVIEW=1
  QURAN_KEEP_BUILD=1

The script intentionally avoids requiring an AI service for the core pipeline.
The semantic selector is deterministic and can later be replaced by a semantic
embedding/reranking service without changing the renderer or state engine.
"""

from __future__ import annotations

import glob
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter


# ============================================================================
# 0. Paths, constants, configuration
# ============================================================================

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "quran_index.json"
PROGRESS = DATA / "progress.json"
QUEUE = DATA / "publish_queue.json"
LOCK = DATA / ".production.lock"
PUBLISH = ROOT / "publish"
BUILD = ROOT / "output" / "production"

DATA.mkdir(parents=True, exist_ok=True)
PUBLISH.mkdir(parents=True, exist_ok=True)
BUILD.mkdir(parents=True, exist_ok=True)

def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {name} must be an integer, got {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise SystemExit(
            f"ERROR: {name} must be between {minimum} and {maximum}, got {value}."
        )
    return value


W = env_int("QURAN_WIDTH", 1080, 540, 2160)
H = env_int("QURAN_HEIGHT", 1920, 960, 3840)
FPS = env_int("QURAN_FPS", 30, 24, 60)
TRANSLATION_ID = env_int("QURAN_TRANSLATION_RESOURCE_ID", 20, 1, 10000)
TRANSLATION_NAME = os.environ.get("QURAN_TRANSLATION_NAME", "Saheeh International").strip()
RECITER = os.environ.get("QURAN_RECITER", "Saad Al-Ghamdi").strip()
AUDIO_BASE = os.environ.get(
    "QURAN_AUDIO_BASE", "https://everyayah.com/data/Ghamadi_40kbps"
).rstrip("/")

if RECITER.lower() != "saad al-ghamdi":
    raise SystemExit(
        f"ERROR: Production reciter must be Saad Al-Ghamdi; got {RECITER!r}."
    )

TARGET_MIN = float(os.environ.get("QURAN_TARGET_MIN", "35"))
TARGET_MAX = float(os.environ.get("QURAN_TARGET_MAX", "65"))
if TARGET_MIN <= 0 or TARGET_MAX < TARGET_MIN:
    raise SystemExit("ERROR: Invalid QURAN_TARGET_MIN/QURAN_TARGET_MAX range.")
LOG_LEVEL = os.environ.get("QURAN_LOG_LEVEL", "INFO").upper()
ALLOW_VISUAL_REUSE = os.environ.get("ALLOW_VISUAL_REUSE", "").strip() == "1"
COMMIT_PROGRESS = os.environ.get("COMMIT_PROGRESS", "").strip() == "1"
GIT_PUSH_PROGRESS = os.environ.get("GIT_PUSH_PROGRESS", "").strip() == "1"
GIT_REMOTE = os.environ.get("GIT_REMOTE", "origin").strip() or "origin"
GIT_BRANCH = os.environ.get("GIT_BRANCH", "main").strip() or "main"
GIT_PUSH_RETRIES = env_int("GIT_PUSH_RETRIES", 3, 1, 10)
FORCE_REINDEX = os.environ.get("QURAN_FORCE_REINDEX", "").strip() == "1"
DRY_RUN = os.environ.get("QURAN_DRY_RUN", "").strip() == "1"
KEEP_BUILD = os.environ.get("QURAN_KEEP_BUILD", "").strip() == "1"
RENDER_PREVIEW = os.environ.get("QURAN_RENDER_PREVIEW", "").strip() == "1"

CLIENT_ID = os.environ.get("QF_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("QF_CLIENT_SECRET", "")
ENV = os.environ.get("QF_ENV", "").lower()
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit("ERROR: Quran Foundation credentials are missing.")
if not PEXELS_KEY:
    raise SystemExit("ERROR: PEXELS_API_KEY is missing.")
if ENV == "production":
    OAUTH = "https://oauth2.quran.foundation"
    API = "https://apis.quran.foundation"
elif ENV == "prelive":
    OAUTH = "https://prelive-oauth2.quran.foundation"
    API = "https://apis-prelive.quran.foundation"
else:
    raise SystemExit("ERROR: QF_ENV must be production or prelive.")


# ============================================================================
# 1. Logging and small utilities
# ============================================================================

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def log(message: str, level: str = "INFO") -> None:
    if _LEVELS.get(level, 20) < _LEVELS.get(LOG_LEVEL, 20):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] [{level}] {message}", flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Could not read {path}: {exc}", "WARN")
        return default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_cmd(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    log("RUN " + " ".join(command), "DEBUG")
    return subprocess.run(
        command,
        check=True,
        capture_output=capture,
        text=capture,
    )


def safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return value or "quran"


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value or "").strip()


def romanish(value: str) -> str:
    return normalize_space(value).upper()


def arabic_digits(value: int) -> str:
    return str(value).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


def ensure_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required binary is not installed: {name}")


# ============================================================================
# 2. Single-run lock
# ============================================================================

class ProductionLock:
    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    def __enter__(self):
        if self.path.exists():
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0
            if age < 6 * 3600:
                raise RuntimeError(
                    "Another production run appears active. "
                    f"Lock: {self.path}"
                )
            log("Removing stale production lock.", "WARN")
            self.path.unlink(missing_ok=True)

        self.path.write_text(
            json.dumps({"pid": os.getpid(), "created_at": utc_now()}),
            encoding="utf-8",
        )
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            self.path.unlink(missing_ok=True)


# ============================================================================
# 3. Data models
# ============================================================================

@dataclass
class Verse:
    id: int
    verse_key: str
    chapter_id: int
    chapter_name: str
    chapter_name_arabic: str
    verse_number: int
    juz_number: Any
    hizb_number: Any
    ruku_number: Any
    page_number: Any
    arabic_text: str
    translation: str


@dataclass
class AudioItem:
    verse_key: str
    path: Path
    duration: float


@dataclass
class VisualCandidate:
    video_id: int
    video_url: str
    file_url: str
    duration: float
    width: int
    height: int
    score: float
    query: str
    photographer: str
    photographer_url: str


@dataclass
class LayoutResult:
    arabic_size: int
    arabic_lines: list[str]
    english_size: int
    english_lines: list[str]
    arabic_center_y: int
    english_top_y: int
    ornament_y: int
    header_size: int


# ============================================================================
# 4. HTTP clients
# ============================================================================

class HttpClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "quran-production-pipeline/3.1",
        })

    def json(
        self,
        method: str,
        url: str,
        *,
        session: Optional[requests.Session] = None,
        **kwargs,
    ) -> dict[str, Any]:
        active = session or self.session
        last = "unknown error"
        for attempt in range(5):
            try:
                response = active.request(
                    method,
                    url,
                    timeout=90,
                    **kwargs,
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {response.status_code}: {response.text[:300]}"
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2 ** attempt
                    time.sleep(min(delay, 30))
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last = str(exc)
                if attempt == 4:
                    break
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"Request failed: {url} :: {last}")

    def download(self, url: str, destination: Path, *, expected: str = "file") -> None:
        if destination.exists() and destination.stat().st_size > 4096:
            return

        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(destination.suffix + ".part")
        temp.unlink(missing_ok=True)

        for attempt in range(5):
            try:
                with self.session.get(
                    url,
                    stream=True,
                    timeout=120,
                ) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if expected == "audio":
                        if not any(x in content_type for x in ("audio", "mpeg", "octet-stream")):
                            raise RuntimeError(
                                f"Unexpected audio content type: {content_type}"
                            )

                    with temp.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                handle.write(chunk)

                if temp.stat().st_size <= 4096:
                    raise RuntimeError("Downloaded file is unexpectedly small.")
                temp.replace(destination)
                return
            except Exception:
                temp.unlink(missing_ok=True)
                if attempt == 4:
                    raise
                time.sleep(min(2 ** attempt, 20))


class QuranFoundationClient:
    def __init__(self, http: HttpClient):
        self.http = http
        self.session = requests.Session()
        token_payload = http.json(
            "POST",
            f"{OAUTH}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "scope": "content",
            },
            auth=(CLIENT_ID, CLIENT_SECRET),
        )
        token = token_payload.get("access_token")
        if not token:
            raise RuntimeError("Quran Foundation returned no access token.")
        self.session.headers.update({
            "x-auth-token": token,
            "x-client-id": CLIENT_ID,
            "User-Agent": "quran-production-pipeline/3.1",
        })
        log("Quran Foundation authentication: SUCCESS")

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self.http.json(
            "GET",
            API + path,
            session=self.session,
            params=params or {},
        )


class PexelsClient:
    def __init__(self, http: HttpClient):
        self.http = http
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": PEXELS_KEY,
            "User-Agent": "quran-production-pipeline/3.1",
        })

    def search(self, query: str, page: int = 1) -> dict[str, Any]:
        return self.http.json(
            "GET",
            "https://api.pexels.com/v1/videos/search",
            session=self.session,
            params={
                "query": query,
                "orientation": "portrait",
                "size": "large",
                "per_page": 30,
                "page": page,
            },
        )


# ============================================================================
# 5. Quran index and text normalization
# ============================================================================

class QuranRepository:
    def __init__(self, client: QuranFoundationClient):
        self.client = client

    @staticmethod
    def clean_translation(text: str) -> str:
        text = html.unescape(text or "")
        # Remove HTML footnote/superscript markup before any typography work.
        text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.I | re.S)
        text = re.sub(r"<foot_note[^>]*>.*?</foot_note>", "", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", "", text)
        # Remove Unicode superscript digits and common bracketed footnote markers.
        text = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", text)
        text = re.sub(r"\[\s*\d{1,3}\s*\]", "", text)
        # Some translation payloads expose a plain trailing footnote number, e.g.
        # "...Merciful.2". Remove only a number directly attached to punctuation
        # so legitimate numbers such as "Chapter 2" are untouched.
        text = re.sub(r"(?<=[.!?,;:])\s*\d{1,3}(?=\s|$)", "", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Project rule: an explicit slash in a translation becomes a new visual line.
        text = re.sub(r"\s*/\s*", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        return text.strip()

    def build_index(self) -> list[Verse]:
        log("Building Quran index from the live API...")
        verses: list[Verse] = []
        chapters = self.client.get(
            "/content/api/v4/chapters",
            {"language": "en"},
        ).get("chapters", [])
        chapters.sort(key=lambda x: int(x["id"]))

        for chapter in chapters:
            cid = int(chapter["id"])
            page = 1
            while True:
                payload = self.client.get(
                    f"/content/api/v4/verses/by_chapter/{cid}",
                    {
                        "translations": str(TRANSLATION_ID),
                        "fields": (
                            "text_uthmani,verse_key,verse_number,"
                            "juz_number,hizb_number,ruku_number,page_number"
                        ),
                        "translation_fields": "resource_id,text",
                        "page": page,
                        "per_page": 50,
                    },
                )

                for raw in payload.get("verses", []):
                    translation = ""
                    for item in raw.get("translations", []):
                        if str(item.get("resource_id")) == str(TRANSLATION_ID):
                            translation = self.clean_translation(item.get("text", ""))
                            break

                    verses.append(
                        Verse(
                            id=int(raw["id"]),
                            verse_key=raw["verse_key"],
                            chapter_id=cid,
                            chapter_name=(
                                chapter.get("name_simple")
                                or chapter.get("name_complex")
                                or f"Chapter {cid}"
                            ),
                            chapter_name_arabic=(
                                chapter.get("name_arabic")
                                or ""
                            ),
                            verse_number=int(raw["verse_number"]),
                            juz_number=raw.get("juz_number"),
                            hizb_number=raw.get("hizb_number"),
                            ruku_number=raw.get("ruku_number"),
                            page_number=raw.get("page_number"),
                            arabic_text=raw.get("text_uthmani", "").strip(),
                            translation=translation,
                        )
                    )

                pagination = payload.get("pagination", {})
                next_page = pagination.get("next_page")
                if not next_page:
                    break
                page = int(next_page)

        verses.sort(key=lambda v: v.id)
        self.validate_index(verses)
        log(f"Quran index ready: {len(verses)} verses")
        return verses

    @staticmethod
    def validate_index(verses: list[Verse]) -> None:
        if len(verses) < 6000:
            raise RuntimeError(f"Quran index looks incomplete: {len(verses)} verses.")

        ids = [v.id for v in verses]
        if len(ids) != len(set(ids)):
            raise RuntimeError("Quran index contains duplicate verse IDs.")

        keys = [v.verse_key for v in verses]
        if len(keys) != len(set(keys)):
            raise RuntimeError("Quran index contains duplicate verse keys.")

        missing_arabic = sum(not v.arabic_text for v in verses)
        missing_translation = sum(not v.translation for v in verses)
        if missing_arabic > 0:
            raise RuntimeError(f"Quran index has {missing_arabic} verses without Arabic text.")
        if missing_translation > 0:
            raise RuntimeError(
                f"Quran index has {missing_translation} verses without translation {TRANSLATION_ID}."
            )

        log("Quran index validation: PASSED")

    def load_or_build(self) -> list[Verse]:
        if CACHE.exists() and not FORCE_REINDEX:
            raw = load_json(CACHE, [])
            try:
                verses = [Verse(**item) for item in raw]
                self.validate_index(verses)
                log(f"Using cached Quran index: {len(verses)} verses")
                return verses
            except Exception as exc:
                log(f"Cached index rejected: {exc}", "WARN")

        verses = self.build_index()
        atomic_write_json(CACHE, [asdict(v) for v in verses])
        return verses


# ============================================================================
# 6. Persistent state
# ============================================================================

class ProgressStore:
    SCHEMA = 4

    def __init__(self, verses: list[Verse]):
        self.verses = verses
        self.id_by_key = {v.verse_key: v.id for v in verses}
        self.valid_keys = set(self.id_by_key)
        self.state = load_json(PROGRESS, {})
        if not isinstance(self.state, dict):
            self.state = {}

        self.state.setdefault("schema_version", self.SCHEMA)
        self.state.setdefault("cycle", 1)
        self.state.setdefault("used_verses", [])
        self.state.setdefault("used_pexels_ids", [])
        self.state.setdefault("theme_usage", {})
        self.state.setdefault("completed_cycles", [])
        self.state.setdefault("published_count", 0)
        self.state.setdefault("last_section", None)
        self.state.setdefault("history", [])

        self.used = set(self.state.get("used_verses", [])) & self.valid_keys
        self.used_visuals = {int(x) for x in self.state.get("used_pexels_ids", [])}
        self.theme_usage = dict(self.state.get("theme_usage", {}))
        self.cycle = int(self.state.get("cycle", 1))

    def begin_cycle_if_needed(self) -> None:
        if len(self.used) < len(self.verses):
            return

        self.state["completed_cycles"].append({
            "cycle": self.cycle,
            "verses": len(self.verses),
            "completed_at": utc_now(),
            "published_count": int(self.state.get("published_count", 0)),
        })
        self.cycle += 1
        self.used = set()
        self.theme_usage = {}
        # Keep the in-memory state internally consistent immediately. The state is
        # persisted on the next successful section commit; the GitHub workflow only
        # pushes it after the finished MP4 artifact has uploaded successfully.
        self.state["cycle"] = self.cycle
        self.state["used_verses"] = []
        self.state["theme_usage"] = {}
        log(f"QURAN CYCLE {self.cycle} STARTING")

    def commit(
        self,
        *,
        keys: list[str],
        visual_id: int,
        theme: str,
        metadata: dict[str, Any],
    ) -> None:
        self.used.update(keys)
        self.used_visuals.add(visual_id)
        self.theme_usage[theme] = int(self.theme_usage.get(theme, 0)) + 1

        history_item = {
            "cycle": self.cycle,
            "verse_keys": keys,
            "visual_id": visual_id,
            "theme": theme,
            "published_file": metadata.get("published_file"),
            "created_at": utc_now(),
        }

        history = self.state.get("history", [])
        history.append(history_item)
        # Keep the state file compact. Full metadata remains beside the video.
        history = history[-200:]

        self.state.update({
            "schema_version": self.SCHEMA,
            "cycle": self.cycle,
            "used_verses": sorted(self.used, key=lambda key: self.id_by_key[key]),
            "used_pexels_ids": sorted(self.used_visuals),
            "theme_usage": self.theme_usage,
            "published_count": int(self.state.get("published_count", 0)) + 1,
            "last_section": metadata,
            "history": history,
        })
        atomic_write_json(PROGRESS, self.state)


# ============================================================================
# 7. Meaning model and passage planner
# ============================================================================

THEMES: list[tuple[str, list[str], list[str]]] = [
    ("mercy", ["mercy", "merciful", "compassion", "forgive", "forgiveness", "gracious", "kind"], [
        "soft golden sunrise over calm hills",
        "warm morning light through clouds",
        "gentle sunrise over a quiet valley",
    ]),
    ("guidance", ["guidance", "guide", "guided", "straight path", "path"], [
        "winding path through peaceful landscape morning",
        "trail through misty forest sunrise",
        "quiet road through open countryside",
    ]),
    ("creation", ["creation", "created", "heavens", "earth", "sky", "sun", "moon", "stars"], [
        "vast night sky and stars",
        "mountains under dramatic sky",
        "sun and clouds over open horizon",
    ]),
    ("water", ["rain", "water", "river", "rivers", "sea", "ocean", "spring"], [
        "flowing river through green valley",
        "gentle rain over calm landscape",
        "waves rolling onto quiet shore",
    ]),
    ("garden", ["garden", "gardens", "fruit", "fruits", "green", "trees", "orchard"], [
        "lush green garden sunlight leaves",
        "orchard trees in soft sunlight",
        "botanical garden morning light",
    ]),
    ("light", ["light", "darkness", "dark", "night", "morning", "day"], [
        "sunlight breaking through clouds",
        "golden hour light over fields",
        "soft light through forest canopy",
    ]),
    ("patience", ["patience", "patient", "steadfast", "persevere"], [
        "slow ocean waves calm horizon",
        "still lake reflecting mountains",
        "quiet desert dunes in wind",
    ]),
    ("gratitude", ["grateful", "gratitude", "thankful", "thanks"], [
        "golden sunrise over open field",
        "warm light over wheat field",
        "peaceful countryside morning glow",
    ]),
    ("knowledge", ["knowledge", "wisdom", "learn", "understand", "signs"], [
        "quiet contemplative landscape dawn",
        "misty mountains sunrise stillness",
        "calm valley soft morning fog",
    ]),
    ("justice", ["justice", "just", "truth", "truthful", "balance"], [
        "calm balanced landscape wide horizon",
        "mountain reflection on still water",
        "clear horizon over open plain",
    ]),
    ("warning", ["warning", "punishment", "wrongdoers", "wrong", "evil", "fire"], [
        "dramatic storm clouds over landscape",
        "dark clouds rolling over hills",
        "distant storm horizon",
    ]),
    ("paradise", ["paradise", "bliss", "gardens", "rivers"], [
        "lush garden with flowing water",
        "waterfall through green landscape",
        "peaceful oasis with water",
    ]),
]

KEYWORD_WEIGHTS = {
    "straight path": 10,
    "day of judgment": 10,
    "forgive us": 9,
    "forgives whom": 8,
    "mercy": 6,
    "merciful": 6,
    "compassion": 6,
    "guidance": 6,
    "guided": 5,
    "path": 3,
    "patience": 6,
    "patient": 5,
    "steadfast": 5,
    "gratitude": 5,
    "grateful": 5,
    "thankful": 5,
    "forgive": 6,
    "forgiveness": 6,
    "kind": 4,
    "wisdom": 5,
    "knowledge": 5,
    "signs": 4,
    "creation": 5,
    "created": 4,
    "heavens": 4,
    "earth": 4,
    "paradise": 7,
    "gardens": 5,
    "garden": 5,
    "rivers": 4,
    "river": 4,
    "water": 4,
    "justice": 6,
    "truth": 5,
    "truthful": 5,
    "light": 4,
    "darkness": 3,
    "warning": 4,
    "remember": 5,
    "remembrance": 5,
    "prayer": 5,
    "worship": 5,
    "trust": 5,
    "hope": 5,
    "fear": 3,
    "repent": 6,
}


class MeaningEngine:
    def __init__(self, progress: ProgressStore):
        self.progress = progress

    def score_text(self, text: str) -> tuple[str, float]:
        lower = text.lower()
        best_theme = "reflection"
        best_score = 0.0

        for name, words, _variants in THEMES:
            score = 0.0
            for word in words:
                weight = KEYWORD_WEIGHTS.get(word, 1)
                if " " in word:
                    if word in lower:
                        score += weight
                elif re.search(r"\b" + re.escape(word) + r"\b", lower):
                    score += weight
            if score > best_score:
                best_score = score
                best_theme = name

        return best_theme, best_score

    def visual_queries(self, verses: list[Verse], cycle: int) -> tuple[str, list[str], list[str]]:
        text = " ".join(v.translation for v in verses)
        theme, theme_score = self.score_text(text)
        variants = next(
            (variants for name, _words, variants in THEMES if name == theme),
            ["peaceful cinematic nature landscape"],
        )

        # Rotate within the theme so repeated meanings do not look identical.
        count = int(self.progress.theme_usage.get(theme, 0))
        chosen = variants[count % len(variants)]
        cycle_styles = [
            "cinematic natural landscape, subtle movement, realistic",
            "cinematic nature close-up, soft depth of field, realistic",
            "wide atmospheric landscape, realistic documentary style",
            "water and sky nature footage, elegant slow motion, realistic",
            "cinematic dawn or twilight nature, restrained movement, realistic",
        ]
        style = cycle_styles[(cycle - 1) % len(cycle_styles)]

        # The score is intentionally deterministic; a later embedding service can
        # replace this method without touching the render pipeline.
        queries = [
            f"{chosen}, {style}",
            *[f"{v}, {style}" for v in variants if v != chosen],
        ]
        fallback = [
            "peaceful cinematic nature landscape, realistic",
            "calm natural scenery soft light, realistic",
            "quiet countryside morning atmosphere, realistic",
        ]
        avoid = "no people, no faces, no religious figures, no text, no logos, no crowds, no readable signs"
        queries = [f"{q}, {avoid}" for q in queries]
        fallback = [f"{q}, {avoid}" for q in fallback]

        log(
            f"Meaning theme={theme} score={theme_score:.1f} "
            f"queries={len(queries)}",
            "DEBUG",
        )
        return theme, queries, fallback


class PassagePlanner:
    def __init__(self, verses: list[Verse], progress: ProgressStore, meaning: MeaningEngine):
        self.verses = verses
        self.progress = progress
        self.meaning = meaning

    def _unused_indexes(self) -> list[int]:
        return [
            i for i, verse in enumerate(self.verses)
            if verse.verse_key not in self.progress.used
        ]

    def _runs(self) -> list[list[int]]:
        unused = self._unused_indexes()
        runs: list[list[int]] = []
        current: list[int] = []
        prev_i: Optional[int] = None
        prev_chapter: Optional[int] = None
        prev_ruku: Any = None

        for i in unused:
            verse = self.verses[i]
            contiguous = (
                prev_i is not None
                and i == prev_i + 1
                and verse.chapter_id == prev_chapter
                and verse.ruku_number == prev_ruku
            )
            if not contiguous and current:
                runs.append(current)
                current = []
            current.append(i)
            prev_i = i
            prev_chapter = verse.chapter_id
            prev_ruku = verse.ruku_number

        if current:
            runs.append(current)
        return runs

    def choose_start(self) -> int:
        unused = self._unused_indexes()
        if not unused:
            raise RuntimeError("No unused Quran verse remains.")

        if self.progress.cycle == 1:
            return unused[0]

        runs = self._runs()
        if not runs:
            raise RuntimeError("No contiguous unused passage exists.")

        previous_theme = ""
        history = self.progress.state.get("history", [])
        if history:
            previous_theme = str(history[-1].get("theme", ""))

        def run_score(run: list[int]) -> float:
            verses = [self.verses[i] for i in run]
            text = " ".join(v.translation for v in verses)
            theme, meaning_score = self.meaning.score_text(text)
            score = meaning_score

            # Prefer coherent but not enormous runs.
            score += min(len(text) / 260.0, 3.0)
            score += min(len(run) * 0.4, 3.0)

            if theme == previous_theme:
                score -= 1.5

            # Gentle deterministic spread across the Quran so the same high-score
            # passage cannot win forever across cycles.
            first_id = self.verses[run[0]].id
            score += ((first_id * (self.progress.cycle + 11)) % 31) / 100.0
            return score

        ranked = sorted(runs, key=run_score, reverse=True)
        return ranked[0][0]

    def select_passage(self, start_index: int, durations: dict[str, float]) -> list[Verse]:
        start = self.verses[start_index]
        selected: list[Verse] = []
        total = 0.0
        i = start_index

        while i < len(self.verses):
            verse = self.verses[i]
            if verse.verse_key in self.progress.used:
                break
            if verse.chapter_id != start.chapter_id:
                break

            # Same ruku until the minimum is satisfied. After the minimum, stop
            # at the boundary rather than creating a contextless mash-up.
            if (
                selected
                and start.ruku_number is not None
                and verse.ruku_number != start.ruku_number
                and total >= TARGET_MIN
            ):
                break

            d = durations.get(verse.verse_key)
            if d is None:
                raise RuntimeError(f"Missing audio duration for {verse.verse_key}")

            if selected and total + d > TARGET_MAX and total >= TARGET_MIN:
                break

            selected.append(verse)
            total += d
            i += 1

            if total >= TARGET_MIN:
                if i >= len(self.verses):
                    break
                nxt = self.verses[i]
                if nxt.chapter_id != start.chapter_id:
                    break
                if start.ruku_number is not None and nxt.ruku_number != start.ruku_number:
                    break

        if not selected:
            raise RuntimeError("Could not construct a Quran passage.")

        self._validate(selected)
        reused = [v.verse_key for v in selected if v.verse_key in self.progress.used]
        if reused:
            raise RuntimeError(f"Planner selected already-used ayahs: {reused}")
        return selected

    def _validate(self, selected: list[Verse]) -> None:
        keys = [v.verse_key for v in selected]
        if len(keys) != len(set(keys)):
            raise RuntimeError("Duplicate ayah inside selected section.")
        if len({v.chapter_id for v in selected}) != 1:
            raise RuntimeError("Section crossed a Surah boundary.")
        for left, right in zip(selected, selected[1:]):
            if right.id != left.id + 1:
                raise RuntimeError(
                    f"Non-contiguous section: {left.verse_key} -> {right.verse_key}"
                )


# ============================================================================
# 8. Audio engine
# ============================================================================

class AudioEngine:
    def __init__(self, http: HttpClient):
        self.http = http
        self.cache = BUILD / "_audio-cache"
        self.cache.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def url_for(verse_key: str) -> str:
        surah, ayah = verse_key.split(":")
        return f"{AUDIO_BASE}/{int(surah):03d}{int(ayah):03d}.mp3"

    @staticmethod
    def duration(path: Path) -> float:
        result = run_cmd([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ], capture=True)
        value = float(result.stdout.strip())
        if value <= 0:
            raise RuntimeError(f"Invalid duration for {path}")
        return value

    def fetch(self, verse_key: str) -> AudioItem:
        path = self.cache / f"{verse_key.replace(':', '-')}.mp3"
        self.http.download(self.url_for(verse_key), path, expected="audio")
        # ffprobe is the authoritative validity check, not only file size.
        d = self.duration(path)
        return AudioItem(verse_key, path, d)

    def prepare(self, verses: list[Verse], section_dir: Path) -> tuple[list[AudioItem], Path, float]:
        items = [self.fetch(v.verse_key) for v in verses]
        concat = section_dir / "audio.concat.txt"
        output = section_dir / "saad-al-ghamdi.mp3"

        concat.write_text(
            "\n".join(
                "file '" + str(item.path.resolve()).replace("'", "'\\''") + "'"
                for item in items
            ) + "\n",
            encoding="utf-8",
        )

        run_cmd([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat),
            "-vn",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(output),
        ])

        final_duration = self.duration(output)
        summed = sum(item.duration for item in items)
        if abs(final_duration - summed) > 0.35:
            raise RuntimeError(
                f"Audio duration mismatch: sum={summed:.3f}s final={final_duration:.3f}s"
            )
        return items, output, final_duration


# ============================================================================
# 9. Visual engine
# ============================================================================

class VisualEngine:
    def __init__(self, client: PexelsClient, progress: ProgressStore, http: HttpClient):
        self.client = client
        self.progress = progress
        self.http = http

    @staticmethod
    def best_file(video: dict[str, Any]) -> Optional[tuple[float, int, int, dict[str, Any]]]:
        choices = []
        for item in video.get("video_files", []):
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            link = item.get("link")
            if not link or width <= 0 or height <= 0:
                continue
            if height < width:
                continue

            ratio = height / width
            if ratio < 1.40:
                continue

            score = 0.0
            if height >= 2160:
                score += 70
            elif height >= 1920:
                score += 60
            elif height >= 1280:
                score += 35
            elif height >= 720:
                score += 10

            if 1.60 <= ratio <= 1.85:
                score += 20
            elif ratio >= 1.40:
                score += 8

            if str(item.get("quality", "")).lower() == "hd":
                score += 15

            score += min(height / 1000.0, 12)
            choices.append((score, height, width, item))

        if not choices:
            return None
        return max(choices, key=lambda x: (x[0], x[1], x[2]))

    def candidate_from_video(
        self,
        video: dict[str, Any],
        query: str,
        audio_duration: float,
    ) -> Optional[VisualCandidate]:
        chosen = self.best_file(video)
        if not chosen:
            return None

        score, height, width, file_data = chosen
        video_id = int(video.get("id") or -1)
        duration = float(video.get("duration") or 0)

        if duration >= audio_duration:
            score += 30
        elif duration >= audio_duration * 0.75:
            score += 18
        elif duration >= audio_duration * 0.50:
            score += 8
        else:
            score -= 8

        # Prefer clips with room for a slow crop/pan rather than extremely short loops.
        if duration >= 12:
            score += 5
        if duration >= 20:
            score += 4

        user = video.get("user") or {}
        return VisualCandidate(
            video_id=video_id,
            video_url=video.get("url") or "",
            file_url=file_data["link"],
            duration=duration,
            width=width,
            height=height,
            score=score,
            query=query,
            photographer=user.get("name") or "Unknown",
            photographer_url=user.get("url") or "",
        )

    def choose(
        self,
        queries: list[str],
        fallback_queries: list[str],
        audio_duration: float,
    ) -> VisualCandidate:
        fresh: list[VisualCandidate] = []
        reused: list[VisualCandidate] = []

        def scan(query_list: list[str]) -> None:
            for query in query_list:
                for page in range(1, 5):
                    try:
                        payload = self.client.search(query, page)
                    except Exception as exc:
                        log(f"Pexels search failed: {query} page={page}: {exc}", "WARN")
                        continue

                    videos = payload.get("videos", [])
                    if not videos:
                        break

                    for raw in videos:
                        candidate = self.candidate_from_video(raw, query, audio_duration)
                        if candidate is None:
                            continue
                        if candidate.video_id in self.progress.used_visuals:
                            reused.append(candidate)
                        else:
                            fresh.append(candidate)

        scan(queries)
        if not fresh:
            log("No fresh visual found in theme queries; trying neutral fallbacks.", "WARN")
            scan(fallback_queries)

        if fresh:
            # Highest score wins; tiny deterministic tie-breaker prevents unstable ordering.
            fresh.sort(
                key=lambda c: (
                    c.score,
                    c.duration,
                    c.height,
                    -c.video_id,
                ),
                reverse=True,
            )
            return fresh[0]

        if ALLOW_VISUAL_REUSE and reused:
            reused.sort(
                key=lambda c: (c.score, c.duration, c.height),
                reverse=True,
            )
            log(
                f"ALLOW_VISUAL_REUSE=1; reusing Pexels video {reused[0].video_id}.",
                "WARN",
            )
            return reused[0]

        raise RuntimeError(
            "No fresh suitable Pexels video was found. "
            "The build stops instead of silently repeating footage."
        )

    def download(self, candidate: VisualCandidate, destination: Path) -> None:
        self.http.download(candidate.file_url, destination)


# ============================================================================
# 10. Typography and visual layout engine
# ============================================================================
#
# Presentation notes (design pass):
#   - Palette: warm ivory Arabic text, soft champagne-gold accents, pure white
#     English translation. Everything sits on a deep charcoal scrim rather than
#     flat black so it reads as "cinematic" instead of "subtitle box".
#   - The gradient scrim is now stronger and asymmetric: heavier over the
#     lower third (where the English line and progress dots live) so text
#     always has contrast regardless of the background footage.
#   - A slim double-line frame plus four minimal arabesque corner flourishes
#     replace the old plain dots — this is what gives the frame its
#     "designed", non-template feel.
#   - A thin hairline divider sits directly under the header block, and a
#     matching one sits above the footer, so the composition reads as three
#     clear zones (header / verse / footer) at a glance.


PALETTE = {
    "scrim": (8, 8, 11),
    "gold": (231, 199, 125),
    "gold_soft": (212, 178, 108),
    "ivory": (255, 241, 199),
    "white": (255, 255, 255),
}


class TypographyEngine:
    def __init__(self):
        self.font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self.arabic_font_path = self.find_font([
            "/usr/share/fonts/**/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/**/NotoNaskhArabic*Regular*.ttf",
            "/usr/share/fonts/**/NotoNaskhArabic*.ttf",
        ])
        self.english_font_path = self.find_font([
            "/usr/share/fonts/**/NotoSans-Regular.ttf",
            "/usr/share/fonts/**/NotoSans*Regular*.ttf",
        ])
        self.bold_font_path = self.find_font([
            "/usr/share/fonts/**/NotoSans-Bold.ttf",
            "/usr/share/fonts/**/NotoSans*Bold*.ttf",
        ]) or self.english_font_path

        if not self.arabic_font_path or not self.english_font_path:
            raise RuntimeError("Required Noto fonts were not found.")

    @staticmethod
    def find_font(patterns: list[str]) -> Optional[str]:
        for pattern in patterns:
            matches = sorted(glob.glob(pattern, recursive=True))
            if matches:
                return matches[0]
        return None

    def font(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        key = (path, size)
        if key not in self.font_cache:
            self.font_cache[key] = ImageFont.truetype(path, size)
        return self.font_cache[key]

    def width(self, path: str, text: str, size: int) -> float:
        return self.font(path, size).getlength(text)

    def wrap(self, path: str, text: str, size: int, maximum: float) -> list[str]:
        result: list[str] = []
        for paragraph in text.split("\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            words = paragraph.split()
            line = ""
            for word in words:
                candidate = word if not line else line + " " + word
                if not line or self.width(path, candidate, size) <= maximum:
                    line = candidate
                else:
                    result.append(line)
                    line = word
            if line:
                result.append(line)
        return result or [""]

    def fit(
        self,
        path: str,
        text: str,
        maximum_width: int,
        maximum_height: int,
        start_size: int,
        minimum_size: int,
        line_gap: float,
    ) -> tuple[int, list[str], float]:
        safe_width = maximum_width * 0.90
        safe_height = maximum_height * 0.90
        best = (minimum_size, [text], minimum_size * line_gap)

        for size in range(start_size, minimum_size - 1, -2):
            lines = self.wrap(path, text, size, safe_width)
            line_height = size * line_gap
            block_height = line_height * len(lines)
            widest = max(self.width(path, line, size) for line in lines)
            best = (size, lines, line_height)
            if widest <= safe_width and block_height <= safe_height:
                return best
        return best

    def layout(
        self,
        chapter_name: str,
        chapter_id: int,
        first_ayah: int,
        last_ayah: int,
        arabic: str,
        english: str,
    ) -> LayoutResult:
        header = f"✦  SURAH {romanish(chapter_name)}  ✦"
        header_size, _header_lines, _ = self.fit(
            self.english_font_path,
            header,
            W - 160,
            74,
            42,
            26,
            1.10,
        )

        arabic_size, arabic_lines, arabic_line_height = self.fit(
            self.arabic_font_path,
            arabic,
            W - 140,
            460,
            96,
            44,
            1.46,
        )
        arabic_height = arabic_line_height * len(arabic_lines)

        # The two text blocks are intentionally close: the ornament becomes the
        # breathing space, not an enormous empty gap.
        arabic_top = 336
        arabic_height_limit = 448
        arabic_center = int(
            arabic_top + (arabic_height_limit - arabic_height) / 2 + arabic_height / 2
        )

        english_size, english_lines, english_line_height = self.fit(
            self.english_font_path,
            english,
            W - 176,
            470,
            50,
            26,
            1.30,
        )
        english_height = english_line_height * len(english_lines)

        ornament_y = int(
            max(742, min(818, arabic_center + arabic_height / 2 + 50))
        )
        english_top = int(
            ornament_y + 54
        )

        # If English would run into the footer, compact it rather than allowing overflow.
        if english_top + english_height > 1500:
            english_size, english_lines, english_line_height = self.fit(
                self.english_font_path,
                english,
                W - 176,
                405,
                min(english_size, 45),
                24,
                1.26,
            )

        return LayoutResult(
            arabic_size=arabic_size,
            arabic_lines=arabic_lines,
            english_size=english_size,
            english_lines=english_lines,
            arabic_center_y=arabic_center,
            english_top_y=english_top,
            ornament_y=ornament_y,
            header_size=header_size,
        )

    def make_overlay(self, path: Path) -> None:
        """Build the static PNG scrim + frame that sits over the background video.

        This is the single biggest lever for how "premium" the final render
        looks, since it controls legibility and the frame/border identity.
        """
        image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        top, bottom = 640, 1240  # gradient never touches, header/footer own their zones

        # --- readability scrim -------------------------------------------------
        # Asymmetric: the lower third (English line + progress + footer) needs
        # the most contrast because it sits over the busiest part of most stock
        # footage (horizon lines, water glare, moving foliage).
        for y in range(H):
            alpha = 0
            if y < 400:
                alpha = int(150 * (1 - y / 400))
            elif y > 1200:
                t = (y - 1200) / (H - 1200)
                alpha = int(30 + 190 * t)
            if alpha:
                draw.line([(0, y), (W, y)], fill=(*PALETTE["scrim"], alpha))

        # A soft secondary wash directly behind the Arabic block only, so the
        # verse itself always sits on a slightly darker plate regardless of
        # what's happening in the footage behind it.
        verse_band = Image.new("RGBA", (W, 560), (0, 0, 0, 0))
        band_draw = ImageDraw.Draw(verse_band)
        for y in range(560):
            d = min(y, 560 - y) / 280.0
            alpha = int(70 * (1 - d))
            band_draw.line([(0, y), (W, y)], fill=(*PALETTE["scrim"], max(alpha, 0)))
        image.alpha_composite(verse_band, (0, 300))

        # --- frame ---------------------------------------------------------
        margin = 34
        inner = margin + 10
        draw.rounded_rectangle(
            [margin, margin, W - margin, H - margin],
            radius=26,
            outline=(*PALETTE["gold"], 60),
            width=2,
        )
        draw.rounded_rectangle(
            [inner, inner, W - inner, H - inner],
            radius=20,
            outline=(255, 255, 255, 18),
            width=1,
        )

        # --- minimal arabesque corner flourishes ---------------------------
        # A short diagonal tick plus a small hollow diamond at each corner:
        # reads as an intentional ornamental frame rather than plain dots.
        def flourish(cx: float, cy: float, flip_x: int, flip_y: int) -> None:
            gold = (*PALETTE["gold"], 130)
            arm = 30
            draw.line(
                [
                    (cx, cy + flip_y * arm * 0.35),
                    (cx + flip_x * arm, cy),
                    (cx, cy - flip_y * arm * 0.35),
                ],
                fill=gold,
                width=2,
                joint="curve",
            )
            r = 5
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=gold, width=2)

        pad = margin + 26
        flourish(pad, pad, 1, 1)
        flourish(W - pad, pad, -1, 1)
        flourish(pad, H - pad, 1, -1)
        flourish(W - pad, H - pad, -1, -1)

        # --- hairline zone dividers -----------------------------------------
        draw.line([(150, 224), (W - 150, 224)], fill=(*PALETTE["gold"], 55), width=1)
        draw.line([(150, 1745), (W - 150, 1745)], fill=(*PALETTE["gold"], 45), width=1)

        # A very soft blur avoids hard overlay edges.
        image = image.filter(ImageFilter.GaussianBlur(radius=0.25))
        image.save(path)

    @staticmethod
    def ass_escape(text: str) -> str:
        return (
            str(text)
            .replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
        )

    @classmethod
    def ass_lines(cls, lines: Iterable[str]) -> str:
        return r"\N".join(cls.ass_escape(line) for line in lines)

    @staticmethod
    def ass_time(seconds: float) -> str:
        centiseconds = max(0, int(round(seconds * 100)))
        hours = centiseconds // 360000
        centiseconds %= 360000
        minutes = centiseconds // 6000
        centiseconds %= 6000
        seconds_part = centiseconds // 100
        centiseconds %= 100
        return f"{hours}:{minutes:02d}:{seconds_part:02d}.{centiseconds:02d}"

    def make_ass(
        self,
        path: Path,
        verses: list[Verse],
        audio_items: list[AudioItem],
        chapter_name: str,
        chapter_id: int,
        chapter_name_arabic: str,
    ) -> None:
        total_count = len(verses)
        events: list[str] = []
        cursor = 0.0

        header_text = f"✦  SURAH {romanish(chapter_name)}  ✦"
        arabic_chapter = chapter_name_arabic or ""

        for idx, (verse, audio) in enumerate(zip(verses, audio_items)):
            start = cursor
            end = cursor + audio.duration
            cursor = end

            layout = self.layout(
                chapter_name,
                chapter_id,
                verse.verse_number,
                verse.verse_number,
                verse.arabic_text,
                verse.translation,
            )

            if idx == 0:
                range_label = (
                    f"CHAPTER {chapter_id}  •  "
                    f"AYAH {verse.verse_number}–{verses[-1].verse_number}"
                )
            else:
                range_label = (
                    f"CHAPTER {chapter_id}  •  "
                    f"AYAH {verse.verse_number}"
                )

            progress = f"{idx + 1} / {total_count}"
            arabic_badge = f"﴿ {arabic_digits(verse.verse_number)} ﴾"

            # Header
            events.append(
                f"Dialogue: 0,{self.ass_time(start)},{self.ass_time(end)},Header,,0,0,0,,"
                f"{{\\an8\\pos(540,64)\\fs{layout.header_size}\\fad(180,180)}}"
                f"{self.ass_escape(header_text)}"
            )

            # Arabic Surah name is deliberately small and elegant, not another headline.
            if arabic_chapter:
                events.append(
                    f"Dialogue: 0,{self.ass_time(start)},{self.ass_time(end)},ChapterArabic,,0,0,0,,"
                    f"{{\\an8\\pos(540,118)\\fad(180,180)}}"
                    f"{self.ass_escape(arabic_chapter)}"
                )

            events.append(
                f"Dialogue: 0,{self.ass_time(start)},{self.ass_time(end)},SubHeader,,0,0,0,,"
                f"{{\\an8\\pos(540,160)\\fad(180,180)}}"
                f"{self.ass_escape(range_label)}"
            )
            events.append(
                f"Dialogue: 0,{self.ass_time(start)},{self.ass_time(end)},Source,,0,0,0,,"
                f"{{\\an8\\pos(540,200)\\fad(180,180)}}"
                f"SAHEEH INTERNATIONAL  •  SAAD AL-GHAMDI"
            )

            # Arabic verse
            events.append(
                f"Dialogue: 1,{self.ass_time(start)},{self.ass_time(end)},Arabic,,0,0,0,,"
                f"{{\\an5\\pos(540,{layout.arabic_center_y})"
                f"\\fs{layout.arabic_size}\\fad(220,220)}}"
                f"{self.ass_lines(layout.arabic_lines)}"
            )

            # Small ayah medallion and ornamental divider.
            events.append(
                f"Dialogue: 1,{self.ass_time(start)},{self.ass_time(end)},Ornament,,0,0,0,,"
                f"{{\\an5\\pos(540,{layout.ornament_y})\\fad(180,180)}}"
                f"✦   ۞   ✦   {self.ass_escape(arabic_badge)}"
            )

            # English stays intentionally close to Arabic.
            events.append(
                f"Dialogue: 2,{self.ass_time(start)},{self.ass_time(end)},English,,0,0,0,,"
                f"{{\\an8\\pos(540,{layout.english_top_y})\\fs{layout.english_size}\\fad(220,220)}}"
                f"{self.ass_lines(layout.english_lines)}"
            )

            # Progress indicator is tiny and quiet.
            dots = "".join("●" if j == idx else "○" for j in range(total_count))
            events.append(
                f"Dialogue: 0,{self.ass_time(start)},{self.ass_time(end)},Progress,,0,0,0,,"
                f"{{\\an8\\pos(540,1668)\\fad(180,180)}}"
                f"{self.ass_escape(dots)}   {progress}"
            )

            events.append(
                f"Dialogue: 0,{self.ass_time(start)},{self.ass_time(end)},Footer,,0,0,0,,"
                f"{{\\an8\\pos(540,1788)}}QURAN  •  SAHEEH INTERNATIONAL"
            )

        ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Header,Noto Sans,42,&H007DC7E7,&H007DC7E7,&H00101010,&H78000000,1,0,0,0,100,100,1,0,3,1.4,2,8,60,60,0,1
Style: ChapterArabic,Noto Naskh Arabic,27,&H00AED6E7,&H00AED6E7,&H00101010,&H60000000,0,0,0,0,100,100,0,0,3,1,1,8,60,60,0,1
Style: SubHeader,Noto Sans,25,&H00FFFFFF,&H00FFFFFF,&H00101010,&H70000000,1,0,0,0,100,100,0,0,3,1.2,2,8,60,60,0,1
Style: Source,Noto Sans,16,&H00E0D9CC,&H00E0D9CC,&H00101010,&H50000000,0,0,0,0,100,100,1,0,3,1,1,8,60,60,0,1
Style: Arabic,Noto Naskh Arabic,84,&H00C7F1FF,&H00C7F1FF,&H00101010,&H35000000,0,0,0,0,100,100,0,0,1,3.4,2,5,60,60,0,1
Style: Ornament,Noto Sans,26,&H007DC7E7,&H007DC7E7,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,2,1,5,60,60,0,1
Style: English,Noto Sans,46,&H00FFFFFF,&H00FFFFFF,&H00101010,&H55000000,0,0,0,0,100,100,0,0,3,2.2,2,8,70,70,0,1
Style: Progress,Noto Sans,18,&H007DC7E7,&H007DC7E7,&H00101010,&H30000000,0,0,0,0,100,100,2,0,3,1,1,8,60,60,0,1
Style: Footer,Noto Sans,15,&H00D6CFC1,&H00D6CFC1,&H00101010,&H00000000,0,0,0,0,100,100,2,0,1,1,0,8,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"

        path.write_text(ass, encoding="utf-8")


# ============================================================================
# 11. Video renderer
# ============================================================================

class VideoRenderer:
    def __init__(self, typography: TypographyEngine):
        self.typography = typography

    def prepare_background(
        self,
        source: Path,
        destination: Path,
        duration: float,
        cycle: int,
        visual_id: int,
    ) -> str:
        # Deterministic camera profile. Each visual gets a gentle but non-identical
        # motion pattern. No aggressive zooming: the Quran remains the focus.
        profiles = [
            "x='(iw-1080)/2 + (iw-1080)/2*0.28*sin(2*PI*t/47)':y='(ih-1920)/2 + (ih-1920)/2*0.18*cos(2*PI*t/59)'",
            "x='(iw-1080)/2 + (iw-1080)/2*0.20*cos(2*PI*t/53)':y='(ih-1920)/2 + (ih-1920)/2*0.20*sin(2*PI*t/61)'",
            "x='(iw-1080)/2 + (iw-1080)/2*0.16*sin(2*PI*t/67)':y='(ih-1920)/2 + (ih-1920)/2*0.25*sin(2*PI*t/71)'",
            "x='(iw-1080)/2 + (iw-1080)/2*0.22*cos(2*PI*t/73)':y='(ih-1920)/2 + (ih-1920)/2*0.14*cos(2*PI*t/83)'",
        ]
        profile = profiles[(cycle + visual_id) % len(profiles)]

        run_cmd([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(source),
            "-t", f"{duration:.3f}",
            "-vf",
            (
                "scale=1300:2311:force_original_aspect_ratio=increase,"
                f"crop=1080:1920:{profile},"
                f"fps={FPS},format=yuv420p,"
                "eq=contrast=1.03:saturation=1.05"
            ),
            "-an",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "16",
            "-movflags", "+faststart",
            str(destination),
        ])
        return profile

    def render(
        self,
        background: Path,
        overlay: Path,
        subtitles: Path,
        audio: Path,
        output: Path,
        duration: float,
    ) -> None:
        fade_out_start = max(0.0, duration - 0.8)
        font_dir = "/usr/share/fonts/truetype/noto"
        filter_complex = (
            "[0:v][1:v]overlay=0:0:shortest=1[bg];"
            f"[bg]subtitles={subtitles}:fontsdir={font_dir},"
            f"fade=t=in:st=0:d=0.6,fade=t=out:st={fade_out_start:.3f}:d=0.8[vout];"
            f"[2:a]afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_start:.3f}:d=0.8[aout]"
        )

        run_cmd([
            "ffmpeg", "-y",
            "-i", str(background),
            "-loop", "1",
            "-i", str(overlay),
            "-i", str(audio),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "16",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output),
        ])


# ============================================================================
# 12. Hard quality assurance
# ============================================================================

class QualityAssurance:
    @staticmethod
    def probe(path: Path) -> dict[str, Any]:
        result = run_cmd([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size,format_name",
            "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate",
            "-of", "json",
            str(path),
        ], capture=True)
        return json.loads(result.stdout)

    def validate_video(self, path: Path, expected_duration: float) -> dict[str, Any]:
        if not path.exists() or path.stat().st_size < 100_000:
            raise RuntimeError("Final MP4 is missing or unexpectedly small.")

        info = self.probe(path)
        streams = info.get("streams", [])
        format_info = info.get("format", {})
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video or not audio:
            raise RuntimeError("Final MP4 must contain both video and audio.")
        if video.get("codec_name") != "h264":
            raise RuntimeError(f"Unexpected video codec: {video.get('codec_name')}")
        if audio.get("codec_name") not in ("aac", "mp4a"):
            raise RuntimeError(f"Unexpected audio codec: {audio.get('codec_name')}")

        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        duration = float(format_info.get("duration") or 0)
        size = int(format_info.get("size") or 0)

        if (width, height) != (W, H):
            raise RuntimeError(f"Wrong output dimensions: {width}x{height}")
        if size < 100_000:
            raise RuntimeError("Final MP4 is unexpectedly small.")
        if duration <= 0:
            raise RuntimeError("Final MP4 has an invalid duration.")
        if abs(duration - expected_duration) > 0.40:
            raise RuntimeError(
                f"Final A/V duration mismatch: expected={expected_duration:.3f}s output={duration:.3f}s"
            )

        return {
            "width": width,
            "height": height,
            "duration": duration,
            "size": size,
            "fps": video.get("r_frame_rate"),
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
            "audio_channels": audio.get("channels"),
            "audio_sample_rate": audio.get("sample_rate"),
        }


# ============================================================================
# 13. Publication package and queue
# ============================================================================

class Publisher:
    def __init__(self):
        self.queue_path = QUEUE

    def package(
        self,
        final_video: Path,
        metadata: dict[str, Any],
        chapter_name: str,
        chapter_id: int,
        first_ayah: int,
        last_ayah: int,
        cycle: int,
    ) -> tuple[Path, Path]:
        safe = safe_slug(chapter_name)
        if first_ayah == last_ayah:
            ayah_label = f"Ayah-{first_ayah}"
        else:
            ayah_label = f"Ayahs-{first_ayah}-to-{last_ayah}"

        name = f"Surah-{safe}-Chapter-{chapter_id}-{ayah_label}-Cycle-{cycle}.mp4"
        video_path = PUBLISH / name
        metadata_path = PUBLISH / f"{video_path.stem}.json"

        shutil.copy2(final_video, video_path)
        metadata["published_file"] = video_path.name
        metadata["metadata_file"] = metadata_path.name
        metadata["published_sha256"] = sha256_file(video_path)
        metadata["published_at"] = utc_now()
        atomic_write_json(metadata_path, metadata)
        return video_path, metadata_path

    def enqueue(self, metadata: dict[str, Any], video_path: Path, metadata_path: Path) -> None:
        queue = load_json(self.queue_path, [])
        if not isinstance(queue, list):
            queue = []

        # Avoid duplicate queue entries if the same publication is rerun.
        key = metadata.get("published_sha256")
        queue = [item for item in queue if item.get("published_sha256") != key]
        queue.append({
            "status": "pending",
            "video_file": video_path.name,
            "metadata_file": metadata_path.name,
            "published_sha256": key,
            "cycle": metadata.get("cycle"),
            "surah": metadata.get("surah", {}).get("name"),
            "chapter": metadata.get("surah", {}).get("number"),
            "verse_keys": metadata.get("verse_keys", []),
            "duration_seconds": metadata.get("section", {}).get("duration_seconds"),
            "created_at": utc_now(),
        })
        atomic_write_json(self.queue_path, queue)


# ============================================================================
# 14. Git progress commit
# ============================================================================

class GitProgress:
    def commit_if_enabled(self) -> None:
        if not COMMIT_PROGRESS:
            return

        run_cmd(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"])
        run_cmd(["git", "-C", str(ROOT), "config", "user.name", "quran-pipeline-bot"])
        run_cmd(["git", "-C", str(ROOT), "config", "user.email", "pipeline@localhost"])
        run_cmd([
            "git", "-C", str(ROOT), "add",
            "data/progress.json",
            "data/quran_index.json",
            "data/publish_queue.json",
        ])

        diff = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--cached", "--quiet"],
            check=False,
        )
        if diff.returncode == 0:
            log("No progress changes to commit.")
            return

        run_cmd([
            "git", "-C", str(ROOT), "commit",
            "-m", "Advance Quran production progress",
        ])
        log("Progress committed to git.")

        if not GIT_PUSH_PROGRESS:
            return

        last_error = "unknown push error"
        for attempt in range(1, GIT_PUSH_RETRIES + 1):
            try:
                # Fetch the remote first so a harmless remote advancement does not
                # leave the runner permanently unable to persist its checkpoint.
                run_cmd([
                    "git", "-C", str(ROOT), "fetch",
                    GIT_REMOTE, GIT_BRANCH,
                ])
                run_cmd([
                    "git", "-C", str(ROOT), "rebase",
                    f"{GIT_REMOTE}/{GIT_BRANCH}",
                ])
                run_cmd([
                    "git", "-C", str(ROOT), "push",
                    GIT_REMOTE, f"HEAD:{GIT_BRANCH}",
                ])
                log(f"Progress pushed to {GIT_REMOTE}/{GIT_BRANCH}.")
                return
            except Exception as exc:
                last_error = str(exc)
                subprocess.run(
                    ["git", "-C", str(ROOT), "rebase", "--abort"],
                    check=False,
                    capture_output=True,
                )
                if attempt < GIT_PUSH_RETRIES:
                    delay = min(2 ** attempt, 15)
                    log(f"Git push attempt {attempt} failed; retrying in {delay}s: {exc}", "WARN")
                    time.sleep(delay)

        raise RuntimeError(
            f"Progress was committed locally but could not be pushed after "
            f"{GIT_PUSH_RETRIES} attempts: {last_error}"
        )


# ============================================================================
# 15. Main orchestration
# ============================================================================

class QuranProduction:
    def __init__(self):
        ensure_binary("ffmpeg")
        ensure_binary("ffprobe")
        self.http = HttpClient()
        self.quran = QuranFoundationClient(self.http)
        self.repository = QuranRepository(self.quran)
        self.verses = self.repository.load_or_build()
        self.progress = ProgressStore(self.verses)
        self.progress.begin_cycle_if_needed()
        self.meaning = MeaningEngine(self.progress)
        self.planner = PassagePlanner(self.verses, self.progress, self.meaning)
        self.audio = AudioEngine(self.http)
        self.pexels = PexelsClient(self.http)
        self.visuals = VisualEngine(self.pexels, self.progress, self.http)
        self.typography = TypographyEngine()
        self.renderer = VideoRenderer(self.typography)
        self.qa = QualityAssurance()
        self.publisher = Publisher()
        self.git = GitProgress()

    @staticmethod
    def _audio_timeline(audio_items: list[AudioItem]) -> list[dict[str, Any]]:
        cursor = 0.0
        timeline: list[dict[str, Any]] = []
        for item in audio_items:
            start = cursor
            end = cursor + item.duration
            timeline.append({
                "verse_key": item.verse_key,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "duration_seconds": round(item.duration, 3),
            })
            cursor = end
        return timeline

    def run(self) -> dict[str, Any]:
        start_time = time.time()
        cycle = self.progress.cycle
        start_index = self.planner.choose_start()
        start_verse = self.verses[start_index]

        log(
            f"Planning next section: cycle={cycle} "
            f"start={start_verse.verse_key} "
            f"surah={start_verse.chapter_name}"
        )

        # Fetch enough audio metadata to decide the passage length. The actual
        # files are cached, so this is not an expensive repeat download.
        duration_map: dict[str, float] = {}
        i = start_index
        while i < len(self.verses):
            verse = self.verses[i]
            if verse.verse_key in self.progress.used:
                break
            if verse.chapter_id != start_verse.chapter_id:
                break
            if (
                i > start_index
                and start_verse.ruku_number is not None
                and verse.ruku_number != start_verse.ruku_number
                and sum(duration_map.values()) >= TARGET_MIN
            ):
                break
            item = self.audio.fetch(verse.verse_key)
            duration_map[verse.verse_key] = item.duration
            proposed = sum(duration_map.values())
            if proposed >= TARGET_MIN:
                break
            i += 1

        selected = self.planner.select_passage(start_index, duration_map)
        keys = [v.verse_key for v in selected]

        # Ensure the duration planner has everything it needs; if the final
        # passage contains one extra ayah at the edge, fetch it now.
        for verse in selected:
            if verse.verse_key not in duration_map:
                duration_map[verse.verse_key] = self.audio.fetch(verse.verse_key).duration

        planned_duration = sum(duration_map[v.verse_key] for v in selected)
        if planned_duration < TARGET_MIN:
            log(
                f"Selected passage is {planned_duration:.2f}s, below target because "
                "the next semantic boundary would be crossed. Keeping coherence.",
                "WARN",
            )
        if planned_duration > TARGET_MAX + 0.05:
            raise RuntimeError(
                f"Planner exceeded target maximum: {planned_duration:.2f}s"
            )

        # A stable section ID makes reruns inspectable.
        section_id = (
            f"cycle-{cycle}-"
            f"{keys[0].replace(':', '-')}-"
            f"{keys[-1].replace(':', '-')}"
        )
        section_dir = BUILD / section_id
        section_dir.mkdir(parents=True, exist_ok=True)

        log(
            f"Selected {keys[0]} → {keys[-1]} "
            f"({len(selected)} ayahs, planned={planned_duration:.2f}s)"
        )

        if DRY_RUN:
            theme, queries, _fallback = self.meaning.visual_queries(selected, cycle)
            result = {
                "dry_run": True,
                "cycle": cycle,
                "verse_keys": keys,
                "surah": start_verse.chapter_name,
                "theme": theme,
                "planned_duration": planned_duration,
                "queries": queries,
            }
            atomic_write_json(section_dir / "dry-run.json", result)
            return result

        # ------------------------------------------------------------
        # Audio
        # ------------------------------------------------------------
        audio_items, section_audio, audio_duration = self.audio.prepare(
            selected,
            section_dir,
        )

        # ------------------------------------------------------------
        # Meaning + visual selection
        # ------------------------------------------------------------
        theme, queries, fallback_queries = self.meaning.visual_queries(selected, cycle)
        visual = self.visuals.choose(
            queries,
            fallback_queries,
            audio_duration,
        )
        source_video = section_dir / "pexels-source.mp4"
        self.visuals.download(visual, source_video)

        # ------------------------------------------------------------
        # Background and overlay
        # ------------------------------------------------------------
        background = section_dir / "background.mp4"
        camera_profile = self.renderer.prepare_background(
            source_video,
            background,
            audio_duration,
            cycle,
            visual.video_id,
        )

        overlay = section_dir / "overlay.png"
        self.typography.make_overlay(overlay)

        # ------------------------------------------------------------
        # Subtitles / frontend
        # ------------------------------------------------------------
        subtitles = section_dir / "subtitles.ass"
        self.typography.make_ass(
            subtitles,
            selected,
            audio_items,
            start_verse.chapter_name,
            start_verse.chapter_id,
            start_verse.chapter_name_arabic,
        )

        final_video = section_dir / "quran-section.mp4"
        self.renderer.render(
            background,
            overlay,
            subtitles,
            section_audio,
            final_video,
            audio_duration,
        )

        # ------------------------------------------------------------
        # QA
        # ------------------------------------------------------------
        qa = self.qa.validate_video(final_video, audio_duration)
        log(
            f"Media QA PASSED: {qa['width']}x{qa['height']} "
            f"{qa['duration']:.2f}s H.264/AAC"
        )

        # ------------------------------------------------------------
        # Metadata
        # ------------------------------------------------------------
        metadata: dict[str, Any] = {
            "schema_version": 4,
            "cycle": cycle,
            "verse_keys": keys,
            "surah": {
                "number": start_verse.chapter_id,
                "name": start_verse.chapter_name,
                "name_arabic": start_verse.chapter_name_arabic,
            },
            "section": {
                "first_ayah": selected[0].verse_number,
                "last_ayah": selected[-1].verse_number,
                "ayah_count": len(selected),
                "duration_seconds": round(audio_duration, 3),
            },
            "audio": {
                "reciter": RECITER,
                "source": "EveryAyah / Ghamadi_40kbps",
                "duration_seconds": round(audio_duration, 3),
                "verse_durations": {
                    item.verse_key: round(item.duration, 3)
                    for item in audio_items
                },
                "timeline": self._audio_timeline(audio_items),
            },
            "translation": {
                "resource_id": TRANSLATION_ID,
                "name": TRANSLATION_NAME,
            },
            "meaning": {
                "theme": theme,
                "selection_mode": "quran-order" if cycle == 1 else "meaning-ranked-contiguous",
                "visual_query": visual.query,
            },
            "visual": {
                "provider": "Pexels",
                "video_id": visual.video_id,
                "source_url": visual.video_url,
                "file_url": visual.file_url,
                "photographer": visual.photographer,
                "photographer_url": visual.photographer_url,
                "fresh_for_cycle": visual.video_id not in self.progress.used_visuals,
                "visual_duration_seconds": visual.duration,
                "source_width": visual.width,
                "source_height": visual.height,
                "camera_profile": camera_profile,
            },
            "render": {
                "width": qa["width"],
                "height": qa["height"],
                "fps": qa["fps"],
                "video_codec": qa["video_codec"],
                "audio_codec": qa["audio_codec"],
                "audio_channels": qa["audio_channels"],
                "audio_sample_rate": qa["audio_sample_rate"],
                "layout": (
                    "Surah title / Arabic Surah name / chapter-ayah / source / "
                    "Arabic verse / Islamic ornament / English translation / progress"
                ),
                "fonts": {
                    "arabic": "Noto Naskh Arabic",
                    "english": "Noto Sans",
                },
            },
            "qa": {
                "status": "passed",
                "final_sha256": sha256_file(final_video),
                "validated_at": utc_now(),
            },
        }

        # ------------------------------------------------------------
        # Publication — only after QA
        # ------------------------------------------------------------
        publish_video, publish_metadata = self.publisher.package(
            final_video,
            metadata,
            start_verse.chapter_name,
            start_verse.chapter_id,
            selected[0].verse_number,
            selected[-1].verse_number,
            cycle,
        )
        self.publisher.enqueue(metadata, publish_video, publish_metadata)

        # ------------------------------------------------------------
        # Progress — only after publication succeeded
        # ------------------------------------------------------------
        visual_is_fresh = visual.video_id not in self.progress.used_visuals

        self.progress.commit(
            keys=keys,
            visual_id=visual.video_id,
            theme=theme,
            metadata={
                "cycle": cycle,
                "verse_keys": keys,
                "surah": start_verse.chapter_name,
                "chapter": start_verse.chapter_id,
                "duration_seconds": round(audio_duration, 3),
                "visual_id": visual.video_id,
                "theme": theme,
                "published_file": publish_video.name,
                "published_sha256": metadata["published_sha256"],
            },
        )

        self.git.commit_if_enabled()

        # ------------------------------------------------------------
        # Final report
        # ------------------------------------------------------------
        elapsed = time.time() - start_time
        result = {
            "success": True,
            "cycle": cycle,
            "verse_keys": keys,
            "surah": start_verse.chapter_name,
            "surah_number": start_verse.chapter_id,
            "duration_seconds": round(audio_duration, 3),
            "reciter": RECITER,
            "translation": TRANSLATION_NAME,
            "theme": theme,
            "visual_id": visual.video_id,
            "visual_fresh": visual_is_fresh,
            "output": str(publish_video.relative_to(ROOT)),
            "metadata": str(publish_metadata.relative_to(ROOT)),
            "elapsed_seconds": round(elapsed, 2),
        }

        log("================================================")
        log("QURAN PRODUCTION SECTION PASSED")
        log("================================================")
        log(f"Cycle: {cycle}")
        log(f"Surah: {start_verse.chapter_name} ({start_verse.chapter_id})")
        log(f"Verses: {', '.join(keys)}")
        log(f"Audio: {RECITER}")
        log(f"Translation: {TRANSLATION_NAME} ({TRANSLATION_ID})")
        log(f"Theme: {theme}")
        log(f"Visual: Pexels {visual.video_id}")
        log(f"Duration: {audio_duration:.2f}s")
        log(f"Output: {publish_video}")
        log(f"Elapsed: {elapsed:.2f}s")
        log("================================================")
        return result


def main() -> int:
    with ProductionLock(LOCK):
        try:
            production = QuranProduction()
            result = production.run()
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return 0
        except Exception as exc:
            log(f"PRODUCTION FAILED: {exc}", "ERROR")
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
