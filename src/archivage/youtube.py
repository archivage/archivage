"""
YouTube transcript archiving: fetch metadata + auto/manual subtitles via yt-dlp,
reflow into paragraphs, save as markdown with frontmatter.
"""

import json
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Callable

from .log import logger


VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)?([a-zA-Z0-9_-]{11})")


def extractVideoId(url: str) -> str:
    """Pull the 11-char video ID from a YouTube URL or bare ID."""
    m = VIDEO_ID_RE.search(url)
    if not m:
        raise ValueError(f"Could not extract video ID from: {url}")
    return m.group(1)


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text


def runYtDlp(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Invoke yt-dlp with --no-update; raise on failure."""
    cmd = ["yt-dlp", "--no-update", *args]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()[:500]}")
    return result


def fetchMetadata(url: str, work_dir: Path) -> dict:
    """Fetch video metadata as info.json. Returns parsed dict."""
    video_id = extractVideoId(url)
    runYtDlp(
        ["--skip-download", "--write-info-json", "-o", "%(id)s", url],
        cwd=work_dir,
    )
    info_path = work_dir / f"{video_id}.info.json"
    return json.loads(info_path.read_text())


def collectionVideos(url: str, work_dir: Path,
                     limit: int | None = None) -> list[str]:
    """List canonical video URLs from a channel tab or playlist.

    Discovery is intentionally flat: metadata and subtitles are fetched later
    by ``saveVideo``. This keeps the first request small and lets an interrupted
    batch resume through the archive's per-video idempotency.
    """
    args = ['--flat-playlist', '--dump-single-json']
    if limit is not None:
        if limit < 1:
            raise ValueError('limit must be greater than zero')
        args.extend(['--playlist-end', str(limit)])
    args.append(url)

    result = runYtDlp(args, cwd=work_dir)
    data = json.loads(result.stdout)
    entries = data.get('entries') or []
    videos = []
    seen = set()
    for entry in entries:
        if not entry:
            continue
        video_id = entry.get('id')
        if not video_id:
            entry_url = entry.get('url') or entry.get('webpage_url') or ''
            try:
                video_id = extractVideoId(entry_url)
            except ValueError:
                continue
        if not re.fullmatch(r'[a-zA-Z0-9_-]{11}', video_id):
            continue
        if video_id in seen:
            continue
        seen.add(video_id)
        videos.append(f'https://www.youtube.com/watch?v={video_id}')
    return videos


def pickSubtitleLang(info: dict, preferred: str | None = None) -> tuple[str, bool]:
    """Pick the best subtitle language. Returns (lang_code, is_auto).

    Preference: explicit `preferred` > manual subs > auto en-orig > auto en > auto fr > first auto.
    """
    manual = {
        lang: formats
        for lang, formats in (info.get('subtitles') or {}).items()
        if lang != 'live_chat'
    }
    auto = info.get("automatic_captions") or {}

    if preferred:
        if preferred in manual:
            return preferred, False
        if preferred in auto:
            return preferred, True
        raise RuntimeError(f"No subtitles in language '{preferred}'")

    if manual:
        return next(iter(manual.keys())), False

    for candidate in ("en-orig", "en", "fr"):
        if candidate in auto:
            return candidate, True

    if auto:
        return next(iter(auto.keys())), True

    raise RuntimeError("No subtitles available for this video")


def fetchTranscript(url: str, lang: str, is_auto: bool, work_dir: Path) -> str:
    """Download subtitles, convert to SRT, return cleaned plain text (no timestamps)."""
    flag = "--write-auto-subs" if is_auto else "--write-subs"
    srt_files = []
    for attempt in range(3):
        runYtDlp(
            ['--skip-download', '--retries', '3', flag, '--sub-lang', lang,
             '--sub-format', 'srt', '-o', 'transcript.%(ext)s', url],
            cwd=work_dir,
        )
        srt_files = list(work_dir.glob('*.srt'))
        if srt_files:
            break
        if attempt < 2:
            time.sleep(2 ** attempt)
    if not srt_files:
        raise RuntimeError('No SRT file produced by yt-dlp after 3 attempts')

    srt = srt_files[0].read_text()
    return srtToText(srt)


def srtToText(srt: str) -> str:
    """Strip SRT cues and tags, return continuous text."""
    lines = []
    for line in srt.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.isdigit():
            continue
        if "-->" in line:
            continue
        line = re.sub(r"<[^>]*>", "", line)
        lines.append(line)
    text = " ".join(lines)
    return re.sub(r"\s+", " ", text).strip()


def sentencesToParagraphs(text: str, target_sentences: int = 4) -> list[str]:
    """Split into sentences, group target_sentences per paragraph."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    paragraphs = []
    for i in range(0, len(sentences), target_sentences):
        paragraphs.append(" ".join(sentences[i:i + target_sentences]))
    return paragraphs or [text]


def inferGuest(title: str, host: str) -> str | None:
    """Heuristic: pull a guest name from the title.

    Patterns: "X with Y", "X feat. Y", "X ft. Y", "X - Y", "X | Y", "X × Y".
    Returns None if nothing matches.
    """
    patterns = [
        r"\s+with\s+([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})\s*$",
        r"\s+feat\.?\s+([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})\s*$",
        r"\s+ft\.?\s+([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})\s*$",
        r"\s+[-–—|×]\s+([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})\s*$",
    ]
    for pattern in patterns:
        m = re.search(pattern, title)
        if m:
            candidate = m.group(1).strip()
            if candidate.lower() != host.lower():
                return candidate
    return None


def reflow(raw: str, host: str, guest: str | None) -> str:
    """Build the markdown body from cleaned transcript text.

    Splits on `>>` speaker markers from YouTube auto-captions. Without `>>`,
    treats the whole thing as a single-speaker monologue.
    """
    text = re.sub(r"\s+", " ", raw).strip()
    text = re.sub(r"&gt;&gt;|&#62;&#62;", ">>", text)

    parts = re.split(r"\s*>>\s*", text)
    parts = [p.strip() for p in parts if p.strip()]

    # Single speaker: no labels, just paragraphs.
    if len(parts) <= 1:
        return "\n\n".join(sentencesToParagraphs(parts[0] if parts else text))

    speakers = [host, guest or "Guest"]
    blocks = []
    for i, chunk in enumerate(parts):
        speaker = speakers[i % 2]
        paragraphs = sentencesToParagraphs(chunk)
        blocks.append(f"**{speaker}** — " + paragraphs[0])
        if len(paragraphs) > 1:
            blocks.append("\n\n".join(paragraphs[1:]))
    return "\n\n".join(blocks)


def formatMarkdown(info: dict, body: str, video_id: str, lang: str,
                   is_auto: bool) -> str:
    """Compose YAML frontmatter + body."""
    title = info["title"]
    upload = info["upload_date"]  # YYYYMMDD
    upload_iso = f"{upload[:4]}-{upload[4:6]}-{upload[6:]}"
    duration = info.get("duration_string", "")
    channel = info.get("channel", "")
    channel_handle = info.get("uploader_id", "")
    channel_id = info.get("channel_id", "")
    url = f"https://www.youtube.com/watch?v={video_id}"
    title_esc = title.replace('"', '\\"')
    channel_esc = channel.replace('"', '\\"')

    fm = ["---"]
    fm.append(f'title: "{title_esc}"')
    fm.append(f'channel: "{channel_esc}"')
    if channel_handle:
        fm.append(f'channel_handle: "{channel_handle}"')
    if channel_id:
        fm.append(f'channel_id: "{channel_id}"')
    fm.append(f'video_id: "{video_id}"')
    fm.append(f"url: {url}")
    fm.append(f'upload_date: "{upload_iso}"')
    if duration:
        fm.append(f'duration: "{duration}"')
    fm.append(f'language: "{lang.removesuffix("-orig")}"')
    fm.append(f"captions: {'auto' if is_auto else 'manual'}")
    fm.append(f"archived: {date.today().isoformat()}")
    fm.append("---")
    fm.append("")
    fm.append(f"# {title}")
    fm.append("")
    fm.append("")

    return "\n".join(fm) + body + "\n"


def videoOutputPath(info: dict, video_id: str, archive_dir: Path) -> Path:
    """Compute the canonical archive path for a video."""
    handle = info.get("uploader_id") or "_orphans"
    upload = info["upload_date"]
    upload_iso = f"{upload[:4]}-{upload[4:6]}-{upload[6:]}"
    slug = slugify(info["title"])
    return archive_dir / "youtube" / handle / f"{upload_iso}-{slug}-{video_id}.md"


def saveVideo(url: str, archive_dir: Path,
              lang: str | None = None,
              host: str | None = None,
              guest: str | None = None,
              force: bool = False) -> tuple[Path, str]:
    """Fetch metadata + transcript for a YouTube video, save as markdown.

    Idempotent: skips if the destination file already exists, unless force=True.
    Returns (path, status) where status is "saved" or "skipped".
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is required (sudo apt install yt-dlp)")

    video_id = extractVideoId(url)

    with tempfile.TemporaryDirectory(prefix="archivage-yt-") as tmp:
        work_dir = Path(tmp)

        info = fetchMetadata(url, work_dir)
        out_path = videoOutputPath(info, video_id, archive_dir)

        if out_path.exists() and not force:
            logger.info(f"Skip (exists): {out_path}")
            return out_path, "skipped"

        chosen_lang, is_auto = pickSubtitleLang(info, preferred=lang)
        raw = fetchTranscript(url, chosen_lang, is_auto, work_dir)

    actual_host = host or info.get("channel") or "Host"
    actual_guest = guest or inferGuest(info["title"], actual_host)
    body = reflow(raw, actual_host, actual_guest)

    markdown = formatMarkdown(info, body, video_id, chosen_lang, is_auto)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    return out_path, "saved"


def saveCollection(url: str, archive_dir: Path,
                   lang: str | None = None,
                   force: bool = False,
                   limit: int | None = None,
                   fail_fast: bool = False,
                   progress: Callable[[int, int, str, str], None] | None = None
                   ) -> dict:
    """Archive every transcript available in a channel tab or playlist.

    Videos without captions and transient failures are reported and do not
    prevent the remaining collection from being archived, unless
    ``fail_fast`` is requested.
    """
    if not shutil.which('yt-dlp'):
        raise RuntimeError('yt-dlp is required (sudo apt install yt-dlp)')

    with tempfile.TemporaryDirectory(prefix='archivage-yt-list-') as tmp:
        videos = collectionVideos(url, Path(tmp), limit=limit)

    summary = {
        'discovered': len(videos),
        'saved': 0,
        'skipped': 0,
        'failed': [],
    }
    for i, video_url in enumerate(videos, start=1):
        try:
            path, status = saveVideo(
                video_url,
                archive_dir,
                lang=lang,
                force=force,
            )
            summary[status] += 1
            detail = str(path)
        except Exception as e:
            summary['failed'].append({'url': video_url, 'error': str(e)})
            status = 'failed'
            detail = str(e)
            if progress:
                progress(i, len(videos), video_url, f'{status}: {detail}')
            if fail_fast:
                raise
            continue
        if progress:
            progress(i, len(videos), video_url, f'{status}: {detail}')
    return summary


def archiveStats(archive_dir: Path) -> dict:
    """Count archived YouTube transcripts by channel."""
    yt_dir = archive_dir / "youtube"
    if not yt_dir.exists():
        return {"total": 0, "channels": {}}

    channels = {}
    total = 0
    for channel_dir in sorted(yt_dir.iterdir()):
        if not channel_dir.is_dir():
            continue
        count = sum(1 for _ in channel_dir.glob("*.md"))
        if count:
            channels[channel_dir.name] = count
            total += count
    return {"total": total, "channels": channels}
