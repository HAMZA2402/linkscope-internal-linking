"""LinkScope V2: conservative, evidence-based internal link recommendations.

Only existing, unlinked text nodes in paragraph/list content can become anchors.
This project does not edit or publish client websites.
"""
from __future__ import annotations

import csv
import io
import ipaddress
import math
import re
import socket
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote

import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString

USER_AGENT = "LinkScopeSEOAudit/1.0 (+manual, bounded; respects robots.txt)"
MAX_BYTES_HTML = 1_500_000
MAX_BYTES_XML = 1_800_000
MAX_SITEMAP_FILES = 8
MAX_SITEMAP_URLS = 250
MAX_PAGE_COUNT = 60
MAX_LINKS_PER_SOURCE = 5

STOPWORDS = set('''a an the and or but for to of on at by from in into with without as is are was were be been being this that these those how what why where when who whose which your our their its it they them we us i me you my do does did can could should would will may might about through using use guide guides basics overview complete best top tips things important helpful more learn everything all new key step steps page pages services service firm firms office attorney attorneys lawyer lawyers law legal near local information protect protection need needs right get getting work working what means explained understanding'''.split())
STOPWORDS.update({'northstar','example','services','llc','inc'})
SKIP_SOURCE_PATHS = ('/contact', '/testimonials', '/attorney-referral', '/referral', '/reviews/', '/thank-you', '/privacy', '/terms', '/disclaimer', '/cookie', '/case-results/')
SKIP_TARGET_PATHS = ('/privacy', '/terms', '/disclaimer', '/cookie', '/search', '/tag/', '/author/', '/category/', '/feed', '/wp-admin', '/contact', '/about', '/sitemap')
EXCLUDE_TAGS = ['script', 'style', 'noscript', 'nav', 'footer', 'header', 'aside', 'form', 'button', 'svg', 'iframe', 'template', 'blockquote']
EXCLUDE_SELECTORS = [
    '.testimonial', '.testimonials', '[class*=\"testimonial\"]',
    '.review', '[class*=\"review-card\"]', '.elementor-testimonial',
    '.comments', '#comments', '.comment-list', '.breadcrumb', '.breadcrumbs',
    '.related-posts', '.related-articles', '.author-box', '.social-share',
    '.share-buttons', '.newsletter', '.sidebar', '.popup', '.modal',
    '.elementor-widget-form', '.elementor-widget-button',
    '.elementor-widget-icon-list', '.wp-block-latest-posts',
    '.post-navigation', '.table-of-contents', '.toc', '.cta', '.cta-section',
]

REQUIRED_FIELDS = ('URL', 'Content')


class AuditInputError(ValueError):
    pass


@dataclass
class Page:
    url: str
    title: str
    h1: str
    content: str
    paragraphs: list[str] = field(default_factory=list)
    unlinked_nodes: list[tuple[str, str]] = field(default_factory=list)  # (text node, context)
    existing_links: set[str] = field(default_factory=set)
    focus_keywords: str = ''
    noindex: bool = False
    canonical: str = ''
    source: str = 'CSV'
    outlinks_known: bool = True


def clean_space(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def normalize_url(url: str) -> str:
    parts = urlsplit(str(url).strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or '').lower().rstrip('.')
    if scheme not in ('http', 'https') or not host:
        raise AuditInputError('Use a full website URL beginning with http:// or https://.')
    if parts.username or parts.password:
        raise AuditInputError('URLs with embedded usernames/passwords are not allowed.')
    if parts.port and parts.port not in (80, 443):
        raise AuditInputError('Custom ports are not supported.')
    path = re.sub('/+', '/', unquote(parts.path or '/'))
    if not path.startswith('/'):
        path = '/' + path
    # Preserve file-like URLs such as /robots.txt, /sitemap.xml and images.
    looks_like_file = bool(re.search(r'/[^/]+\.[A-Za-z0-9]{1,8}$', path))
    if len(path) > 1 and not looks_like_file:
        path = path.rstrip('/') + '/'
    netloc = host
    return urlunsplit((scheme, netloc, path, '', ''))


def eligible_host(host: str) -> bool:
    try:
        if not host or host in ('localhost', 'localhost.localdomain') or host.endswith(('.local', '.internal', '.test')):
            return False
        if '.' not in host:
            return False
        ips = [entry[4][0] for entry in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
        return bool(ips) and all(ipaddress.ip_address(ip).is_global for ip in ips)
    except (socket.gaierror, ValueError, OSError):
        return False


def host_family(url: str) -> set[str]:
    host = urlsplit(url).hostname or ''
    if host.startswith('www.'):
        return {host, host[4:]}
    return {host, 'www.' + host}


def url_allowed_for_network(url: str, hosts: set[str]) -> str:
    url = normalize_url(url)
    host = urlsplit(url).hostname or ''
    if host not in hosts:
        raise AuditInputError('Link points outside the selected domain.')
    if not eligible_host(host):
        raise AuditInputError('Link resolves to a non-public or unavailable host; request was blocked.')
    return url


def relative_url_same_domain(raw: str, base_url: str, hosts: set[str]) -> str | None:
    try:
        full = normalize_url(urljoin(base_url, raw))
        if urlsplit(full).hostname not in hosts:
            return None
        return full
    except (AuditInputError, ValueError):
        return None


def limited_request(session: requests.Session, url: str, hosts: set[str], max_bytes: int = MAX_BYTES_HTML, redirects: int = 3):
    """Validate every redirect and place a strict cap on response bytes."""
    next_url = url
    for _ in range(redirects + 1):
        next_url = url_allowed_for_network(next_url, hosts)
        try:
            response = session.get(next_url, timeout=(5, 12), allow_redirects=False, stream=True, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xml,text/xml,*/*;q=0.7'})
        except requests.RequestException as exc:
            raise AuditInputError(f'Cannot fetch {next_url}: {str(exc)[:180]}') from exc
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('location', '')
            response.close()
            if not location:
                raise AuditInputError('Redirect response missing Location header.')
            next_url = urljoin(next_url, location)
            continue
        if response.status_code >= 400:
            code = response.status_code
            response.close()
            raise AuditInputError(f'HTTP {code} for {next_url}')
        chunks = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=32768):
                total += len(chunk)
                if total > max_bytes:
                    raise AuditInputError('Response exceeds the size limit.')
                chunks.append(chunk)
            result = (response.url, b''.join(chunks), response.headers.get('Content-Type', ''), dict(response.headers))
        finally:
            response.close()
        return result
    raise AuditInputError('Too many redirects.')


class RobotsGuard:
    def __init__(self, session: requests.Session, hosts: set[str]):
        self.session = session
        self.hosts = hosts
        self.parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.failures: list[str] = []
        self.sitemap_urls: list[str] = []

    def get_parser(self, url: str):
        parts = urlsplit(url)
        origin = f'{parts.scheme}://{parts.netloc}'
        if origin in self.parsers:
            return self.parsers[origin]
        robots_url = origin + '/robots.txt'
        try:
            _, content, _, _ = limited_request(self.session, robots_url, self.hosts, max_bytes=200_000)
            txt = content.decode('utf-8', errors='replace')
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(txt.splitlines())
            self.parsers[origin] = parser
            for line in txt.splitlines():
                if re.match(r'^\s*Sitemap\s*:', line, re.I):
                    possible = line.split(':', 1)[1].strip()
                    if relative_url_same_domain(possible, robots_url, self.hosts):
                        self.sitemap_urls.append(possible)
            return parser
        except AuditInputError as exc:
            message = str(exc)
            if 'HTTP 404' in message or 'HTTP 410' in message:
                self.parsers[origin] = None  # no robots.txt, crawling allowed
                return None
            self.failures.append('Robots rules unavailable for ' + origin + ': ' + message)
            self.parsers[origin] = False  # type: ignore[assignment]
            return False

    def can_fetch(self, url: str) -> bool:
        parser = self.get_parser(url)
        if parser is False:
            return False
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def delay(self, url: str) -> float:
        parser = self.get_parser(url)
        if not parser:
            return 0.0
        delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay('*') or 0
        return min(10.0, float(delay))


def _extract_blocks(scope: BeautifulSoup) -> tuple[list[str], list[tuple[str, str]]]:
    """Read editable editorial copy and preserve exact unlinked text-node evidence."""
    for tag in list(scope.find_all(EXCLUDE_TAGS)):
        tag.decompose()
    for selector in EXCLUDE_SELECTORS:
        for item in list(scope.select(selector)):
            item.decompose()
    paragraphs: list[str] = []
    nodes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for block in scope.select('p, li, div'):
        # Div fallback supports visual builders that render paragraphs as text-only divs.
        if block.name == 'div' and block.find(['p', 'li', 'div']):
            continue
        paragraph = clean_space(block.get_text(' ', strip=True))
        if len(paragraph) < 32 or paragraph in seen or len(paragraph) > 2200:
            continue
        seen.add(paragraph)
        paragraphs.append(paragraph)
        for node in block.find_all(string=True):
            if not isinstance(node, NavigableString) or not node.parent:
                continue
            if any(parent.name in ('a', 'button', 'code', 'pre') for parent in [node.parent] + list(node.parents)):
                continue
            plain = clean_space(str(node))
            if len(plain) >= 8:
                nodes.append((plain, paragraph))
        if len(paragraphs) >= 350:
            break
    return paragraphs, nodes


def parse_html(url: str, raw_html: bytes, source: str = 'Crawl') -> Page:
    soup = BeautifulSoup(raw_html, 'html.parser')
    title = clean_space(soup.title.get_text(' ', strip=True)) if soup.title else ''
    h1_node = soup.find('h1')
    h1 = clean_space(h1_node.get_text(' ', strip=True)) if h1_node else ''
    robots_meta = soup.find('meta', attrs={'name': re.compile(r'^robots$', re.I)})
    noindex = bool(robots_meta and 'noindex' in (robots_meta.get('content') or '').lower())
    canonical_node = soup.find('link', attrs={'rel': lambda r: r and 'canonical' in str(r).lower()})
    canonical = urljoin(url, canonical_node.get('href', '')) if canonical_node else ''
    existing_links: set[str] = set()
    for anchor in soup.select('a[href]'):
        try:
            existing_links.add(normalize_url(urljoin(url, anchor['href'])))
        except (AuditInputError, ValueError):
            continue

    # Content-builder friendly extraction: prefer article bodies, while checking
    # larger candidates when the selected template only exposes an excerpt.
    selectors = (
        '[itemprop=articleBody]', '.elementor-widget-theme-post-content',
        'article .entry-content', '.entry-content', 'article .post-content',
        '.post-content', '.elementor-location-single', 'main article',
        'article', 'main', '[role=main]', '.site-main', 'body',
    )
    examined: set[int] = set()
    best: tuple[float, list[str], list[tuple[str, str]]] = (-1.0, [], [])
    for priority, selector in enumerate(selectors):
        for candidate in soup.select(selector)[:3]:
            if id(candidate) in examined:
                continue
            examined.add(id(candidate))
            scope = BeautifulSoup(str(candidate), 'html.parser')
            paragraphs, nodes = _extract_blocks(scope)
            word_count = sum(len(p.split()) for p in paragraphs)
            if not nodes:
                continue
            specificity_bonus = max(0, 11 - priority) * 2.0
            score = word_count + specificity_bonus
            if score > best[0]:
                best = (score, paragraphs, nodes)
            if word_count >= 180 and priority <= 3:
                # Dedicated article-body container with substantial editorial copy.
                break
        if best[0] > 230 and priority <= 3:
            break
    paragraphs, nodes = best[1], best[2]
    return Page(url=normalize_url(url), title=title or h1 or url, h1=h1,
                content=' '.join(paragraphs), paragraphs=paragraphs, unlinked_nodes=nodes,
                existing_links=existing_links, noindex=noindex,
                canonical=canonical, source=source, outlinks_known=True)


def read_csv_pages(content: bytes) -> tuple[list[Page], list[str]]:
    if len(content) > 8_000_000:
        raise AuditInputError('CSV is too large. Use a smaller export (8 MB limit).')
    raw = None
    for encoding in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            raw = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise AuditInputError('Could not decode the CSV; please export it as UTF-8.')
    try:
        df = pd.read_csv(io.StringIO(raw), dtype=str, keep_default_na=False, on_bad_lines='error')
    except (pd.errors.ParserError, ValueError, UnicodeError) as exc:
        raise AuditInputError(f'CSV format problem: {exc}') from exc
    lowered = {re.sub(r'[^a-z0-9]', '', str(col).lower()): col for col in df.columns}
    def take(row, *names):
        for n in names:
            field_name = lowered.get(re.sub(r'[^a-z0-9]', '', n.lower()))
            if field_name:
                return str(row.get(field_name, ''))
        return ''
    pages: list[Page] = []
    warnings: list[str] = []
    no_text = 0
    dupes = 0
    seen = set()
    has_links = any(k in lowered for k in ('existinglinks', 'outlinks', 'links'))
    for _, row in df.iterrows():
        candidate = take(row, 'URL', 'Address', 'Page URL')
        if not candidate:
            continue
        try:
            url = normalize_url(candidate)
        except AuditInputError:
            continue
        if url in seen:
            dupes += 1
            continue
        seen.add(url)
        text = take(row, 'Content', 'Body Text', 'Text', 'Page Copy', 'Copy')
        html = take(row, 'HTML', 'Raw HTML')
        if html.strip():
            page = parse_html(url, html.encode('utf-8'), source='CSV HTML')
        elif text.strip():
            blocks = [clean_space(x) for x in re.split(r'\n\s*\n|\s*\|\|\s*', text) if clean_space(x)]
            if not blocks:
                blocks = [clean_space(text)]
            page = Page(url=url, title=take(row, 'Title', 'Title 1', 'Page Title') or url,
                        h1=take(row, 'H1', 'H1-1'), content=' '.join(blocks), paragraphs=blocks,
                        unlinked_nodes=[(b, b) for b in blocks if len(b) >= 12],
                        focus_keywords=take(row, 'Focus Keywords', 'Target Keywords'), source='CSV Text',
                        outlinks_known=has_links)
        else:
            no_text += 1
            continue
        raw_links = take(row, 'Existing Links', 'Outlinks', 'Links')
        if raw_links:
            for val in re.split(r'[;|\n]+', raw_links):
                if not val.strip():
                    continue
                try:
                    page.existing_links.add(normalize_url(urljoin(url, val.strip())))
                except (AuditInputError, ValueError):
                    pass
        if page.content:
            pages.append(page)
    if no_text:
        warnings.append(f'Skipped {no_text} CSV rows without Content/Body Text or HTML. An ordinary Screaming Frog metadata export alone is not enough to verify existing anchors.')
    if dupes:
        warnings.append(f'Ignored {dupes} duplicate URLs.')
    if not has_links:
        warnings.append('This CSV has no Existing Links column. Existing-link checks and link graph status are incomplete; verify each target on the live page.')
    if len(pages) < 2:
        raise AuditInputError('Need at least two valid URL rows with Content or HTML to recommend internal links.')
    return pages[:MAX_PAGE_COUNT], warnings


def parse_sitemap_xml(content: bytes) -> tuple[list[str], list[str]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], []
    name = root.tag.split('}')[-1].lower()
    locs = [clean_space(e.text or '') for e in root.iter() if e.tag.split('}')[-1].lower() == 'loc']
    if name == 'sitemapindex':
        return [], [loc for loc in locs if loc]
    if name == 'urlset':
        return [loc for loc in locs if loc], []
    return [], []


def extract_internal_links(html: bytes, origin_url: str, hosts: set[str]) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for a in soup.select('a[href]'):
        link = relative_url_same_domain(a['href'], origin_url, hosts)
        if link and link not in out and not re.search(r'\.(pdf|jpe?g|png|gif|svg|zip|css|js|xml|webp)/?$', link, re.I):
            out.append(link)
    return out[:200]


def crawl_website(site_url: str, max_pages: int = 30, min_delay: float = 0.6, sitemap_url: str = '') -> tuple[list[Page], list[str]]:
    start = normalize_url(site_url)
    hosts = host_family(start)
    # No networking for private networks, localhost, or names that resolve privately.
    url_allowed_for_network(start, hosts)
    max_pages = max(2, min(MAX_PAGE_COUNT, int(max_pages)))
    min_delay = max(0.3, min(5.0, float(min_delay)))
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})
    guard = RobotsGuard(session, hosts)
    warnings: list[str] = []
    if not guard.can_fetch(start):
        raise AuditInputError('The homepage is disallowed by robots.txt, or robots.txt could not be safely checked.')
    candidates: list[str] = []
    candidates.extend(guard.sitemap_urls)
    if sitemap_url.strip():
        user_sitemap = relative_url_same_domain(sitemap_url, start, hosts)
        if not user_sitemap:
            raise AuditInputError('Sitemap must be on the same website.')
        candidates.insert(0, user_sitemap)
    else:
        for guess in ('/sitemap.xml', '/sitemap_index.xml', '/wp-sitemap.xml'):
            candidates.append(urljoin(start, guess))
    sitemap_queue = deque(dict.fromkeys(candidates))
    seen_sitemaps: set[str] = set()
    sitemap_pages: list[str] = []
    last_request = 0.0
    while sitemap_queue and len(seen_sitemaps) < MAX_SITEMAP_FILES and len(sitemap_pages) < MAX_SITEMAP_URLS:
        candidate = sitemap_queue.popleft()
        sitemap = relative_url_same_domain(candidate, start, hosts)
        if not sitemap or sitemap in seen_sitemaps or not guard.can_fetch(sitemap):
            continue
        seen_sitemaps.add(sitemap)
        time.sleep(max(0.0, min_delay - (time.monotonic() - last_request)))
        try:
            _, data, content_type, _ = limited_request(session, sitemap, hosts, MAX_BYTES_XML)
            last_request = time.monotonic()
        except AuditInputError:
            continue
        # A sitemap URL that returns an HTML error page should be ignored.
        if not (b'<urlset' in data[:500] or b'<sitemapindex' in data[:500] or 'xml' in content_type.lower()):
            continue
        new_pages, nested = parse_sitemap_xml(data)
        sitemap_pages.extend(new_pages[:MAX_SITEMAP_URLS - len(sitemap_pages)])
        for link in nested:
            if len(seen_sitemaps) + len(sitemap_queue) < MAX_SITEMAP_FILES:
                sitemap_queue.append(link)
    queue: deque[str] = deque([start])
    for link in sitemap_pages:
        normalized = relative_url_same_domain(link, start, hosts)
        if normalized and normalized not in queue:
            queue.append(normalized)
    pages: list[Page] = []
    seen: set[str] = set()
    blocked = 0
    errors = []
    max_fetch_attempts = max_pages * 3
    attempts = 0
    while queue and len(pages) < max_pages and attempts < max_fetch_attempts:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        if not guard.can_fetch(url):
            blocked += 1
            continue
        if re.search(r'\.(pdf|jpe?g|png|gif|svg|zip|css|js|xml|webp)/?$', url, re.I):
            continue
        time.sleep(max(0.0, max(min_delay, guard.delay(url)) - (time.monotonic() - last_request)))
        attempts += 1
        try:
            resolved, html, content_type, headers = limited_request(session, url, hosts)
            last_request = time.monotonic()
            if 'html' not in content_type.lower() and b'<html' not in html[:1200].lower():
                continue
            page = parse_html(resolved, html)
            if 'noindex' in headers.get('X-Robots-Tag', '').lower():
                page.noindex = True
            if page.content:
                pages.append(page)
                # Fallback to same-site links when sitemap is absent or incomplete.
                if len(sitemap_pages) < 2:
                    for found in extract_internal_links(html, resolved, hosts):
                        if found not in seen and found not in queue and len(queue) < 200:
                            queue.append(found)
        except AuditInputError as exc:
            if len(errors) < 5:
                errors.append(f'{url}: {exc}')
    if blocked:
        warnings.append(f'{blocked} URLs skipped due to robots restrictions.')
    if errors:
        warnings.extend(errors)
    warnings.extend(guard.failures[:3])
    if attempts >= max_fetch_attempts:
        warnings.append('Stopped after the maximum bounded fetch attempts.')
    if len(pages) < 2:
        raise AuditInputError('Only ' + str(len(pages)) + ' usable page(s) could be read. The site may block crawlers or require JavaScript. Try CSV mode with exported page content.')
    return pages, warnings


def tokens(value: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", value.lower()) if len(t) >= 3 and t not in STOPWORDS and not t.isdigit()]


def clean_heading(raw: str) -> str:
    raw = re.split(r'\s*[|]\s*|\s+[-]\s+|\s+[\u2013\u2014]\s+', str(raw or ''))[0]
    return clean_space(raw)


def is_source_eligible(page: Page) -> bool:
    path = urlsplit(page.url).path.lower()
    return not page.noindex and not any(segment in path for segment in SKIP_SOURCE_PATHS)


def content_review(page: Page) -> str:
    words = len(page.content.split())
    if words < 55:
        return 'Short extracted copy - verify complete article'
    if words < 130:
        return 'Limited extracted copy - manual review'
    return 'Substantial extracted copy'


def is_target(page: Page) -> bool:
    path = urlsplit(page.url).path.lower()
    if path in ('/', '') or any(p in path for p in SKIP_TARGET_PATHS) or page.noindex:
        return False
    if any(re.search(r'(?:^|-)(?:sample|demo|staging|draft)(?:-|$)', part)
           or part.startswith('test-') for part in path.strip('/').split('/')):
        return False
    if page.canonical:
        try:
            if normalize_url(page.canonical) != page.url:
                return False
        except AuditInputError:
            return False
    # Live crawl frequently picks up 20-30 word blog-card excerpts. Those aren't
    # sufficient evidence that a destination serves the anchor's reader intent.
    if page.source == 'Crawl' and len(page.content.split()) < 55:
        return False
    return True


def phrases_from_target(page: Page) -> dict[str, float]:
    bases: list[tuple[str, float]] = [(clean_heading(page.h1), 7.0), (clean_heading(page.title), 6.0)]
    slug = unquote(urlsplit(page.url).path.strip('/').split('/')[-1]).replace('-', ' ').replace('_', ' ')
    bases.append((slug, 5.0))
    if page.focus_keywords:
        bases.extend((s.strip(), 7.5) for s in re.split(r'[|,;]+', page.focus_keywords) if s.strip())
    phrases: dict[str, float] = {}
    for base, weight in bases:
        words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", base)
        for size in range(2, min(5, len(words)) + 1):
            for ix in range(len(words) - size + 1):
                chunk = words[ix:ix + size]
                norm = ' '.join(chunk).lower()
                distinctive = [w for w in chunk if w.lower() not in STOPWORDS and len(w) >= 3]
                if len(distinctive) < 2:
                    continue
                if chunk[0].lower() in ('the', 'and', 'your', 'our', 'how', 'what', 'for', 'to', 'with', 'when'):
                    continue
                phrases[norm] = max(phrases.get(norm, 0), weight + min(size, 4) * 0.25)
    return phrases


def similarity(source: Page, target: Page) -> float:
    stokens = set(tokens(source.title + ' ' + source.h1 + ' ' + source.content[:5000]))
    ttokens = set(tokens(target.title + ' ' + target.h1 + ' ' + target.content[:5000]))
    if not stokens or not ttokens:
        return 0
    return len(stokens & ttokens) / math.sqrt(len(stokens) * len(ttokens))


def find_matches(text_node: str, context: str, candidates: dict[str, float]):
    matches = []
    for phrase, weight in candidates.items():
        match = re.search(r'(?<!\w)' + re.escape(phrase).replace(r'\ ', r'\s+') + r'(?!\w)', text_node, flags=re.I)
        if match:
            existing_case_anchor = text_node[match.start():match.end()]
            matches.append((existing_case_anchor, weight, context))
    return matches


def target_is_hub(target: Page) -> bool:
    path = urlsplit(target.url).path.lower().strip('/')
    parts = path.split('/')
    if any(p in ('service', 'services', 'services_list', 'practice-area', 'practice-areas') for p in parts):
        return True
    core = {'personal-injury', 'car-accident', 'car-accidents', 'truck-accidents',
            'dog-bites', 'dog-bite', 'slip-and-fall', 'estate-planning', 'probate'}
    return bool(path in core or (parts and parts[-1] in core))


def specificity_gap(anchor: str, context: str, target: Page) -> tuple[float, str]:
    """Penalize broad terms linking to unrelated, much narrower articles."""
    anchor_tokens = set(tokens(anchor))
    target_terms = set(tokens(clean_heading(target.h1 or target.title)))
    context_terms = set(tokens(context))
    missing = target_terms - anchor_tokens
    if not missing or target_is_hub(target):
        return 0.0, ''
    overlap = missing & context_terms
    generic = len(anchor_tokens) <= 2 or anchor.lower().strip() in {
        'a car accident', 'slip and fall', 'personal injury', 'accident in california', 'dog bite',
    }
    if generic and not overlap and len(missing) >= 1:
        return 2.6, 'Broad anchor; specific destination topic missing from sentence.'
    if not overlap and len(missing) >= 2:
        return 1.35, 'Check narrow destination topic against surrounding content.'
    return 0.0, ''


def placement_markup(anchor: str, context: str) -> str:
    match = re.search(re.escape(anchor), context, flags=re.I)
    if not match:
        return context[:320]
    start = max(0, match.start()-90)
    end = min(len(context), match.end()+130)
    return ('...' if start else '') + context[start:match.start()] + '[' + context[match.start():match.end()] + ']' + context[match.end():end] + ('...' if end < len(context) else '')


def suggest_links(pages: list[Page], max_per_source: int = 3, min_score: float = 5.5) -> pd.DataFrame:
    max_per_source = max(1, min(MAX_LINKS_PER_SOURCE, int(max_per_source)))
    targets = {p.url: phrases_from_target(p) for p in pages if is_target(p)}
    rows: list[dict] = []
    for source in pages:
        if not is_source_eligible(source) or not source.unlinked_nodes:
            continue
        possible = []
        source_text_lower = source.content.lower()
        for target in pages:
            if target.url == source.url or target.url in source.existing_links or target.url not in targets:
                continue
            phrases = targets[target.url]
            if not any(phrase in source_text_lower for phrase in phrases):
                continue
            similarity_score = similarity(source, target)
            best = None
            for node_text, context in source.unlinked_nodes:
                for anchor, weight, sentence in find_matches(node_text, context, phrases):
                    if len(anchor.strip()) > 75:
                        continue
                    penalty, note = specificity_gap(anchor, sentence, target)
                    score = (weight + 4.5*similarity_score
                             + min(0.5, len(anchor.split()) * 0.1) - penalty)
                    if score < min_score:
                        continue
                    if best is None or score > best[0]:
                        best = (score, anchor, sentence, note)
            if best:
                score, anchor, sentence, note = best
                confidence = 'High' if score >= 9.1 and similarity_score >= 0.24 and not note else 'Medium'
                possible.append({
                    'Source URL': source.url, 'Source Title': source.title,
                    'Existing anchor': anchor, 'Target URL': target.url,
                    'Target Title': target.title, 'Priority': confidence,
                    'Relevance Score': round(score, 2),
                    'Evidence / existing sentence': sentence[:550],
                    'Exact placement': placement_markup(anchor, sentence)[:420],
                    'QA Flags': note or ('Target copy is limited; check the destination live.' if len(target.content.split()) < 130 else ''),
                    'Reason': 'Phrase occurs in an unlinked text node; review context and destination intent.',
                    'Review Status': 'Needs review', 'Owner': '', 'Notes': '',
                })
        # One destination per anchor and one new link per sentence/context. Never
        # place multiple competing destinations on the same repeated phrase.
        possible.sort(key=lambda item: (-item['Relevance Score'], -len(item['Existing anchor']), item['Target URL']))
        used_anchors: set[str] = set()
        used_sentences: set[str] = set()
        count = 0
        for rec in possible:
            anchor_key = rec['Existing anchor'].casefold().strip()
            sentence_key = rec['Evidence / existing sentence'].casefold().strip()
            if anchor_key in used_anchors or sentence_key in used_sentences:
                continue
            used_anchors.add(anchor_key)
            used_sentences.add(sentence_key)
            rows.append(rec)
            count += 1
            if count >= max_per_source:
                break
    rows.sort(key=lambda item: (-item['Relevance Score'], item['Source URL'], item['Target URL']))
    columns = ['Source URL','Source Title','Existing anchor','Target URL','Target Title',
               'Priority','Relevance Score','Evidence / existing sentence','Exact placement',
               'QA Flags','Reason','Review Status','Owner','Notes']
    return pd.DataFrame(rows, columns=columns)


def pages_table(pages: Iterable[Page]) -> pd.DataFrame:
    seq = list(pages)
    indexed = {page.url for page in seq}
    coverage = all(page.outlinks_known for page in seq)
    inbound: Counter = Counter()
    for page in seq:
        if page.outlinks_known:
            for link in page.existing_links:
                if link in indexed and link != page.url:
                    inbound[link] += 1
    data = []
    for page in seq:
        count = inbound[page.url]
        graph_status = ('Unknown: incomplete link inventory' if not coverage
                        else ('No incoming link observed in sample' if count == 0
                              else 'Incoming link observed in sample'))
        data.append({
            'URL': page.url, 'Title': page.title, 'H1': page.h1,
            'Words in extracted copy': len(page.content.split()),
            'Content review': content_review(page),
            'Existing outgoing links': len(page.existing_links),
            'Incoming links in audited pages': count if coverage else None,
            'Link graph status': graph_status,
            'Eligible unlinked text nodes': len(page.unlinked_nodes),
            'Noindex': 'Yes' if page.noindex else 'No',
            'Canonical': page.canonical,
            'Source': page.source,
        })
    return pd.DataFrame(data)
