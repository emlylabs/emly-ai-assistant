"""Background worker that drives website crawl jobs.

A single async supervisor task is started at FastAPI startup. It polls
the ``crawl_jobs`` table every few seconds for rows in ``running``
status and ensures a per-job worker coroutine is processing each one.
On worker boot, any pages stuck in fetching/uploading/embedding from a
prior process get reset to ``queued`` so they get retried.

The worker reuses the same fetch/SSRF helpers (``services/crawl_fetch``)
and the same embed pipeline (``_embed_file_sync``) as the upload route
so there's one source of truth for ingestion.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

from agents.rag_manager import get_rag_manager
from config import DATA_DIR
from models.crawl_jobs import (
    Crawl_Jobs,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_RUNNING,
    PAGE_DONE,
    PAGE_EMBEDDING,
    PAGE_FAILED,
    PAGE_SKIPPED,
    PAGE_UPLOADING,
)
from models.emly_files import (
    EMBEDDING_STATUS_EMBEDDED,
    EMLYFiles,
    Emly_Files,
)
from services.crawl_fetch import UnsafeUrlError, fetch_html, fetch_robots, validate_url

log = logging.getLogger(__name__)

SUPERVISOR_INTERVAL_S = 3.0
DEFAULT_CONCURRENCY = 3
DEFAULT_THIN_THRESHOLD = 200
DEFAULT_POLITE_DELAY_MS = 250

# In-process registry of jobs being driven by this worker. Multi-process
# coordination is out of scope; CLAUDE.md notes this is a single-replica
# deployment until S3 + Redis ship.
_active_jobs: dict[str, asyncio.Task] = {}
_supervisor_task: Optional[asyncio.Task] = None


def _bot_uploads_dir(bot_id: str) -> Path:
    return Path(DATA_DIR) / "bots" / bot_id / "uploads"


def _canonicalize(raw: str) -> Optional[str]:
    """Lowercase host, strip fragment, trim trailing slash."""
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    netloc = parsed.hostname.lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    rebuilt = f"{parsed.scheme}://{netloc}{path}"
    if parsed.query:
        rebuilt = f"{rebuilt}?{parsed.query}"
    return rebuilt


def _url_to_filename(canonical_url: str) -> str:
    parsed = urlparse(canonical_url)
    host_part = re.sub(r"[^a-zA-Z0-9.-]", "_", parsed.hostname or "host")
    path_raw = "_root" if parsed.path in ("", "/") else parsed.path.replace("/", "__")
    path_part = re.sub(r"[^a-zA-Z0-9_.-]", "_", path_raw)[:80]
    sha = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:8]
    return f"crawl__{host_part}__{path_part}__{sha}.html"


def _parse_robots(text: str) -> list[str]:
    rules: list[str] = []
    in_star = False
    for line_raw in text.splitlines():
        line = line_raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            in_star = value == "*"
        elif key == "disallow" and in_star and value:
            rules.append(value)
    return rules


def _robots_allows(path: str, rules: list[str]) -> bool:
    return all(not path.startswith(rule) for rule in rules)


def _effective_base(soup, base_url: str) -> str:
    """Return the base URL to use when resolving relative refs in ``soup``.

    Honors a ``<base href="...">`` tag if present, otherwise falls back to
    the document URL.
    """
    base_tag = soup.find("base", href=True)
    if not base_tag:
        return base_url
    base_href = (base_tag.get("href") or "").strip()
    if not base_href:
        return base_url
    try:
        return urljoin(base_url, base_href)
    except Exception:
        return base_url


def _resolve_url(value: str, base: str) -> str:
    """Resolve a single href/src against ``base``. Skip non-resolvable schemes."""
    candidate = (value or "").strip()
    if not candidate:
        return value
    lowered = candidate.lower()
    if lowered.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
        return value
    try:
        return urljoin(base, candidate)
    except Exception:
        return value


def _resolve_srcset(value: str, base: str) -> str:
    """Resolve every URL inside an HTML ``srcset`` attribute.

    ``srcset`` is a comma-separated list of ``url [descriptor]`` entries
    (e.g. ``img.png 1x, img@2x.png 2x`` or ``img.png 480w``). Only the URL
    portion is rewritten; descriptors pass through untouched.
    """
    if not value:
        return value
    parts: list[str] = []
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        bits = entry.split(None, 1)
        url = _resolve_url(bits[0], base)
        if len(bits) == 2:
            parts.append(f"{url} {bits[1]}")
        else:
            parts.append(url)
    return ", ".join(parts)


def _extract_links(html: str, base_url: str) -> list[str]:
    # bs4 import is local so the worker module imports cheaply if it isn't
    # used. bs4 is already a transitive dep through langchain-community.
    from bs4 import BeautifulSoup

    out: list[str] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return out

    effective_base = _effective_base(soup, base_url)

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        lowered = href.lower()
        if lowered.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        if href.startswith("#"):
            continue
        try:
            out.append(urljoin(effective_base, href))
        except Exception:
            continue
    return out


def _resolve_relative_urls(html: str, base_url: str) -> str:
    """Rewrite relative image (and related media) URLs in ``html`` to absolute.

    Images are rewritten so the persisted HTML renders correctly when served
    or viewed standalone — without this, ``<img src="/foo.png">`` would be
    a dead reference once the page is detached from its origin.
    """
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return html

    base = _effective_base(soup, base_url)

    for img in soup.find_all("img"):
        if img.has_attr("src"):
            img["src"] = _resolve_url(img["src"], base)
        if img.has_attr("srcset"):
            img["srcset"] = _resolve_srcset(img["srcset"], base)

    # <picture>/<video>/<audio> children carry the same kinds of refs.
    for source in soup.find_all("source"):
        if source.has_attr("src"):
            source["src"] = _resolve_url(source["src"], base)
        if source.has_attr("srcset"):
            source["srcset"] = _resolve_srcset(source["srcset"], base)

    return str(soup)


def _extract_text_length(html: str) -> int:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return 0
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return len(text)


def _passes_scope(canonical_url: str, opts: dict, seed_host: str) -> bool:
    parsed = urlparse(canonical_url)
    if opts.get("sameHostOnly", True) and parsed.hostname != seed_host:
        return False
    prefix = (opts.get("pathPrefix") or "").strip()
    if prefix and not parsed.path.startswith(prefix):
        return False
    include_re = opts.get("includeRegex") or ""
    if include_re:
        try:
            if not re.search(include_re, canonical_url):
                return False
        except re.error:
            pass  # bad regex — ignore filter
    exclude_re = opts.get("excludeRegex") or ""
    if exclude_re:
        try:
            if re.search(exclude_re, canonical_url):
                return False
        except re.error:
            pass
    return True


async def _process_page(
    job: dict,
    page: dict,
    seed_host: str,
    robots_cache: Dict[str, list[str]],
    last_fetch_by_host: Dict[str, float],
    options: dict,
) -> None:
    job_id = job["id"]
    bot_id = job["bot_id"]
    page_id = page["id"]
    url = page["url"]
    depth = page["depth"]

    # SSRF re-check (host could have been re-pointed since canonicalization).
    try:
        validate_url(url)
    except UnsafeUrlError as exc:
        Crawl_Jobs.update_page_state(page_id, PAGE_SKIPPED, reason=f"unsafe url: {exc}")
        Crawl_Jobs.bump_counters(job_id, delta_skipped=1)
        return

    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Robots.txt
    if options.get("respectRobots", True):
        rules = robots_cache.get(host)
        if rules is None:
            try:
                text = await asyncio.to_thread(fetch_robots, host)
            except Exception:
                text = None
            rules = _parse_robots(text) if text else []
            robots_cache[host] = rules
        if not _robots_allows(parsed.path or "/", rules):
            Crawl_Jobs.update_page_state(page_id, PAGE_SKIPPED, reason="robots disallow")
            Crawl_Jobs.bump_counters(job_id, delta_skipped=1)
            return

    # Polite delay per host
    delay_ms = int(options.get("politeDelayMs", DEFAULT_POLITE_DELAY_MS) or 0)
    if delay_ms > 0:
        last = last_fetch_by_host.get(host, 0.0)
        wait = (last + delay_ms / 1000.0) - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
    last_fetch_by_host[host] = time.time()

    # Fetch
    result = await asyncio.to_thread(fetch_html, url)
    if result.get("skipped_reason") or not result.get("html"):
        Crawl_Jobs.update_page_state(
            page_id,
            PAGE_SKIPPED,
            reason=result.get("skipped_reason") or "no body",
        )
        Crawl_Jobs.bump_counters(job_id, delta_skipped=1)
        return

    html: str = result["html"]
    final_url = result.get("final_url") or url

    # Discover links
    if depth < int(options.get("maxDepth", 3)):
        for raw_link in _extract_links(html, final_url):
            canon = _canonicalize(raw_link)
            if not canon:
                continue
            if not _passes_scope(canon, options, seed_host):
                continue
            if Crawl_Jobs.add_page_if_new(job_id, canon, depth + 1):
                Crawl_Jobs.bump_counters(job_id, delta_total=1)

    # Thin-page filter
    if options.get("skipThinPages", True):
        threshold = int(options.get("thinThreshold", DEFAULT_THIN_THRESHOLD))
        if _extract_text_length(html) < threshold:
            Crawl_Jobs.update_page_state(page_id, PAGE_SKIPPED, reason=f"thin (<{threshold} chars)")
            Crawl_Jobs.bump_counters(job_id, delta_skipped=1)
            return

    # Dedupe by filename slug against this bot's existing files.
    canonical_final = _canonicalize(final_url) or url
    filename = _url_to_filename(canonical_final)
    if options.get("skipAlreadyImported", True):
        existing = (
            EMLYFiles.select(EMLYFiles.id)
            .where((EMLYFiles.bot == bot_id) & (EMLYFiles.file_name == filename))
            .first()
        )
        if existing is not None:
            Crawl_Jobs.update_page_state(
                page_id,
                PAGE_SKIPPED,
                reason="already imported",
                file_id=str(existing.id),
            )
            Crawl_Jobs.bump_counters(job_id, delta_skipped=1)
            return

    # Persist file + insert row
    Crawl_Jobs.update_page_state(page_id, PAGE_UPLOADING)
    file_id = str(uuid.uuid4())
    target_dir = _bot_uploads_dir(bot_id) / file_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    rewritten_html = _resolve_relative_urls(html, final_url)
    body_bytes = rewritten_html.encode("utf-8")
    target_path.write_bytes(body_bytes)
    sha = hashlib.sha256(body_bytes).hexdigest()

    Emly_Files.insert_new_file(
        id=file_id,
        bot_id=bot_id,
        file_name=filename,
        file_type="text/html",
        file_size=len(body_bytes),
        size_bytes=len(body_bytes),
        mime_type="text/html",
        sha256=sha,
        document_type=job.get("document_type") or "web_page",
    )

    Crawl_Jobs.update_page_state(page_id, PAGE_EMBEDDING, file_id=file_id)

    # Synchronous embed (in a thread so the asyncio loop stays free).
    from routes.admin_bot_files import _embed_file_sync  # avoid import cycle at module load

    await asyncio.to_thread(
        _embed_file_sync,
        bot_id,
        file_id,
        str(target_path),
        canonical_final,
    )

    refreshed = Emly_Files.get_by_id(bot_id, file_id) or {}
    if refreshed.get("embedding_status") == EMBEDDING_STATUS_EMBEDDED:
        Crawl_Jobs.update_page_state(page_id, PAGE_DONE, file_id=file_id)
        Crawl_Jobs.bump_counters(job_id, delta_done=1)
    else:
        Crawl_Jobs.update_page_state(
            page_id,
            PAGE_FAILED,
            reason=refreshed.get("error_message") or "embed failed",
            file_id=file_id,
        )
        Crawl_Jobs.bump_counters(job_id, delta_failed=1)


async def _run_job(job_id: str) -> None:
    """Drive a single crawl job to completion."""
    job = Crawl_Jobs.get_any(job_id)
    if not job or job["status"] != JOB_STATUS_RUNNING:
        return

    options = job["options"] or {}
    bot_id = job["bot_id"]

    # Recovery sweep: any in-flight pages from a prior worker run go back to queued.
    Crawl_Jobs.reset_in_flight_for_job(job_id)

    seed_canon = _canonicalize(job["seed_url"])
    if not seed_canon:
        Crawl_Jobs.update_status(job_id, "failed", error_message="invalid seed URL")
        return
    seed_host = urlparse(seed_canon).hostname or ""

    # First-run seed: empty page table means we never started.
    if Crawl_Jobs.count_pages(job_id) == 0:
        if Crawl_Jobs.add_page_if_new(job_id, seed_canon, 0):
            Crawl_Jobs.bump_counters(job_id, delta_total=1)

    concurrency = int(options.get("concurrency", DEFAULT_CONCURRENCY))
    max_pages = int(options.get("maxPages", 100))
    semaphore = asyncio.Semaphore(concurrency)
    robots_cache: Dict[str, list[str]] = {}
    last_fetch_by_host: Dict[str, float] = {}
    in_flight: set[asyncio.Task] = set()

    async def runner(page: dict) -> None:
        async with semaphore:
            try:
                await _process_page(job, page, seed_host, robots_cache, last_fetch_by_host, options)
            except Exception:
                log.exception("Crawl page processor crashed job=%s url=%s", job_id, page.get("url"))
                try:
                    Crawl_Jobs.update_page_state(page["id"], PAGE_FAILED, reason="worker crashed")
                    Crawl_Jobs.bump_counters(job_id, delta_failed=1)
                except Exception:
                    pass

    while True:
        cur = Crawl_Jobs.get_any(job_id)
        if not cur or cur["status"] != JOB_STATUS_RUNNING:
            break
        if cur["pages_total"] >= max_pages and not Crawl_Jobs.has_pages_in_flight(job_id):
            break

        page = Crawl_Jobs.claim_next_queued(job_id)
        if page is None:
            if not in_flight:
                # Truly nothing to do — but only if no peer is still running.
                if not Crawl_Jobs.has_pages_in_flight(job_id):
                    break
                await asyncio.sleep(0.25)
                continue
            done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            in_flight = set(in_flight)
            continue

        # Cap on total pages in flight + scheduled
        if cur["pages_total"] > max_pages:
            # We've already discovered more than max_pages; skip the overflow.
            Crawl_Jobs.update_page_state(page["id"], PAGE_SKIPPED, reason="max pages reached")
            Crawl_Jobs.bump_counters(job_id, delta_skipped=1)
            continue

        task = asyncio.create_task(runner(page))
        in_flight.add(task)

        # Throttle: keep at most ``concurrency`` runners in flight.
        if len(in_flight) >= concurrency:
            done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            in_flight = set(in_flight)

    if in_flight:
        await asyncio.gather(*in_flight, return_exceptions=True)

    final = Crawl_Jobs.get_any(job_id)
    if final and final["status"] == JOB_STATUS_RUNNING:
        Crawl_Jobs.update_status(job_id, JOB_STATUS_COMPLETED, completed=True)


async def _supervisor_loop() -> None:
    """Periodically pick up running jobs and drive them."""
    while True:
        try:
            running = Crawl_Jobs.list_running()
            for job in running:
                job_id = job["id"]
                if job_id in _active_jobs and not _active_jobs[job_id].done():
                    continue
                _active_jobs[job_id] = asyncio.create_task(_run_job(job_id))

            # GC finished tasks
            for job_id in list(_active_jobs.keys()):
                if _active_jobs[job_id].done():
                    _active_jobs.pop(job_id, None)
        except Exception:
            log.exception("Crawl supervisor tick failed")
        await asyncio.sleep(SUPERVISOR_INTERVAL_S)


def start_supervisor() -> None:
    """Idempotent: start the supervisor if it isn't running yet.

    Must be called from inside a running event loop (e.g. a FastAPI
    ``async`` startup hook). Uses ``asyncio.create_task`` rather than
    ``get_event_loop().create_task`` because the latter is deprecated
    in Python 3.12+ and emits noisy warnings on cold start.
    """
    global _supervisor_task
    if _supervisor_task is not None and not _supervisor_task.done():
        return
    _supervisor_task = asyncio.create_task(_supervisor_loop())
    log.info("Crawl-worker supervisor started")


def kick(job_id: str) -> None:
    """Inform the supervisor a new job exists. The next tick will pick it up.
    Provided as an extension point for tests; the supervisor poll is short
    enough that an explicit kick isn't required for correctness today.
    """
    log.debug("Crawl-worker kick requested job=%s", job_id)
