from datetime import datetime
from urllib.parse import urlparse

import scrapy
from scrapy.linkextractors import LinkExtractor
from ..items import PageItem

EXCLUDE_SUBSTRINGS = [
    'admin',
    'avvisi-docente',
    'avvisi',
    'archivio',
    '?eng',
    '/en/',
    '&eng',
    'bandi',
    'courses',
    'notizie',
    'faculty',
    'seuid',
    'uid',
    'calendario',
    'francesco.russo',
    'vittorio.romano'
]

class DMISpider(scrapy.Spider):

    name = "dmi_full"
    allowed_domains = ["web.dmi.unict.it", "dmi.unict.it"]
    start_urls = ["https://web.dmi.unict.it/"]

    custom_settings = {
        # ignore non-html via downloader middleware settings
        'HTTPERROR_ALLOWED_CODES': [301,302,307,308],
        # stop after first 200 pages
        'CLOSESPIDER_PAGECOUNT': 200,
    }

    # deny common non-html extensions + anything Scrapy already ignores
    DENY_EXTENSIONS = set([
        # archives
        '7z', '7zip', 'bz2', 'rar', 'tar', 'tar.gz', 'xz', 'zip',
        # images
        'mng', 'pct', 'bmp', 'gif', 'jpg', 'jpeg', 'png', 'pst', 'psp', 'tif', 'tiff', 'ai', 'drw', 'dxf', 'eps', 'ps', 'svg', 'cdr', 'ico',
        # audio
        'mp3', 'wma', 'ogg', 'wav', 'ra', 'aac', 'mid', 'au', 'aiff',
        # video
        '3gp', 'asf', 'asx', 'avi', 'mov', 'mp4', 'mpg', 'qt', 'rm', 'swf', 'wmv', 'm4a', 'm4v', 'flv', 'webm',
        # office suites
        'xls', 'xlsx', 'ppt', 'pptx', 'pps', 'doc', 'docx', 'odt', 'ods', 'odg', 'odp',
        # other
        'css', 'pdf', 'exe', 'bin', 'rss', 'dmg', 'iso', 'apk',
    ])

    link_extractor = LinkExtractor(allow_domains=allowed_domains)

    def is_excluded(self, url: str) -> bool:
        return any(part in url for part in EXCLUDE_SUBSTRINGS)

    def is_html(self, response: scrapy.http.Response) -> bool:
        ctype = response.headers.get(b'Content-Type', b'').decode('latin-1').lower()
        if 'text/html' in ctype or 'application/xhtml+xml' in ctype:
            return True
        return False

    def should_follow(self, url: str) -> bool:
        if self.is_excluded(url):
            return False
        if any(url.lower().endswith('.' + ext) for ext in self.DENY_EXTENSIONS):
            return False
        parsed = urlparse(url)
        if parsed.netloc and parsed.netloc not in self.allowed_domains:
            return False
        return True

    def parse(self, response: scrapy.http.Response):

        if not self.is_html(response):
            return

        title = response.xpath('//title/text()').get(default='').strip()
        item = PageItem(
            metadata = {
                'url': response.url,
                'timestamp': datetime.now().isoformat(),
                'title': title,
            },
            content=response.text, #Page HTML
        )
        # Yield item if not excluded
        if not self.is_excluded(response.url):
            self.logger.info(f"Processing: {response.url}")
            yield item

        # extract links and follow
        for link in self.link_extractor.extract_links(response):
            url = link.url.split('#')[0]
            if self.should_follow(url):
                yield scrapy.Request(url, callback=self.parse)
