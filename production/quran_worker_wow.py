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
    Reference-style Arabic + English typography with Pillow/libraqm
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
  ALLOW_VISUAL_REUSE=1   (legacy — kept for compatibility only; reuse of an
                          already-used background is now ALWAYS the final
                          safety net regardless of this flag, so a run can
                          never fail just because every matching visual has
                          been used before. A brand-new/never-used visual is
                          still always preferred whenever one is available.)
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
  QURAN_KEEP_BUILD=1     (keep output/production/<section>/ after a
                          successful publish, for debugging. By default the
                          per-section build folder — source/background MP4 + timed transparent typography frames
                          attempts, raw audio, overlay, subtitles, captions —
                          is deleted right after publish so long-running
                          automation never fills up disk. The small, shared
                          audio cache in output/production/_audio-cache is
                          never deleted by this flag; it is bounded on its
                          own by the size of the Quran.)

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
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, features


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
TARGET_MAX = float(os.environ.get("QURAN_TARGET_MAX", "153"))
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
    print(f"[{now}] [{level}] {message}", file=sys.stderr, flush=True)


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


def load_json(path: Path, default: Any = None) -> Any:
    """
    Load JSON safely.

    For ordinary optional JSON files, return `default` when the file
    does not exist.

    For an existing but corrupt JSON file, STOP instead of silently
    replacing it with an empty/default structure.

    This is especially important for progress.json because silently
    resetting a corrupt progress file could cause Quran verses to be
    repeated.
    """
    if not path.exists():
        return default

    try:
        text = path.read_text(encoding="utf-8")

        if not text.strip():
            raise RuntimeError(
                f"{path} exists but is empty."
            )

        return json.loads(text)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"CRITICAL: {path} contains invalid/corrupt JSON. "
            f"Refusing to continue with a default state because "
            f"doing so could reset production progress. "
            f"Repair or restore the file before running again. "
            f"JSON error: {exc}"
        ) from exc

    except OSError as exc:
        raise RuntimeError(
            f"CRITICAL: unable to read {path}. "
            f"Refusing to continue because production state may be "
            f"unavailable. Error: {exc}"
        ) from exc

    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"CRITICAL: {path} is not valid UTF-8 JSON. "
            f"Refusing to reset production state. "
            f"Error: {exc}"
        ) from exc


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
        text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.I | re.S)
        text = re.sub(r"<foot_note[^>]*>.*?</foot_note>", "", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", text)
        text = re.sub(r"\[\s*\d{1,3}\s*\]", "", text)
        text = re.sub(r"(?<=[.!?,;:])\s*\d{1,3}(?=\s|$)", "", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
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
            log(
                "progress.json was not a JSON object; "
                "starting from a safe empty state.",
                "WARN",
            )
            self.state = {}

        # ------------------------------------------------------------------
        # State schema
        # ------------------------------------------------------------------
        self.state.setdefault("schema_version", self.SCHEMA)
        self.state.setdefault("cycle", 1)
        self.state.setdefault("used_verses", [])
        self.state.setdefault("used_pexels_ids", [])
        self.state.setdefault("visual_history", [])
        self.state.setdefault("theme_usage", {})
        self.state.setdefault("completed_cycles", [])
        self.state.setdefault("published_count", 0)
        self.state.setdefault("last_section", None)
        self.state.setdefault("history", [])

        # ------------------------------------------------------------------
        # Quran progress
        # ------------------------------------------------------------------
        raw_used = self.state.get("used_verses", [])

        if not isinstance(raw_used, list):
            log(
                "Invalid used_verses state detected; rebuilding it safely.",
                "WARN",
            )
            raw_used = []

        self.used = {
            str(key)
            for key in raw_used
            if str(key) in self.valid_keys
        }

        # ------------------------------------------------------------------
        # Pexels visual memory
        #
        # Keep a large rolling memory instead of allowing progress.json
        # to grow forever.
        #
        # 2000 IDs is intentionally generous. It means the pipeline will
        # normally avoid repeating a background for a very long time.
        # ------------------------------------------------------------------
        MAX_VISUAL_MEMORY = 2000

        raw_visuals = self.state.get("used_pexels_ids", [])

        if not isinstance(raw_visuals, list):
            log(
                "Invalid used_pexels_ids state detected; "
                "rebuilding safely.",
                "WARN",
            )
            raw_visuals = []

        cleaned_visuals: list[int] = []

        for value in raw_visuals:
            try:
                visual_id = int(value)
            except (TypeError, ValueError):
                continue

            if visual_id > 0 and visual_id not in cleaned_visuals:
                cleaned_visuals.append(visual_id)

        # Keep most recent IDs.
        cleaned_visuals = cleaned_visuals[-MAX_VISUAL_MEMORY:]

        self.used_visuals = set(cleaned_visuals)

        self.state["used_pexels_ids"] = cleaned_visuals

        # ------------------------------------------------------------------
        # Visual usage order
        #
        # This is separate from used_pexels_ids because it tells us which
        # visual was used most recently. That makes reuse genuinely
        # least-recently-used instead of guessing from the numeric Pexels ID.
        # ------------------------------------------------------------------
        raw_visual_history = self.state.get("visual_history", [])

        if not isinstance(raw_visual_history, list):
            raw_visual_history = []

        cleaned_visual_history: list[int] = []

        for value in raw_visual_history:
            try:
                visual_id = int(value)
            except (TypeError, ValueError):
                continue

            if visual_id > 0:
                # Remove previous occurrence so newest use is at the end.
                if visual_id in cleaned_visual_history:
                    cleaned_visual_history.remove(visual_id)

                cleaned_visual_history.append(visual_id)

        # Preserve IDs from used_pexels_ids that are not in visual_history.
        # Their exact recency is unknown, so they are treated as older.
        for visual_id in cleaned_visuals:
            if visual_id not in cleaned_visual_history:
                cleaned_visual_history.append(visual_id)

        cleaned_visual_history = cleaned_visual_history[-MAX_VISUAL_MEMORY:]

        self.state["visual_history"] = cleaned_visual_history

        # Keep visual memory synchronized and bounded.
        self.used_visuals = set(cleaned_visual_history)
        self.state["used_pexels_ids"] = cleaned_visual_history.copy()

        # ------------------------------------------------------------------
        # Theme intelligence
        #
        # IMPORTANT:
        # Do NOT reset this between Quran cycles.
        # It is deliberately persistent so Cycle 2, 3, 4... learn which
        # visual variants have already been used.
        # ------------------------------------------------------------------
        raw_theme_usage = self.state.get("theme_usage", {})

        if not isinstance(raw_theme_usage, dict):
            raw_theme_usage = {}

        self.theme_usage = {
            str(theme): max(0, int(count))
            for theme, count in raw_theme_usage.items()
            if isinstance(count, (int, float, str))
            and str(count).lstrip("-").isdigit()
        }

        # ------------------------------------------------------------------
        # Cycle information
        # ------------------------------------------------------------------
        try:
            self.cycle = max(1, int(self.state.get("cycle", 1)))
        except (TypeError, ValueError):
            self.cycle = 1

        # ------------------------------------------------------------------
        # Bound historical records.
        # ------------------------------------------------------------------
        MAX_HISTORY = 500
        MAX_COMPLETED_CYCLES = 100

        history = self.state.get("history", [])
        if not isinstance(history, list):
            history = []

        self.state["history"] = history[-MAX_HISTORY:]

        completed_cycles = self.state.get("completed_cycles", [])
        if not isinstance(completed_cycles, list):
            completed_cycles = []

        self.state["completed_cycles"] = completed_cycles[-MAX_COMPLETED_CYCLES:]

        # ------------------------------------------------------------------
        # Published count
        # ------------------------------------------------------------------
        try:
            self.state["published_count"] = max(
                0,
                int(self.state.get("published_count", 0)),
            )
        except (TypeError, ValueError):
            self.state["published_count"] = 0

        self.audit()

    def audit(self) -> None:
        """Hard integrity check on the Quran completeness invariant.

        This is the single most important check in the whole pipeline: it is
        what guarantees "no ayah is ever skipped, and the whole Qur'an is
        completed before anything repeats." It runs on every load, before any
        planning happens, and it STOPS the run rather than silently
        continuing if the state file is ever inconsistent (rule #16 of the
        rulebook: uncertain about Qur'an integrity -> STOP).
        """
        raw_used = self.state.get("used_verses", [])

        # 1) Every recorded "used" key must be a real, current verse_key.
        unknown = [k for k in raw_used if k not in self.valid_keys]
        if unknown:
            raise RuntimeError(
                f"progress.json references {len(unknown)} verse key(s) not present "
                f"in the current Quran index (e.g. {unknown[:3]}). STOPPING rather "
                "than risk skipping or double-counting ayahs."
            )

        # 2) No duplicates recorded as used.
        if len(raw_used) != len(set(raw_used)):
            raise RuntimeError(
                "progress.json contains duplicate entries in used_verses. STOPPING."
            )

        remaining = len(self.verses) - len(self.used)
        log(
            f"Cycle integrity: cycle={self.cycle} "
            f"used={len(self.used)}/{len(self.verses)} remaining={remaining}",
            "INFO",
        )

        # 3) If every verse is marked used, the NEXT run must roll into a new
        #    cycle rather than silently having nothing left to plan. This is
        #    enforced again in begin_cycle_if_needed(), this is just an early,
        #    loud signal so a stuck state is visible in logs immediately.
        if remaining == 0 and len(self.verses) > 0:
            log(
                f"Cycle {self.cycle} is fully complete "
                f"({len(self.verses)}/{len(self.verses)} ayahs). "
                "Next run will start Cycle "
                f"{self.cycle + 1} from Al-Fatihah 1:1.",
                "INFO",
            )

    def begin_cycle_if_needed(self) -> None:
        if len(self.used) < len(self.verses):
            return

        MAX_COMPLETED_CYCLES = 100

        completed_cycles = self.state.get("completed_cycles", [])
        if not isinstance(completed_cycles, list):
            completed_cycles = []

        completed_cycles.append({
            "cycle": self.cycle,
            "verses": len(self.verses),
            "completed_at": utc_now(),
            "published_count": int(
                self.state.get("published_count", 0)
            ),
        })

        # Safety: don't let completed cycle history grow forever.
        completed_cycles = completed_cycles[-MAX_COMPLETED_CYCLES:]

        self.cycle += 1

        # --------------------------------------------------------------
        # Quran selection resets.
        # --------------------------------------------------------------
        self.used = set()
        self.state["cycle"] = self.cycle
        self.state["used_verses"] = []

        # --------------------------------------------------------------
        # IMPORTANT:
        #
        # DO NOT reset theme_usage.
        #
        # Theme usage is our long-term visual intelligence.
        # Cycle 2 should know what Cycle 1 already used.
        # Cycle 3 should know what Cycles 1 + 2 used.
        # --------------------------------------------------------------
        self.state["theme_usage"] = self.theme_usage
        self.state["completed_cycles"] = completed_cycles

        log(
            f"QURAN CYCLE {self.cycle} STARTING — "
            f"every ayah of the previous cycle was covered. "
            f"Visual/theme intelligence is being preserved across cycles."
        )
        atomic_write_json(PROGRESS, self.state)

    def commit(
        self,
        *,
        keys: list[str],
        visual_id: int,
        theme: str,
        metadata: dict[str, Any],
    ) -> None:
        """
        Atomically commit one successfully published Quran section.

        Safety guarantees:
          - validates verse keys before changing state
          - validates Pexels visual ID
          - preserves Quran cycle integrity
          - keeps theme intelligence across cycles
          - keeps Pexels visual memory bounded
          - keeps visual LRU history bounded
          - keeps production history bounded
          - keeps completed-cycle history bounded
          - writes progress atomically
        """

        # ==============================================================
        # 1. Validate Quran verse keys BEFORE modifying anything.
        # ==============================================================

        if not keys:
            raise RuntimeError(
                "Progress commit refused: no Quran verse keys supplied."
            )

        invalid_keys = [
            key
            for key in keys
            if key not in self.valid_keys
        ]

        if invalid_keys:
            raise RuntimeError(
                "Progress commit refused: invalid Quran verse key(s): "
                f"{invalid_keys[:5]}"
            )

        # Prevent accidental duplicate keys inside one production.
        if len(keys) != len(set(keys)):
            raise RuntimeError(
                "Progress commit refused: duplicate Quran verse keys "
                "were supplied."
            )

        # ==============================================================
        # 2. Validate Pexels visual ID.
        # ==============================================================

        try:
            visual_id = int(visual_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Progress commit refused: invalid Pexels visual ID "
                f"{visual_id!r}."
            ) from exc

        if visual_id <= 0:
            raise RuntimeError(
                f"Progress commit refused: invalid Pexels visual ID "
                f"{visual_id}."
            )

        # ==============================================================
        # 3. Normalize theme.
        # ==============================================================

        theme = str(theme or "reflection").strip()

        if not theme:
            theme = "reflection"

        # ==============================================================
        # 4. Update Quran usage.
        # ==============================================================

        self.used.update(keys)

        # ==============================================================
        # 5. Persistent theme intelligence.
        #
        # IMPORTANT:
        # This is intentionally NOT reset when a new Quran cycle starts.
        # Cycle 2 learns from Cycle 1, Cycle 3 learns from 1 + 2, etc.
        # ==============================================================

        current_theme_count = self.theme_usage.get(theme, 0)

        try:
            current_theme_count = int(current_theme_count)
        except (TypeError, ValueError):
            current_theme_count = 0

        self.theme_usage[theme] = max(
            0,
            current_theme_count,
        ) + 1

        # ==============================================================
        # 6. BOUNDED PEXELS VISUAL MEMORY.
        #
        # Never allow this set to grow forever during a long-running
        # process.
        # ==============================================================

        MAX_VISUAL_MEMORY = 2000

        visual_history = self.state.get(
            "visual_history",
            [],
        )

        if not isinstance(visual_history, list):
            visual_history = []

            # Merge explicit LRU history with used-ID memory so an upgrade/restart
        # cannot accidentally forget previously-used visuals.
        legacy_visuals = self.state.get("used_pexels_ids", [])
        if not isinstance(legacy_visuals, list):
            legacy_visuals = []

        cleaned_visual_history: list[int] = []

        def append_visual_id(value: Any) -> None:
            try:
                existing_id = int(value)
            except (TypeError, ValueError):
                return
            if existing_id <= 0:
                return
            if existing_id in cleaned_visual_history:
                cleaned_visual_history.remove(existing_id)
            cleaned_visual_history.append(existing_id)

        for value in visual_history:
            append_visual_id(value)

        # Legacy IDs have less precise ordering; preserve only missing IDs.
        for value in legacy_visuals:
            try:
                existing_id = int(value)
            except (TypeError, ValueError):
                continue
            if existing_id > 0 and existing_id not in cleaned_visual_history:
                cleaned_visual_history.insert(0, existing_id)

        # Current visual becomes the newest-used visual.
        if visual_id in cleaned_visual_history:
            cleaned_visual_history.remove(visual_id)
        cleaned_visual_history.append(visual_id)

        cleaned_visual_history = cleaned_visual_history[-MAX_VISUAL_MEMORY:]
        self.state["visual_history"] = cleaned_visual_history
        self.used_visuals = set(cleaned_visual_history)

    # ==============================================================
    # 7. Keep Quran verse usage clean and deterministic.
    # ==============================================================

        ordered_used_verses = sorted(
            self.used,
            key=lambda key: self.id_by_key[key],
        )

        # ==============================================================
        # 8. Production history.
        # ==============================================================

        history = self.state.get(
            "history",
            [],
        )

        if not isinstance(history, list):
            history = []

        history_item = {
            "cycle": self.cycle,
            "verse_keys": list(keys),
            "visual_id": visual_id,
            "theme": theme,
            "visual_query": metadata.get(
                "visual_query"
            ),
            "published_file": metadata.get(
                "published_file"
            ),
            "published_sha256": metadata.get(
                "published_sha256"
            ),
            "created_at": utc_now(),
        }

        history.append(history_item)

        # Long-term safety limit.
        MAX_HISTORY = 500

        history = history[-MAX_HISTORY:]

        # ==============================================================
        # 9. Completed cycle history.
        # ==============================================================

        completed_cycles = self.state.get(
            "completed_cycles",
            [],
        )

        if not isinstance(completed_cycles, list):
            completed_cycles = []

        MAX_COMPLETED_CYCLES = 100

        completed_cycles = (
            completed_cycles[-MAX_COMPLETED_CYCLES:]
        )

        # ==============================================================
        # 10. Published count.
        # ==============================================================

        try:
            published_count = int(
                self.state.get(
                    "published_count",
                    0,
                )
            )
        except (TypeError, ValueError):
            published_count = 0

        published_count = max(
            0,
            published_count,
        ) + 1

        # ==============================================================
        # 11. Store the complete bounded state.
        # ==============================================================

        self.state.update({
            "schema_version": self.SCHEMA,
            "cycle": self.cycle,

            # Quran progress
            "used_verses": ordered_used_verses,

            # Bounded visual memory
            "used_pexels_ids": sorted(
                self.used_visuals
            ),
            "visual_history": cleaned_visual_history,

            # Persistent intelligence
            "theme_usage": self.theme_usage,

            # Bounded historical records
            "history": history,
            "completed_cycles": completed_cycles,

            # Counters / metadata
            "published_count": published_count,
            "last_section": metadata,
        })

        # ==============================================================
        # 12. Final sanity checks BEFORE writing.
        # ==============================================================

        if len(self.state["used_pexels_ids"]) > MAX_VISUAL_MEMORY:
            raise RuntimeError(
                "Internal safety check failed: "
                "used_pexels_ids exceeded the configured limit."
            )

        if len(self.state["visual_history"]) > MAX_VISUAL_MEMORY:
            raise RuntimeError(
                "Internal safety check failed: "
                "visual_history exceeded the configured limit."
            )

        if len(self.state["history"]) > MAX_HISTORY:
            raise RuntimeError(
                "Internal safety check failed: "
                "history exceeded the configured limit."
            )

        if len(self.state["completed_cycles"]) > MAX_COMPLETED_CYCLES:
            raise RuntimeError(
                "Internal safety check failed: "
                "completed_cycles exceeded the configured limit."
            )

        # ==============================================================
        # 13. Atomic persistence.
        #
        # Never directly overwrite progress.json.
        # ==============================================================
    
        atomic_write_json(
            PROGRESS,
            self.state,
        )


# ============================================================================
# 7. Meaning model and passage planner
# ============================================================================

THEMES: list[tuple[str, list[str], list[str]]] = [

    ("mercy", [
        "mercy", "merciful", "compassion", "forgive", "forgiveness",
        "forgiving", "gracious", "kind", "rahmah"
    ], [
        "soft golden sunrise over peaceful desert hills",
        "warm sunlight spreading across a quiet valley",
        "gentle sunrise over vast peaceful mountains",
        "golden light breaking through soft morning clouds",
        "warm lantern glowing inside quiet Islamic architecture",
        "open Quran manuscript illuminated by warm candlelight",
        "sunlight entering a peaceful mosque through geometric windows",
        "calm landscape glowing with soft golden morning light",
    ]),

    ("guidance", [
        "guidance", "guide", "guided", "guiding", "straight path",
        "path", "way", "direction"
    ], [
        "peaceful mountain path leading toward sunrise",
        "winding desert path toward distant golden light",
        "ancient stone pathway illuminated at dawn",
        "lantern illuminating a quiet stone passage",
        "mountain trail disappearing into morning mist",
        "peaceful road through vast open landscape at sunrise",
        "ancient Islamic archway with light beyond it",
        "single path through quiet hills under morning sky",
    ]),

    ("creation", [
        "creation", "created", "heavens", "signs", "universe",
        "sun", "moon", "stars", "sky", "skies", "cloud", "clouds"
    ], [
        "vast star-filled sky over dramatic mountains",
        "moon rising above distant desert mountains",
        "galaxy nebula deep space",
        "milky way over desert horizon",
        "slow moving clouds across vast blue sky",
        "sun rays breaking through enormous clouds",
        "full moon above distant mountains",
        "timelapse clouds moving across a vast horizon",
    ]),

    ("water", [
        "rain", "water", "river", "rivers", "spring",
        "springs", "stream", "streams"
    ], [
        "clear river flowing through green mountain valley",
        "gentle rain falling over peaceful landscape",
        "rain clouds moving over distant mountains",
        "fresh spring water flowing between mountain rocks",
        "peaceful waterfall surrounded by green landscape",
        "clear river reflecting mountains at sunrise",
        "raindrops on window glass",
        "fountain courtyard reflection",
    ]),

    ("garden", [
        "garden", "gardens", "fruit", "fruits", "green",
        "trees", "tree", "orchard", "orchards", "shade"
    ], [
        "lush green garden with soft sunlight through leaves",
        "peaceful orchard trees glowing in morning sunlight",
        "date palm oasis surrounded by green landscape",
        "lush garden with gentle flowing water",
        "green courtyard garden with Islamic arches",
        "vines growing across ancient stone walls",
        "peaceful botanical garden in soft morning light",
        "date palms beside clear water in desert oasis",
    ]),

    ("light", [
        "light", "bright", "brightness", "illuminate",
        "illuminated", "radiance", "shines", "shine"
    ], [
        "powerful sun rays breaking through dark clouds",
        "golden sunlight across a vast peaceful landscape",
        "soft dawn light over distant mountains",
        "sunbeams passing through clouds over desert",
        "warm lantern illuminating ancient stone architecture",
        "golden light entering through geometric Islamic windows",
        "sunlight through trees creating gentle rays",
        "bright horizon glowing over peaceful hills",
    ]),

    ("darkness", [
        "darkness", "dark", "night", "lightning", "thunder", "flash"
    ], [
        "vast peaceful night sky filled with stars",
        "moonlight breaking through heavy clouds",
        "distant lightning across dark mountain horizon",
        "dark clouds moving over a silent desert",
        "quiet mountains beneath deep blue night sky",
        "single lantern glowing in dark stone architecture",
        "moon rising through clouds over distant hills",
        "dark ocean beneath dramatic night sky",
    ]),

    ("patience", [
        "patience", "patient", "steadfast", "steadfastness",
        "persevere", "perseverance", "endure", "endurance"
    ], [
        "still mountain lake reflecting peaceful sky",
        "slow ocean waves beneath a calm horizon",
        "quiet desert dunes under soft evening light",
        "ancient weathered mountains under changing sky",
        "slow river flowing peacefully through valley",
        "hourglass with sand falling in warm light",
        "mist slowly moving across silent mountains",
        "calm horizon over vast empty desert",
    ]),

    ("gratitude", [
        "grateful", "gratitude", "thankful", "thanks", "thank",
        "blessing", "blessings", "favor", "favors"
    ], [
        "golden sunrise over abundant wheat fields",
        "warm sunlight across peaceful countryside",
        "golden harvest field moving gently in the breeze",
        "lush green valley beneath morning sunlight",
        "date palm oasis glowing in golden light",
        "peaceful countryside landscape at dawn",
        "sunrise over fertile green valley",
        "warm golden light across fields and mountains",
    ]),

    ("knowledge", [
        "knowledge", "wisdom", "wise", "learn", "learning",
        "understand", "understanding", "know", "knowing"
    ], [
        "open Quran manuscript pages in warm natural light",
        "detailed Arabic calligraphy on ancient stone",
        "ink pen beside an open manuscript",
        "old manuscript pages illuminated by soft sunlight",
        "Islamic geometric architecture close up",
        "ancient Islamic library interior without people",
        "ornate Arabic calligraphy on historic architecture",
        "Quran pages with soft golden light and shallow focus",
    ]),

    ("justice", [
        "justice", "just", "truth", "truthful", "truthfulness",
        "balance", "balanced", "fair", "measure", "scales"
    ], [
        "perfectly balanced mountains reflected in still water",
        "symmetrical Islamic architecture under clear sky",
        "balanced stone scales in ancient architecture",
        "perfectly straight horizon over open landscape",
        "symmetrical mosque courtyard geometric patterns",
        "still water reflecting symmetrical mountains",
        "balanced stones beside calm water",
        "geometric Islamic architecture in perfect symmetry",
    ]),

    ("warning", [
        "warning", "warn", "warned", "punishment", "punished",
        "wrongdoers", "wrongdoer", "wrong", "evil", "fire",
        "torment", "hell", "hellfire", "wrath"
    ], [
        "dramatic storm clouds over barren mountains",
        "dark storm approaching across vast desert",
        "distant lightning across an empty horizon",
        "cracked dry earth beneath threatening clouds",
        "dark turbulent ocean beneath storm clouds",
        "powerful thunderstorm over distant mountains",
        "heavy clouds covering a barren landscape",
        "dramatic dark sky over empty desert terrain",
    ]),

    ("paradise", [
        "paradise", "bliss", "jannah", "eternal", "fountains"
    ], [
        "lush eternal garden with clear flowing rivers",
        "beautiful green garden with peaceful flowing water",
        "date palm oasis with clear streams and greenery",
        "serene garden surrounded by flowing water",
        "lush garden courtyard with elegant fountain",
        "golden clouds glowing sunset light",
        "misty garden soft morning light",
        "beautiful oasis surrounded by trees and clear water",
    ]),

    ("repentance", [
        "repent", "repentance", "return", "returned",
        "turn", "turning", "seek forgiveness"
    ], [
        "quiet path leading toward sunrise over mountains",
        "golden dawn appearing after dark clouds",
        "peaceful road disappearing toward distant light",
        "sunrise over empty desert after night",
        "soft light breaking through clouds over hills",
        "ancient doorway opening toward warm morning light",
        "quiet mountain valley at first light",
        "lantern illuminating a path through darkness",
    ]),

    ("trust", [
        "trust", "rely", "reliance", "trustworthy", "depend",
        "relied", "entrust"
    ], [
        "vast calm ocean beneath peaceful sunrise",
        "mountains beneath a wide open sky",
        "quiet desert landscape under golden dawn",
        "still lake reflecting enormous sky",
        "peaceful valley surrounded by mountains",
        "soft clouds moving over calm landscape",
        "wide horizon over untouched desert",
        "sunrise illuminating distant mountain peaks",
    ]),

    ("hope", [
        "hope", "hopeful", "hoped", "hopefulness"
    ], [
        "golden sunrise emerging over distant mountains",
        "light breaking through clouds over peaceful valley",
        "first light appearing across desert horizon",
        "bright horizon beyond dark mountains",
        "sunrise reflected across calm water",
        "warm morning light spreading across open fields",
        "soft golden rays through clouds",
        "peaceful dawn over a vast landscape",
    ]),

    ("worship", [
        "prayer", "pray", "prays", "worship", "worships",
        "prostration", "bow", "bowing", "mosque"
    ], [
        "quiet mosque interior with geometric architecture",
        "empty mosque courtyard illuminated by morning light",
        "Islamic prayer hall with sunlight through arches",
        "ornate mosque dome interior with soft light",
        "peaceful Islamic courtyard at sunrise",
        "geometric mosque architecture in golden hour",
        "empty prayer space with warm natural light",
        "mosque arches illuminated by soft evening sunlight",
    ]),

    ("remembrance", [
        "remember", "remembrance", "remind", "reminded",
        "mention", "glorify", "glory", "praise"
    ], [
        "open Quran beside warm glowing lantern",
        "Arabic calligraphy illuminated by soft golden light",
        "Quran pages with gentle sunlight across the paper",
        "quiet mosque interior with warm lantern light",
        "close up Arabic calligraphy on Islamic architecture",
        "peaceful dawn light entering mosque windows",
        "open manuscript in a quiet contemplative setting",
        "soft candlelight across Quran manuscript pages",
    ]),

    ("provision", [
        "provision", "sustenance", "food", "rizq",
        "provide", "provided", "provisions"
    ], [
        "golden wheat field beneath warm sunlight",
        "abundant date palm oasis",
        "lush agricultural valley under morning light",
        "green fields stretching toward mountains",
        "fruit trees glowing in warm sunlight",
        "peaceful farmland beneath golden sky",
        "harvest fields at golden hour",
        "fertile green valley after rainfall",
    ]),

    ("trials", [
        "trial", "trials", "test", "tested", "testing",
        "difficulty", "hardship", "affliction", "calamity"
    ], [
        "dramatic mountains beneath changing storm clouds",
        "rough ocean waves beneath dark sky",
        "desert landscape under harsh sunlight",
        "mountain path through heavy mist",
        "storm clouds passing over distant mountains",
        "rocky landscape beneath dramatic sky",
        "rain moving across an empty desert",
        "strong waves against a rugged coastline",
    ]),

    ("mortality", [
        "death", "die", "dies", "dead", "dying",
        "mortal", "mortality", "grave", "graves"
    ], [
        "quiet sunset disappearing behind distant mountains",
        "empty desert beneath fading evening light",
        "fallen leaves on a quiet natural landscape",
        "ancient weathered stone beneath evening sky",
        "sunset over a silent mountain valley",
        "last light across a vast empty landscape",
        "quiet path disappearing into evening mist",
        "moon rising over a silent desert",
    ]),

    ("afterlife", [
        "resurrection", "resurrect", "hereafter", "afterlife"
    ], [
        "vast dramatic horizon beneath powerful clouds",
        "mountains beneath an immense golden sky",
        "endless desert horizon at dawn",
        "dramatic sunrise over vast empty landscape",
        "powerful clouds opening above distant mountains",
        "vast empty plain beneath a glowing horizon",
        "mountain landscape under dramatic celestial light",
        "endless horizon disappearing into golden mist",
    ]),

    ("judgment", [
        "judgment", "judgement", "day of judgment",
        "account", "reckoning", "deeds", "deed"
    ], [
        "dramatic sky over a vast empty plain",
        "powerful clouds over distant mountains",
        "vast desert horizon beneath dark clouds",
        "intense sunrise over an empty landscape",
        "dramatic mountain silhouette beneath glowing sky",
        "wide empty plain beneath enormous clouds",
        "storm clouds opening over distant golden horizon",
        "vast landscape beneath a solemn dramatic sky",
    ]),

    ("power", [
        "power", "powerful", "might", "mighty", "strength",
        "great", "greatness", "majesty", "majestic"
    ], [
        "towering mountains beneath enormous dramatic sky",
        "vast ocean beneath powerful storm clouds",
        "massive mountain range under golden sunlight",
        "dramatic clouds moving across immense landscape",
        "towering cliffs beside calm ocean",
        "vast desert beneath overwhelming sky",
        "majestic mountain peaks above clouds",
        "powerful sunrise illuminating enormous mountains",
    ]),

    ("earth", [
        "earth", "land", "lands", "mountain", "mountains",
        "valley", "valleys", "plain", "plains"
    ], [
        "majestic mountain range under clear blue sky",
        "wide green valley surrounded by mountains",
        "vast desert landscape beneath open sky",
        "rocky mountains glowing at sunrise",
        "aerial view of winding mountain valleys",
        "peaceful open plain beneath golden clouds",
        "dramatic mountain landscape at golden hour",
        "vast untouched landscape stretching to horizon",
    ]),

    ("sea", [
        "sea", "ocean", "wave", "waves", "shore",
        "coast", "coastline"
    ], [
        "calm ocean horizon at sunrise",
        "slow waves rolling onto quiet empty shore",
        "vast blue ocean beneath dramatic clouds",
        "golden sunset reflecting across calm sea",
        "powerful waves against rocky coastline",
        "moonlight reflecting across quiet ocean",
        "aerial view of clear ocean meeting coastline",
        "peaceful sea beneath soft morning clouds",
    ]),

    ("desert", [
        "desert", "dunes", "sand", "wilderness", "wasteland",
        "barren"
    ], [
        "vast golden desert dunes at sunrise",
        "wind moving gently across desert sand",
        "empty desert beneath dramatic sky",
        "moonlight across peaceful desert dunes",
        "golden desert horizon at sunset",
        "desert mountains beneath morning light",
        "wide untouched desert landscape",
        "soft shadows across endless sand dunes",
    ]),

    ("architecture", [
        "mihrab", "minaret", "architecture", "arches",
        "courtyard", "Islamic architecture"
    ], [
        "empty mosque courtyard with geometric architecture",
        "Islamic arches illuminated by golden sunlight",
        "ornate geometric mosque interior without people",
        "Islamic architecture beneath evening sky",
        "mosque dome illuminated by warm sunrise",
        "ancient Islamic courtyard with fountain",
        "geometric Islamic tile patterns close up",
        "peaceful mosque corridor with soft natural light",
    ]),

    ("revelation", [
        "Quran", "revelation", "revealed", "reveal",
        "scripture", "book", "verses", "ayah", "ayahs"
    ], [
        "open Quran manuscript under soft warm light",
        "close up Quran pages with Arabic calligraphy",
        "Quran manuscript beside glowing lantern",
        "Arabic calligraphy on aged parchment",
        "open Islamic manuscript illuminated by sunlight",
        "detailed Quran pages with shallow depth of field",
        "ink and parchment beside open Quran",
        "ornate Arabic calligraphy in warm light",
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
    "signs": 5,
    "creation": 5,
    "created": 4,
    "heavens": 4,
    "earth": 4,
    "paradise": 7,
    "jannah": 8,
    "gardens": 5,
    "garden": 5,
    "rivers": 4,
    "river": 4,
    "water": 4,
    "justice": 6,
    "truth": 5,
    "truthful": 5,
    "light": 4,
    "darkness": 5,
    "warning": 4,
    "hell": 6,
    "hellfire": 7,
    "torment": 6,
    "wrath": 6,
    "remember": 5,
    "remembrance": 5,
    "prayer": 5,
    "worship": 5,
    "trust": 5,
    "hope": 5,
    "fear": 3,
    "repent": 6,
    "repentance": 6,
    "resurrection": 8,
    "hereafter": 8,
    "afterlife": 7,
    "judgment": 6,
    "judgement": 6,
    "reckoning": 7,
    "deeds": 5,
    "deed": 4,
    "provision": 5,
    "sustenance": 5,
    "rizq": 6,
    "trial": 5,
    "trials": 5,
    "hardship": 5,
    "affliction": 5,
    "calamity": 6,
    "death": 6,
    "grave": 5,
    "power": 4,
    "mighty": 5,
    "majesty": 6,
    "revelation": 5,
    "quran": 6,
    "verses": 4,
    "mosque": 4,
    "architecture": 3,
    "desert": 3,
    "wilderness": 4,
    "sea": 4,
    "ocean": 4,
    "wave": 3,
    "waves": 3,
    "shore": 3,
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

    def visual_queries(
        self,
        verses: list[Verse],
        cycle: int,
    ) -> tuple[str, list[str], list[str]]:
        text = " ".join(
            v.translation
            for v in verses
        )
        theme, theme_score = self.score_text(text)

        variants = next(
            (
                variants
                for name, _words, variants in THEMES
                if name == theme
            ),
            ["peaceful cinematic nature landscape"],
        )
        if not variants:
            variants = [
                "peaceful cinematic nature landscape"
            ]

        # ==============================================================
        # LONG-TERM THEME INTELLIGENCE
        #
        # This count survives Quran cycles.
        # ==============================================================
        total_theme_usage = int(
            self.progress.theme_usage.get(theme, 0)
        )

        # Start with the least-used variant.
        #
        # We deliberately rotate based on persistent theme usage rather
        # than resetting every Quran cycle.
        variant_index = total_theme_usage % len(variants)
        chosen = variants[variant_index]

        # ==============================================================
        # Cycle style evolves over time too.
        # ==============================================================
        cycle_styles = [
            "cinematic natural landscape, subtle movement, realistic",
            "cinematic nature close-up, soft depth of field, realistic",
            "wide atmospheric landscape, realistic documentary style",
            "water and sky nature footage, elegant slow motion, realistic",
            "cinematic dawn or twilight nature, restrained movement, realistic",
            "cinematic aerial landscape, slow graceful movement, realistic",
            "cinematic environmental detail, shallow depth of field, realistic",
            "wide peaceful landscape, natural motion, realistic documentary footage",
        ]
        style = cycle_styles[
            (max(cycle, 1) - 1) % len(cycle_styles)
        ]

        # ==============================================================
        # Put the currently preferred variant first.
        #
        # Keep ALL other variants afterward so Pexels has a large search
        # pool instead of relying on one phrase.
        # ==============================================================
        ordered_variants = [
            chosen,
            *[
                variant
                for index, variant in enumerate(variants)
                if index != variant_index
            ],
        ]

        queries = [
            f"{variant}, {style}"
            for variant in ordered_variants
        ]

        # ==============================================================
        # Neutral fallbacks.
        # These are intentionally conservative.
        # ==============================================================
        fallback = [
            "peaceful cinematic nature landscape, realistic",
            "calm natural scenery soft light, realistic",
            "quiet countryside morning atmosphere, realistic",
            "wide peaceful landscape natural movement, realistic",
            "cinematic mountains and sky natural footage, realistic",
            "calm water landscape soft natural light, realistic",
        ]

        # ==============================================================
        # Keep searches clean and avoid unwanted visual material.
        #
        # "no animals, no birds, no wildlife" was added after a run
        # returned a duck floating on a pond for a verse with no water/
        # animal theme match — the query had fallen through to the
        # generic "reflection" fallback, which was not excluding
        # wildlife, and Pexels' "peaceful nature" results skew toward
        # animal footage more than plain landscape footage.
        # ==============================================================
        avoid = (
            "no people, no person, no faces, no human, no man, no woman, "
            "no religious figures, no crowd, no crowds, "
            "no animals, no birds, no wildlife, "
            "no text, no logos, no readable signs, empty landscape only"
        )
        queries = [
            f"{query}, {avoid}"
            for query in queries
        ]
        fallback = [
            f"{query}, {avoid}"
            for query in fallback
        ]

        log(
            f"Meaning theme={theme} "
            f"score={theme_score:.1f} "
            f"historical_theme_usage={total_theme_usage} "
            f"preferred_variant={variant_index + 1}/{len(variants)} "
            f"queries={len(queries)} "
            f"cycle={cycle}",
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

            score += min(len(text) / 260.0, 3.0)
            score += min(len(run) * 0.4, 3.0)

            if theme == previous_theme:
                score -= 1.5

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

            if selected and total + d > TARGET_MAX:
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

            # QUALITY GATE: reject anything below Full-HD-portrait equivalent.
            # This is the single biggest lever for "looks premium" vs "looks stocky".
            if height < 1920:
                continue

            score = 0.0
            if height >= 2560:
                score += 90
            elif height >= 2160:
                score += 75
            elif height >= 1920:
                score += 55

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

    def find_candidates(
        self,
        queries: list[str],
        fallback_queries: list[str],
        audio_duration: float,
        limit: int = 8,
    ) -> list[VisualCandidate]:
        """Search Pexels and return a RANKED LIST of viable candidates.

        This is the retry-safe counterpart to choose(): instead of handing
        back a single video, it hands back an ordered shortlist so the
        caller (QuranProduction.run) can fall through to the next candidate
        if the first one fails to download or fails to render (corrupt
        file, transient network error, codec issue, etc.). A single bad
        Pexels asset should never be able to kill a production run.

        Ordering rules (unchanged from the original single-pick logic):
          1. NEW (never-used) visuals matching the meaning-aware queries,
             best score first.
          2. NEW visuals matching the neutral fallback queries, if the
             meaning-aware search produced nothing fresh.
          3. Previously-used visuals, ordered LEAST-recently-used first
             (using visual_history), only if nothing fresh was found at
             all. Reuse is a deliberate, permanent safety net — the build
             must never fail just because every matching visual has been
             seen before.
        """

        fresh: list[VisualCandidate] = []
        reused: list[VisualCandidate] = []

        # Same Pexels video can appear in several searches.
        # Never evaluate the same video more than once.
        seen_ids: set[int] = set()

        def scan(
            query_list: list[str],
            *,
            max_pages: int = 6,
        ) -> None:

            for query in query_list:

                query = str(query).strip()

                if not query:
                    continue

                for page in range(1, max_pages + 1):

                    try:
                        payload = self.client.search(
                            query,
                            page,
                        )
                    except Exception as exc:
                        log(
                            f"Pexels search failed: "
                            f"{query} page={page}: {exc}",
                            "WARN",
                        )
                        continue

                    videos = payload.get("videos", [])

                    if not videos:
                        break

                    for raw in videos:

                        candidate = self.candidate_from_video(
                            raw,
                            query,
                            audio_duration,
                        )

                        if candidate is None:
                            continue

                        if candidate.video_id <= 0:
                            continue

                        # Deduplicate across queries/pages.
                        if candidate.video_id in seen_ids:
                            continue

                        seen_ids.add(candidate.video_id)

                        if candidate.video_id in self.progress.used_visuals:
                            reused.append(candidate)
                        else:
                            fresh.append(candidate)

        # ==============================================================
        # 1. SEARCH ALL MEANING-AWARE QUERIES
        # ==============================================================

        log(
            f"Searching {len(queries)} meaning-aware Pexels queries "
            f"for fresh backgrounds.",
            "DEBUG",
        )

        scan(
            queries,
            max_pages=6,
        )

        # ==============================================================
        # 2. NO FRESH THEME RESULT -> try neutral fallbacks too, so the
        #    shortlist has more than one usable option whenever possible.
        # ==============================================================

        if not fresh:
            log(
                "No fresh meaning-matched visual found; "
                "trying fallback searches.",
                "WARN",
            )
            scan(
                fallback_queries,
                max_pages=6,
            )

        if fresh:
            fresh.sort(
                key=lambda c: (
                    c.score,
                    c.duration,
                    c.height,
                    -c.video_id,
                ),
                reverse=True,
            )

            top = fresh[0]
            log(
                f"Top fresh Pexels candidate: {top.video_id} "
                f"score={top.score:.1f} query={top.query!r} "
                f"({len(fresh)} fresh candidate(s) available).",
                "INFO",
            )

            return fresh[:limit]

        # ==============================================================
        # 3. NO FRESH VIDEO ANYWHERE
        #
        # NEVER CANCEL THE QURAN VIDEO JUST BECAUSE A PEXELS VIDEO
        # WAS USED BEFORE. Reuse is a required, permanent safety net —
        # not gated by ALLOW_VISUAL_REUSE. Order by staleness so the
        # least-recently-used background comes first.
        # ==============================================================

        if reused:

            visual_history = self.progress.state.get(
                "visual_history",
                [],
            )

            if not isinstance(visual_history, list):
                visual_history = []

            last_used_index: dict[int, int] = {}

            for index, value in enumerate(visual_history):

                try:
                    visual_id = int(value)
                except (TypeError, ValueError):
                    continue

                if visual_id > 0:
                    last_used_index[visual_id] = index

            def staleness(
                candidate: VisualCandidate,
            ) -> int:
                """
                Smaller = older usage = better candidate to reuse.

                If a visual is not in the retained history, it is treated
                as extremely old rather than recently used.
                """
                return last_used_index.get(
                    candidate.video_id,
                    -1,
                )

            reused.sort(
                key=lambda candidate: (
                    staleness(candidate),
                    candidate.score,
                    candidate.duration,
                    candidate.height,
                )
            )

            top = reused[0]
            log(
                f"No fresh Pexels visual available. "
                f"Will REUSE oldest suitable visual(s), starting with "
                f"{top.video_id} (query={top.query!r}). "
                f"Production continues.",
                "WARN",
            )

            return reused[:limit]

        # ==============================================================
        # 4. ABSOLUTE LAST RESORT
        #
        # This means Pexels returned no usable video at all — not even a
        # previously-used one. It is NOT a 'video was already used'
        # failure; the caller must treat an empty list as a hard stop.
        # ==============================================================

        return []

    def choose(
        self,
        queries: list[str],
        fallback_queries: list[str],
        audio_duration: float,
    ) -> VisualCandidate:
        """Backward-compatible single-pick wrapper around find_candidates().

        Prefer find_candidates() directly when the caller can retry against
        several candidates (e.g. the main render loop) — this method exists
        for any code path that only ever wants one best answer.
        """
        candidates = self.find_candidates(queries, fallback_queries, audio_duration)
        if not candidates:
            raise RuntimeError(
                "No suitable Pexels video was found at all "
                "(fresh or reusable) across every meaning-aware "
                "and fallback query."
            )
        return candidates[0]

    def download(self, candidate: VisualCandidate, destination: Path) -> None:
        self.http.download(candidate.file_url, destination)


# ============================================================================
# 10. Typography and visual layout engine
# ============================================================================
#
# Presentation notes (design pass v5 — even coverage, zero overflow):
#   - The dark veil behind the text is now a SEPARATE, STATIC full-frame
#     image (built once per section) instead of being baked into every
#     per-ayah PNG. Each per-ayah PNG used to fade its own alpha in/out at
#     every ayah change, which meant the veil itself briefly faded too —
#     that is what produced the "patchy"/uneven darkening between ayahs.
#     Now the veil is composited ONCE, for the full clip duration, and only
#     the text on top of it fades between ayahs, so the background darkening
#     is perfectly even for the entire video, edge to edge, start to finish.
#   - The veil is a soft vertical gradient (slightly lighter near the very
#     top, stronger behind the text block in the middle/lower two-thirds),
#     matching the reference composition where the sky stays readable while
#     the Arabic/English block always sits on a clearly legible dark base.
#   - Font fitting now searches in 1pt steps (not 2pt) and reserves an
#     extra safety margin inside the text box, so a line that is a pixel or
#     two over budget shrinks one more notch instead of ever being drawn
#     outside the safe area. Combined with the existing hard post-render
#     overflow check (which still raises instead of silently shipping a
#     bad frame), Arabic can no longer render as "noodles" outside the box.


PALETTE = {
    "scrim": (8, 8, 11),
    "gold": (231, 199, 125),
    "gold_soft": (212, 178, 108),
    "ivory": (255, 241, 199),
    "white": (255, 255, 255),
}


class TypographyEngine:
    """
    Reference-style typography renderer.

    The important change from the previous renderer is that Quran text is
    rendered by Pillow/libraqm directly into PNG frames instead of asking
    ffmpeg/libass to shape Arabic. This gives us pixel-level control over
    wrapping and guarantees that Arabic cannot escape the safe text area.
    Each ayah gets its own full-frame overlay, while the Pexels background
    remains fully dynamic underneath it.
    """

    # Even, edge-to-edge veil. Built once as a static image (see
    # build_static_scrim) and composited for the FULL clip duration, so
    # coverage never flickers or looks patchy between ayahs.
    SCRIM_TOP_ALPHA = 60       # ~24% black near the very top of the frame
    SCRIM_MID_ALPHA = 100      # transition zone above the text block
    SCRIM_BOTTOM_ALPHA = 150   # ~59% black behind the busiest text area

    # A small safety margin (fraction of the nominal safe width) that is
    # subtracted before fitting text, so a font size that is only a hair
    # too wide gets rejected and the next-smaller size is used instead of
    # ever touching the true edge of the safe area.
    TEXT_SAFETY_MARGIN = 0.965

    SAFE_X = 72
    ARABIC_MAX_W = W - 144
    ENGLISH_MAX_W = W - 150
    HEADER_Y = 88
    SURAH_AR_Y = 245
    SURAH_EN_Y = 340
    BADGE_Y = 505
    ARABIC_TOP = 610
    ARABIC_MAX_H = 480
    ORNAMENT_GAP = 36
    ENGLISH_GAP = 58
    ENGLISH_MAX_H = 390
    FOOT_SAFE_Y = 1700

    def __init__(self):
        self.font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

        self.arabic_font_path = self.find_font([
            "/usr/share/fonts/**/Amiri-Regular.ttf",
            "/usr/share/fonts/**/Amiri*Regular*.ttf",
            "/usr/share/fonts/**/Scheherazade*Regular*.ttf",
            "/usr/share/fonts/**/ScheherazadeNew*Regular*.ttf",
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
            raise RuntimeError(
                "Required Arabic and Latin fonts were not found. "
                "Install Amiri/Scheherazade/Noto Naskh Arabic and Noto Sans."
            )

        self.arabic_font_name = self.font_family(
            self.arabic_font_path,
            "Noto Naskh Arabic",
        )
        self.english_font_name = self.font_family(
            self.english_font_path,
            "Noto Sans",
        )
        self.bold_font_name = self.font_family(
            self.bold_font_path,
            self.english_font_name,
        )

        self.fonts_dir = self.build_fonts_dir()

        # Pillow wheels on many Linux runners include libraqm. If not, the
        # optional pure-Python fallback is used when available.
        self.has_raqm = bool(getattr(features, "check", lambda *_: False)("raqm"))

        try:
            from arabic_reshaper import reshape as arabic_reshape
            from bidi.algorithm import get_display
            self._arabic_reshape = arabic_reshape
            self._bidi_display = get_display
        except Exception:
            self._arabic_reshape = None
            self._bidi_display = None

        if not self.has_raqm and not (
            self._arabic_reshape and self._bidi_display
        ):
            raise RuntimeError(
                "Arabic shaping is unavailable. Pillow/libraqm is missing and "
                "the arabic-reshaper/python-bidi fallback is not installed."
            )

    @staticmethod
    def find_font(patterns: list[str]) -> Optional[str]:
        for pattern in patterns:
            matches = sorted(glob.glob(pattern, recursive=True))
            if matches:
                return matches[0]
        return None

    @staticmethod
    def font_family(path: str, fallback: str) -> str:
        try:
            result = subprocess.run(
                ["fc-scan", "--format=%{family}\n", path],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                name = line.strip().split(",")[0].strip()
                if name:
                    return name
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass
        return fallback

    def build_fonts_dir(self) -> Path:
        merged = BUILD / "_fonts"
        merged.mkdir(parents=True, exist_ok=True)

        for path_str in {
            self.arabic_font_path,
            self.english_font_path,
            self.bold_font_path,
        }:
            if not path_str:
                continue

            source = Path(path_str)
            target = merged / source.name

            if target.exists() or target.is_symlink():
                try:
                    target.unlink()
                except OSError:
                    pass

            try:
                target.symlink_to(source)
            except OSError:
                shutil.copy2(source, target)

        return merged

    def font(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        key = (path, size)
        if key not in self.font_cache:
            self.font_cache[key] = ImageFont.truetype(path, size)
        return self.font_cache[key]

    def _shape_fallback(self, text: str) -> str:
        if self._arabic_reshape and self._bidi_display:
            return self._bidi_display(self._arabic_reshape(text))
        return text

    def _bbox(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        *,
        arabic: bool = False,
        stroke_width: int = 0,
    ) -> tuple[int, int, int, int]:
        if arabic and self.has_raqm:
            return draw.textbbox(
                (0, 0),
                text,
                font=font,
                direction="rtl",
                language="ar",
                stroke_width=stroke_width,
            )

        if arabic and not self.has_raqm:
            text = self._shape_fallback(text)

        return draw.textbbox(
            (0, 0),
            text,
            font=font,
            stroke_width=stroke_width,
        )

    def _draw_centered(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        y: float,
        font: ImageFont.FreeTypeFont,
        *,
        arabic: bool = False,
        fill: tuple[int, int, int, int] = (255, 255, 255, 245),
        stroke_width: int = 1,
        stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 170),
    ) -> None:
        draw_kwargs: dict[str, Any] = {
            "font": font,
            "fill": fill,
            "anchor": "mm",
            "stroke_width": stroke_width,
            "stroke_fill": stroke_fill,
        }

        if arabic and self.has_raqm:
            draw_kwargs["direction"] = "rtl"
            draw_kwargs["language"] = "ar"
            shaped = text
        elif arabic:
            shaped = self._shape_fallback(text)
        else:
            shaped = text

        draw.text((W / 2, y), shaped, **draw_kwargs)

    def _width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        *,
        arabic: bool = False,
        stroke_width: int = 0,
    ) -> float:
        box = self._bbox(
            draw,
            text,
            font,
            arabic=arabic,
            stroke_width=stroke_width,
        )
        return float(box[2] - box[0])

    def _wrap(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        maximum: float,
        *,
        arabic: bool = False,
        stroke_width: int = 0,
    ) -> list[str]:
        words = normalize_space(text.replace("\n", " ")).split()
        if not words:
            return [""]

        result: list[str] = []
        line = ""

        for word in words:
            candidate = word if not line else f"{line} {word}"
            if (
                not line
                or self._width(
                    draw,
                    candidate,
                    font,
                    arabic=arabic,
                    stroke_width=stroke_width,
                ) <= maximum
            ):
                line = candidate
            else:
                result.append(line)
                line = word

        if line:
            result.append(line)

        return result or [""]

    def _fit_arabic(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
    ) -> tuple[int, list[str], float]:
        safe_width = self.ARABIC_MAX_W * self.TEXT_SAFETY_MARGIN
        safe_height = self.ARABIC_MAX_H * self.TEXT_SAFETY_MARGIN

        for size in range(84, 29, -1):
            font = self.font(self.arabic_font_path, size)
            lines = self._wrap(
                draw,
                text,
                font,
                safe_width,
                arabic=True,
                stroke_width=1,
            )
            line_height = size * 1.60
            block_height = line_height * len(lines)

            widest = max(
                self._width(
                    draw,
                    line,
                    font,
                    arabic=True,
                    stroke_width=1,
                )
                for line in lines
            )

            if widest <= safe_width and block_height <= safe_height:
                return size, lines, line_height

        raise RuntimeError(
            "Arabic verse could not be fitted into the safe 9:16 text area "
            "even at the smallest permitted size — the ayah text may be "
            "unusually long for a single section."
        )

    def _fit_english(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        maximum_height: int,
        start_size: int = 46,
    ) -> tuple[int, list[str], float]:
        safe_width = self.ENGLISH_MAX_W * self.TEXT_SAFETY_MARGIN
        safe_height = maximum_height * self.TEXT_SAFETY_MARGIN

        for size in range(start_size, 19, -1):
            font = self.font(self.english_font_path, size)
            lines = self._wrap(
                draw,
                text,
                font,
                safe_width,
                arabic=False,
                stroke_width=1,
            )
            line_height = size * 1.28
            block_height = line_height * len(lines)

            widest = max(
                self._width(
                    draw,
                    line,
                    font,
                    arabic=False,
                    stroke_width=1,
                )
                for line in lines
            )

            if widest <= safe_width and block_height <= safe_height:
                return size, lines, line_height

        raise RuntimeError(
            "English translation could not be fitted into the safe 9:16 "
            "text area even at the smallest permitted size."
        )

    def _draw_multiline_centered(
        self,
        draw: ImageDraw.ImageDraw,
        lines: list[str],
        top: float,
        line_height: float,
        font: ImageFont.FreeTypeFont,
        *,
        arabic: bool = False,
        fill: tuple[int, int, int, int] = (255, 255, 255, 245),
        stroke_width: int = 1,
        stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 170),
    ) -> None:
        for index, line in enumerate(lines):
            self._draw_centered(
                draw,
                line,
                top + line_height * (index + 0.5),
                font,
                arabic=arabic,
                fill=fill,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
            )

    def _draw_badge(
        self,
        draw: ImageDraw.ImageDraw,
        verse_number: int,
    ) -> None:
        cx = W / 2
        cy = self.BADGE_Y

        # Thin reference-style circular ayah marker. No opaque box.
        draw.ellipse(
            (cx - 34, cy - 34, cx + 34, cy + 34),
            outline=(255, 255, 255, 175),
            width=1,
        )
        draw.ellipse(
            (cx - 40, cy - 40, cx + 40, cy + 40),
            outline=(231, 199, 125, 120),
            width=1,
        )

        badge_font = self.font(self.arabic_font_path, 30)
        self._draw_centered(
            draw,
            arabic_digits(verse_number),
            cy + 1,
            badge_font,
            arabic=True,
            fill=(255, 255, 255, 245),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 150),
        )

    @staticmethod
    def _draw_ornament(
        draw: ImageDraw.ImageDraw,
        y: float,
    ) -> None:
        line_color = (231, 199, 125, 175)
        diamond_color = (255, 247, 225, 210)

        left_end = 355
        left_start = 495
        right_start = 585
        right_end = 725

        draw.line((left_end, y, left_start, y), fill=line_color, width=1)
        draw.line((right_start, y, right_end, y), fill=line_color, width=1)

        draw.polygon(
            [
                (540, y - 9),
                (549, y),
                (540, y + 9),
                (531, y),
            ],
            outline=diamond_color,
        )

    def build_static_scrim(self, section_dir: Path) -> Path:
        """
        Build ONE static, full-frame darkening veil for the whole section.

        This is composited a single time in VideoRenderer.render() and held
        for the entire clip duration, so every frame of the video — from the
        first ayah to the last — is darkened by exactly the same amount in
        exactly the same places. Nothing about this image changes per ayah,
        which is what makes the coverage perfectly even instead of the old
        "patchy" look caused by re-fading a scrim baked into every text PNG.

        The gradient is soft: slightly lighter near the very top (so the sky/
        background stays visible, matching the reference image) and stronger
        through the middle and lower two-thirds where the Surah header,
        Arabic verse, and English translation all sit.
        """
        scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pixels = scrim.load()

        top = self.SCRIM_TOP_ALPHA
        mid = self.SCRIM_MID_ALPHA
        bottom = self.SCRIM_BOTTOM_ALPHA

        # Precompute one alpha value per row, then paint each row in one
        # shot instead of touching every pixel individually (fast + simple).
        row_alphas: list[int] = []
        for y in range(H):
            t = y / max(1, H - 1)
            if t <= 0.30:
                # top band: top -> mid
                local_t = t / 0.30
                alpha = top + (mid - top) * local_t
            else:
                # remaining band: mid -> bottom
                local_t = (t - 0.30) / 0.70
                alpha = mid + (bottom - mid) * local_t
            row_alphas.append(int(round(alpha)))

        row = Image.new("RGBA", (W, 1))
        for y, alpha in enumerate(row_alphas):
            row.paste((8, 10, 14, alpha), (0, 0, W, 1))
            scrim.paste(row, (0, y))

        scrim_path = section_dir / "scrim.png"
        scrim.save(scrim_path, format="PNG", optimize=True)
        return scrim_path

    def make_timed_overlay_frames(
        self,
        section_dir: Path,
        verses: list[Verse],
        audio_items: list[AudioItem],
        chapter_name: str,
        chapter_name_arabic: str,
    ) -> list[tuple[Path, float, float]]:
        if len(verses) != len(audio_items):
            raise RuntimeError(
                "Typography timeline mismatch: verses/audio_items lengths differ."
            )

        frames_dir = section_dir / "overlay-frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        probe = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        probe_draw = ImageDraw.Draw(probe)

        header_font = self.font(self.english_font_path, 34)
        surah_ar_font = self.font(self.arabic_font_path, 78)
        surah_en_font = self.font(self.english_font_path, 42)

        if chapter_name_arabic:
            chapter_ar = chapter_name_arabic.strip()
            if not chapter_ar.startswith("سورة"):
                chapter_ar = f"سُورَةُ {chapter_ar}"
        else:
            chapter_ar = ""

        # Verify the fixed header pieces once before generating all frames.
        if self._width(
            probe_draw,
            f"SURAH {romanish(chapter_name)} — AYAH 999",
            header_font,
            stroke_width=1,
        ) > W - 120:
            raise RuntimeError("Header cannot fit inside the safe 9:16 area.")

        if chapter_ar and self._width(
            probe_draw,
            chapter_ar,
            surah_ar_font,
            arabic=True,
            stroke_width=1,
        ) > W - 170:
            # Smaller Surah title is preferable to ever clipping Arabic.
            surah_ar_font = self.font(self.arabic_font_path, 66)

        output: list[tuple[Path, float, float]] = []
        cursor = 0.0
        total = len(verses)

        for index, (verse, audio) in enumerate(zip(verses, audio_items), start=1):
            start = cursor
            end = cursor + float(audio.duration)
            cursor = end

            # NOTE: the frame starts fully transparent. The dark veil is NOT
            # drawn here anymore — it is a separate static layer (see
            # build_static_scrim) composited once for the whole clip, so
            # this per-ayah PNG only ever contains text/ornament pixels.
            image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)

            header = f"SURAH {romanish(chapter_name)} — AYAH {verse.verse_number}"
            self._draw_centered(
                draw,
                header,
                self.HEADER_Y,
                header_font,
                fill=(255, 255, 255, 242),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 150),
            )

            if chapter_ar:
                self._draw_centered(
                    draw,
                    chapter_ar,
                    self.SURAH_AR_Y,
                    surah_ar_font,
                    arabic=True,
                    fill=(255, 255, 255, 242),
                    stroke_width=1,
                    stroke_fill=(0, 0, 0, 145),
                )

            self._draw_centered(
                draw,
                f"Surah {chapter_name}",
                self.SURAH_EN_Y,
                surah_en_font,
                fill=(235, 235, 235, 232),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 135),
            )

            self._draw_badge(draw, verse.verse_number)

            arabic_size, arabic_lines, arabic_line_height = self._fit_arabic(
                draw,
                verse.arabic_text,
            )
            arabic_font = self.font(self.arabic_font_path, arabic_size)

            self._draw_multiline_centered(
                draw,
                arabic_lines,
                self.ARABIC_TOP,
                arabic_line_height,
                arabic_font,
                arabic=True,
                fill=(255, 255, 255, 248),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 150),
            )

            arabic_bottom = (
                self.ARABIC_TOP
                + arabic_line_height * len(arabic_lines)
            )

            ornament_y = int(arabic_bottom + self.ORNAMENT_GAP)
            self._draw_ornament(draw, ornament_y)

            english_top = ornament_y + self.ENGLISH_GAP
            remaining_height = max(
                260,
                self.FOOT_SAFE_Y - english_top,
            )

            english_size, english_lines, english_line_height = self._fit_english(
                draw,
                normalize_space(verse.translation.replace("\n", " ")),
                remaining_height,
            )
            english_font = self.font(self.english_font_path, english_size)

            self._draw_multiline_centered(
                draw,
                english_lines,
                english_top,
                english_line_height,
                english_font,
                fill=(255, 255, 255, 245),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 155),
            )

            # Hard pixel-level safety checks. These prevent the exact "Arabic
            # noodles outside the box" failure the previous renderer allowed.
            for line in arabic_lines:
                width = self._width(
                    draw,
                    line,
                    arabic_font,
                    arabic=True,
                    stroke_width=2,
                )
                if width > self.ARABIC_MAX_W + 1:
                    raise RuntimeError(
                        f"Arabic overflow detected in ayah {verse.verse_number}: "
                        f"{width:.1f}px > {self.ARABIC_MAX_W}px"
                    )

            for line in english_lines:
                width = self._width(
                    draw,
                    line,
                    english_font,
                    stroke_width=1,
                )
                if width > self.ENGLISH_MAX_W + 1:
                    raise RuntimeError(
                        f"English overflow detected in ayah {verse.verse_number}: "
                        f"{width:.1f}px > {self.ENGLISH_MAX_W}px"
                    )

            if english_top + english_line_height * len(english_lines) > H - 150:
                raise RuntimeError(
                    f"English block is too low in ayah {verse.verse_number}."
                )

            frame_path = frames_dir / f"ayah-{index:03d}-{verse.verse_number:03d}.png"
            image.save(frame_path, format="PNG", optimize=True)

            output.append((frame_path, start, end))

        return output


# ============================================================================
# 10.5. Sidecar captions (.srt)
# ============================================================================
#
# The timed PNG typography frames above are burned directly into the MP4, so
# the video is always self-captioned. This engine additionally produces a
# plain .srt sidecar file, timed against the EXACT SAME per-ayah audio
# timeline used to burn the on-screen captions, so it can never drift out of sync
# with what
# is shown in the video. This is for platforms (Instagram/YouTube Shorts/
# TikTok-style uploaders, including automations like make.com) that accept
# or prefer a separate caption/subtitle file alongside the video instead of
# (or in addition to) burned-in text — e.g. for native closed captions,
# accessibility, or auto-generated on-platform caption styling.


class CaptionEngine:
    @staticmethod
    def srt_time(seconds: float) -> str:
        millis = max(0, int(round(seconds * 1000)))
        hours = millis // 3_600_000
        millis %= 3_600_000
        minutes = millis // 60_000
        millis %= 60_000
        secs = millis // 1000
        millis %= 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @classmethod
    def make_srt(
        cls,
        path: Path,
        verses: list[Verse],
        audio_items: list[AudioItem],
    ) -> None:
        """Write an .srt whose cue timings match the burned-in captions
        exactly, one cue per ayah, using the same audio_items durations
        that drive the ASS timeline in TypographyEngine.make_ass().
        """
        if len(verses) != len(audio_items):
            raise RuntimeError(
                "CaptionEngine.make_srt: verses/audio_items length mismatch "
                f"({len(verses)} vs {len(audio_items)}) — refusing to write "
                "out-of-sync captions."
            )

        lines: list[str] = []
        cursor = 0.0

        for idx, (verse, audio) in enumerate(zip(verses, audio_items), start=1):
            start = cursor
            end = cursor + audio.duration
            cursor = end

            text = normalize_space(verse.translation.replace("\n", " "))
            if not text:
                text = normalize_space(verse.arabic_text) or f"Ayah {verse.verse_number}"

            lines.append(str(idx))
            lines.append(f"{cls.srt_time(start)} --> {cls.srt_time(end)}")
            lines.append(text)
            lines.append("")

        path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


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
        # The source stays dynamic. We loop it only when it is shorter than the
        # recitation and use a very slow, deterministic crop drift so the final
        # 9:16 video never looks like a frozen image.
        profiles = [
            "x='(iw-1080)/2 + (iw-1080)/2*0.28*sin(2*PI*t/28)':y='(ih-1920)/2 + (ih-1920)/2*0.18*cos(2*PI*t/35)'",
            "x='(iw-1080)/2 + (iw-1080)/2*0.20*cos(2*PI*t/31)':y='(ih-1920)/2 + (ih-1920)/2*0.20*sin(2*PI*t/37)'",
            "x='(iw-1080)/2 + (iw-1080)/2*0.16*sin(2*PI*t/40)':y='(ih-1920)/2 + (ih-1920)/2*0.25*sin(2*PI*t/43)'",
            "x='(iw-1080)/2 + (iw-1080)/2*0.22*cos(2*PI*t/44)':y='(ih-1920)/2 + (ih-1920)/2*0.14*cos(2*PI*t/49)'",
        ]
        profile = profiles[(cycle + visual_id) % len(profiles)]

        run_cmd([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(source),
            "-t", f"{duration:.3f}",
            "-vf",
            (
                "scale=1950:3467:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop=1620:2880:{profile},"
                "scale=1080:1920:flags=lanczos,"
                f"fps={FPS},format=yuv420p,"
                "unsharp=5:5:0.45:5:5:0.0,"
                "eq=contrast=1.04:saturation=1.06:brightness=0.005"
            ),
            "-an",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "14",
            "-movflags", "+faststart",
            str(destination),
        ])
        return profile

    def render(
        self,
        background: Path,
        scrim: Path,
        overlay_frames: list[tuple[Path, float, float]],
        audio: Path,
        output: Path,
        duration: float,
    ) -> None:
        if not overlay_frames:
            raise RuntimeError("No timed typography frames were generated.")

        fade_out_start = max(0.0, duration - 0.8)

        # Inputs:
        #   0 = dynamic background
        #   1 = static full-duration scrim (even coverage, no per-ayah fade)
        #   2..N+1 = one transparent full-frame typography PNG per ayah
        #   N+2 = recitation audio
        command = ["ffmpeg", "-y", "-i", str(background)]

        command.extend([
            "-loop", "1",
            "-t", f"{duration:.3f}",
            "-i", str(scrim),
        ])

        for frame_path, start, end in overlay_frames:
            frame_duration = max(0.05, end - start)
            command.extend([
                "-loop", "1",
                "-t", f"{frame_duration:.3f}",
                "-i", str(frame_path),
            ])

        command.extend(["-i", str(audio)])

        filters: list[str] = [
            "[0:v]setpts=PTS-STARTPTS[base]",
            # Static veil, held for the entire clip — this is what makes the
            # darkening perfectly even instead of flickering per ayah.
            "[1:v]format=rgba,setpts=PTS-STARTPTS[scrim]",
            "[base][scrim]overlay=0:0:eof_action=pass[based]",
        ]
        current = "based"

        for index, (_frame_path, start, end) in enumerate(overlay_frames, start=2):
            duration_local = max(0.05, end - start)
            fade = min(0.24, max(0.08, duration_local / 4.0))
            fade_out = max(0.0, duration_local - fade)

            overlay_label = f"ov{index}"
            next_label = f"mix{index}"

            filters.append(
                f"[{index}:v]"
                f"format=rgba,"
                f"fade=t=in:st=0:d={fade:.3f}:alpha=1,"
                f"fade=t=out:st={fade_out:.3f}:d={fade:.3f}:alpha=1,"
                f"setpts=PTS+{start:.3f}/TB"
                f"[{overlay_label}]"
            )

            filters.append(
                f"[{current}][{overlay_label}]"
                f"overlay=0:0:eof_action=pass:shortest=0"
                f"[{next_label}]"
            )
            current = next_label

        filters.append(
            f"[{current}]"
            f"fade=t=in:st=0:d=0.6,"
            f"fade=t=out:st={fade_out_start:.3f}:d=0.8"
            f"[vout]"
        )

        audio_input_index = 2 + len(overlay_frames)
        filters.append(
            f"[{audio_input_index}:a]"
            f"afade=t=in:st=0:d=0.35,"
            f"afade=t=out:st={fade_out_start:.3f}:d=0.8"
            f"[aout]"
        )

        filter_complex = ";".join(filters)

        command.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "14",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output),
        ])

        run_cmd(command)


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
        captions_source: Optional[Path] = None,
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

        captions_path: Optional[Path] = None
        if captions_source is not None and captions_source.exists():
            captions_path = PUBLISH / f"{video_path.stem}.srt"
            shutil.copy2(captions_source, captions_path)
            metadata["captions"] = {
                "file": captions_path.name,
                "format": "srt",
                "synced_to": "audio_timeline",
            }

        atomic_write_json(metadata_path, metadata)
        return video_path, metadata_path

    def enqueue(
        self,
        metadata: dict[str, Any],
        video_path: Path,
        metadata_path: Path,
    ) -> None:
        queue = load_json(
            self.queue_path,
            [],
        )
        if not isinstance(queue, list):
            log(
                "publish_queue.json was invalid; "
                "rebuilding queue safely.",
                "WARN",
            )
            queue = []

        key = metadata.get(
            "published_sha256"
        )

        cleaned: list[dict[str, Any]] = []
        for item in queue:
            if not isinstance(item, dict):
                continue
            if item.get("published_sha256") == key:
                continue
            cleaned.append(item)
        queue = cleaned

        queue.append({
            "status": "pending",
            "video_file": video_path.name,
            "metadata_file": metadata_path.name,
            "published_sha256": key,
            "cycle": metadata.get("cycle"),
            "surah": metadata.get("surah", {}).get("name"),
            "chapter": metadata.get("surah", {}).get("number"),
            "verse_keys": metadata.get("verse_keys", []),
            "duration_seconds": metadata.get(
                "section", {}
            ).get("duration_seconds"),
            "captions_file": metadata.get("captions", {}).get("file"),
            "created_at": utc_now(),
        })

        MAX_QUEUE_ITEMS = 500

        pending = [
            item
            for item in queue
            if item.get("status") == "pending"
        ]
        non_pending = [
            item
            for item in queue
            if item.get("status") != "pending"
        ]

        pending = pending[-MAX_QUEUE_ITEMS:]
        non_pending = non_pending[-100:]

        queue = non_pending + pending

        atomic_write_json(
            self.queue_path,
            queue,
        )


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

    def _select_passage_with_fallback(
        self, start_index: int, duration_map: dict[str, float]
    ) -> list[Verse]:
        try:
            return self.planner.select_passage(start_index, duration_map)
        except RuntimeError as exc:
            match = re.search(r"Missing audio duration for (\d+:\d+)", str(exc))
            if not match:
                raise
            missing_key = match.group(1)
            log(f"Duration cache missed {missing_key}; fetching it directly.", "WARN")
            duration_map[missing_key] = self.audio.fetch(missing_key).duration
            return self.planner.select_passage(start_index, duration_map)

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

        duration_map: dict[str, float] = {}
        i = start_index
        while i < len(self.verses):
            verse = self.verses[i]
            if verse.verse_key in self.progress.used:
                break
            if verse.chapter_id != start_verse.chapter_id:
                break

            running_total = sum(duration_map.values())
            if (
                duration_map
                and start_verse.ruku_number is not None
                and verse.ruku_number != start_verse.ruku_number
                and running_total >= TARGET_MIN
            ):
                break

            item = self.audio.fetch(verse.verse_key)
            d = item.duration

            if duration_map and running_total + d > TARGET_MAX:
                break

            duration_map[verse.verse_key] = d
            i += 1

            running_total += d
            if running_total >= TARGET_MIN:
                if i >= len(self.verses):
                    break
                nxt = self.verses[i]
                if nxt.chapter_id != start_verse.chapter_id:
                    break
                if start_verse.ruku_number is not None and nxt.ruku_number != start_verse.ruku_number:
                    break

        selected = self._select_passage_with_fallback(start_index, duration_map)
        keys = [v.verse_key for v in selected]

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

        audio_items, section_audio, audio_duration = self.audio.prepare(
            selected,
            section_dir,
        )

        # Build the reference-style typography as transparent PNG frames.
        # Arabic is shaped by Pillow/libraqm, not ffmpeg/libass, so there are
        # no missing-glyph boxes and no Arabic text can escape the safe area.
        overlay_frames = self.typography.make_timed_overlay_frames(
            section_dir,
            selected,
            audio_items,
            start_verse.chapter_name,
            start_verse.chapter_name_arabic,
        )

        # One static, full-duration darkening veil for the whole section —
        # composited once in the renderer instead of baked into every ayah
        # frame, so the coverage is perfectly even from first frame to last.
        scrim_path = self.typography.build_static_scrim(section_dir)

        captions = section_dir / "captions.srt"
        CaptionEngine.make_srt(captions, selected, audio_items)

        theme, queries, fallback_queries = self.meaning.visual_queries(selected, cycle)
        candidates = self.visuals.find_candidates(queries, fallback_queries, audio_duration)
        if not candidates:
            raise RuntimeError(
                "No suitable Pexels video was found at all "
                "(fresh or reusable) across every meaning-aware "
                "and fallback query."
            )

        final_video = section_dir / "quran-section.mp4"
        MAX_VISUAL_ATTEMPTS = min(5, len(candidates))

        visual: Optional[VisualCandidate] = None
        camera_profile: Optional[str] = None
        qa: Optional[dict[str, Any]] = None
        last_visual_error: Optional[Exception] = None

        for attempt_index, candidate in enumerate(candidates[:MAX_VISUAL_ATTEMPTS], start=1):
            try:
                source_video = section_dir / f"pexels-source-{attempt_index}.mp4"
                self.visuals.download(candidate, source_video)

                background = section_dir / f"background-{attempt_index}.mp4"
                camera_profile = self.renderer.prepare_background(
                    source_video,
                    background,
                    audio_duration,
                    cycle,
                    candidate.video_id,
                )

                self.renderer.render(
                    background,
                    scrim_path,
                    overlay_frames,
                    section_audio,
                    final_video,
                    audio_duration,
                )

                qa = self.qa.validate_video(final_video, audio_duration)
                visual = candidate
                break
            except Exception as exc:
                last_visual_error = exc
                final_video.unlink(missing_ok=True)
                log(
                    f"Visual candidate {candidate.video_id} "
                    f"(attempt {attempt_index}/{MAX_VISUAL_ATTEMPTS}) failed "
                    f"during download/render: {exc}. Trying next candidate.",
                    "WARN",
                )
                continue

        if visual is None or qa is None:
            raise RuntimeError(
                f"All {MAX_VISUAL_ATTEMPTS} visual candidate(s) failed to "
                f"download or render. Last error: {last_visual_error}"
            )

        log(
            f"Media QA PASSED: {qa['width']}x{qa['height']} "
            f"{qa['duration']:.2f}s H.264/AAC "
            f"(visual {visual.video_id})"
        )

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
                    "reference-style full-frame static veil / Surah header / "
                    "Arabic Surah title / ayah badge / safely wrapped "
                    "Arabic verse / ornament / English translation"
                ),
                "fonts": {
                    "arabic": Path(self.typography.arabic_font_path).stem,
                    "english": "Noto Sans",
                },
            },
            "qa": {
                "status": "passed",
                "final_sha256": sha256_file(final_video),
                "validated_at": utc_now(),
            },
        }

        publish_video, publish_metadata = self.publisher.package(
            final_video,
            metadata,
            start_verse.chapter_name,
            start_verse.chapter_id,
            selected[0].verse_number,
            selected[-1].verse_number,
            cycle,
            captions_source=captions,
        )
        self.publisher.enqueue(metadata, publish_video, publish_metadata)

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
                "visual_query": visual.query,
                "published_file": publish_video.name,
                "published_sha256": metadata["published_sha256"],
            },
        )

        self.git.commit_if_enabled()

        if not KEEP_BUILD:
            shutil.rmtree(section_dir, ignore_errors=True)
            log("Build artifacts cleaned up (set QURAN_KEEP_BUILD=1 to keep them).", "DEBUG")
        else:
            log(f"QURAN_KEEP_BUILD=1; keeping build artifacts at {section_dir}", "INFO")

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
            "captions_file": metadata.get("captions", {}).get("file"),
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
        log(f"Visual: Pexels {visual.video_id} (fresh={visual_is_fresh})")
        log(f"Duration: {audio_duration:.2f}s")
        log(f"Output: {publish_video}")
        log(f"Captions: {metadata.get('captions', {}).get('file', '(none)')}")
        log(f"Elapsed: {elapsed:.2f}s")
        log("================================================")
        return result


def main() -> int:
    result_path = ROOT / "run-result.json"
    with ProductionLock(LOCK):
        try:
            production = QuranProduction()
            result = production.run()
            atomic_write_json(result_path, result)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return 0
        except Exception as exc:
            log(f"PRODUCTION FAILED: {exc}", "ERROR")
            traceback.print_exc()
            atomic_write_json(result_path, {
                "success": False,
                "error": str(exc),
                "failed_at": utc_now(),
            })
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
