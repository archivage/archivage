"""
Web page archiving: fetch HTML, extract content via trafilatura, save as markdown.
"""

import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
import trafilatura
from lxml import html as lxml_html

from .log import logger


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; archivage/0.1)",
    "Accept": "text/html,application/xhtml+xml",
}


class LinkExtractor(HTMLParser):
    """Extract href values from <a> tags."""

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)


def fetchPage(url: str, timeout: float = 30.0) -> str:
    """Fetch a URL and return HTML content."""
    response = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def normalizeHtml(html: str) -> str:
    """Normalize HTML for better content extraction.

    Converts <br><br> paragraph breaks to proper <p> tags.
    """
    return re.sub(r"<br\s*/?>\s*<br\s*/?>", "</p><p>", html)


def xmlToMarkdown(xml_str: str) -> str:
    """Convert trafilatura XML output to clean markdown.

    Handles <p>, <hi> (emphasis), <ref> (links), <head> (headings), <list>/<item>.
    """
    root = ElementTree.fromstring(xml_str)

    paragraphs = []
    for elem in root.iter():
        if elem.tag in ("p", "head", "item", "quote"):
            text = elemToText(elem).strip()
            if not text:
                continue
            if elem.tag == "head":
                rend = elem.get("rend", "h2")
                level = int(rend[1]) if rend.startswith("h") and rend[1:].isdigit() else 2
                paragraphs.append(f"{'#' * level} {text}")
            elif elem.tag == "item":
                paragraphs.append(f"- {text}")
            elif elem.tag == "quote":
                paragraphs.append(f"> {text}")
            else:
                paragraphs.append(text)

    return "\n\n".join(paragraphs)


def elemToText(elem) -> str:
    """Recursively extract text from an XML element, applying markdown formatting."""
    parts = []
    if elem.text:
        parts.append(elem.text)

    for child in elem:
        if child.tag == "hi":
            rend = child.get("rend", "")
            inner = elemToText(child).strip()
            if "#i" in rend:
                parts.append(f"*{inner}*")
            elif "#b" in rend:
                parts.append(f"**{inner}**")
            else:
                parts.append(inner)
        elif child.tag == "ref":
            inner = elemToText(child).strip()
            target = child.get("target", "")
            if target and not target.startswith("#"):
                parts.append(f"[{inner}]({target})")
            else:
                # Footnote ref — just output the text, parent already has brackets
                parts.append(inner)
        else:
            parts.append(elemToText(child))

        if child.tail:
            parts.append(child.tail)

    return "".join(parts)


def convertToMarkdown(html: str, url: str) -> dict:
    """Extract main content as markdown using trafilatura.

    Returns dict with: title, content, author, date.
    """
    tree = lxml_html.fromstring(html)
    meta = trafilatura.extract_metadata(tree)

    normalized = normalizeHtml(html)
    xml_output = trafilatura.extract(
        normalized,
        output_format="xml",
        include_links=True,
        url=url,
    )

    content = ""
    if xml_output:
        content = xmlToMarkdown(xml_output)

    return {
        "title": meta.title if meta else None,
        "author": meta.author if meta else None,
        "date": meta.date if meta else None,
        "content": content,
    }


def extractLinks(html: str, base_url: str, same_domain: bool = True) -> list[str]:
    """Extract links from HTML, resolved to absolute URLs.

    If same_domain is True, only returns links on the same domain.
    Filters out anchors, mailto, javascript, and non-HTML extensions.
    """
    parser = LinkExtractor()
    parser.feed(html)

    base_parsed = urlparse(base_url)
    skip_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".zip", ".gz", ".tar", ".css", ".js"}
    seen = set()
    result = []

    for href in parser.links:
        if href.startswith(("#", "mailto:", "javascript:")):
            continue

        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)

        # Strip fragment
        clean = parsed._replace(fragment="").geturl()

        if clean in seen:
            continue
        seen.add(clean)

        # Skip non-HTML
        ext = Path(parsed.path).suffix.lower()
        if ext in skip_extensions:
            continue

        if same_domain and parsed.netloc != base_parsed.netloc:
            continue

        result.append(clean)

    return result


def urlToFilename(url: str) -> str:
    """Convert URL to a markdown filename.

    /foo/bar.html -> foo/bar.md
    /foo/bar -> foo/bar.md
    / -> index.md
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return "index.md"

    p = Path(path)
    if p.suffix in (".html", ".htm"):
        return str(p.with_suffix(".md"))
    return path + ".md"


def formatMarkdown(meta: dict, url: str) -> str:
    """Format extracted content as markdown with YAML frontmatter."""
    lines = ["---"]
    if meta.get("title"):
        title = meta["title"].replace('"', '\\"')
        lines.append(f'title: "{title}"')
    if meta.get("author"):
        author = meta["author"].replace('"', '\\"')
        lines.append(f'author: "{author}"')
    if meta.get("date"):
        lines.append(f'date: "{meta["date"]}"')
    lines.append(f"url: {url}")
    lines.append(f"archived: {time.strftime('%Y-%m-%d')}")
    lines.append("---")
    lines.append("")

    if meta.get("title"):
        lines.append(f"# {meta['title']}")
        lines.append("")

    content = meta.get("content", "").strip()
    if content:
        lines.append(content)
        lines.append("")

    return "\n".join(lines)


def savePage(url: str, archive_dir: Path) -> Path:
    """Fetch, convert, and save a single page. Returns the output path."""
    parsed = urlparse(url)
    domain_dir = archive_dir / "web" / parsed.netloc
    filename = urlToFilename(url)
    out_path = domain_dir / filename

    out_path.parent.mkdir(parents=True, exist_ok=True)

    html = fetchPage(url)
    meta = convertToMarkdown(html, url)
    markdown = formatMarkdown(meta, url)

    out_path.write_text(markdown, encoding="utf-8")
    return out_path


def saveAll(index_url: str, archive_dir: Path, delay: float = 0.5,
            on_progress=None) -> list[Path]:
    """Fetch an index page, follow all same-domain links, save each as markdown.

    on_progress(i, total, url, status) is called for each link if provided.
    Returns list of saved file paths.
    """
    html = fetchPage(index_url)
    links = extractLinks(html, index_url, same_domain=True)

    logger.info(f"Found {len(links)} links on {index_url}")
    total = len(links)

    saved = []
    skipped = 0
    failed = 0
    for i, url in enumerate(links):
        filename = urlToFilename(url)
        parsed = urlparse(url)
        domain_dir = archive_dir / "web" / parsed.netloc
        out_path = domain_dir / filename

        if out_path.exists():
            skipped += 1
            if on_progress:
                on_progress(i + 1, total, filename, "skip")
            continue

        try:
            path = savePage(url, archive_dir)
            saved.append(path)
            if on_progress:
                on_progress(i + 1, total, filename, "saved")
            if i < len(links) - 1:
                time.sleep(delay)
        except Exception as e:
            failed += 1
            logger.warning(f"Failed {url}: {e}")
            if on_progress:
                on_progress(i + 1, total, filename, f"error: {e}")

    return saved
