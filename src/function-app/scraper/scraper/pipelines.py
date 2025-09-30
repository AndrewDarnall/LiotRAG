import json
from json.tool import main
from pathlib import Path
import re
from markdownify import markdownify as md
from bs4 import BeautifulSoup
from scrapy.exceptions import DropItem
from typing import Optional
import hashlib
import os

try:
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
except Exception:
    BlobServiceClient = None
    ContentSettings = None
    DefaultAzureCredential = None


class StreamingPipeline:
    """
    Write each scraped page immediately as NDJSON (streaming).
    Used for LOCAL TESTING / DEBUGGING.
    """

    def __init__(self, output_path: str | None = None):
        self.output_dir = Path(output_path) if output_path else Path(Path.cwd(), "output")
        self.output_dir.mkdir(exist_ok=True, parents=True)
        self.out_file = self.output_dir / "pages.ndjson"

    @classmethod
    def from_crawler(cls, crawler):
        # could allow override via setting OUTPUT_DIR
        output_path = crawler.settings.get("SCRAPED_OUTPUT_DIR")
        return cls(output_path)

    def process_item(self, item, spider):
        with self.out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(item), ensure_ascii=False) + "\n")
        spider.logger.info(f"Stored: {item.get('metadata')['url']}")
        return item
    
class CleaningPipeline:
    """
    Performs HTML to markdown, hash calculation, deduplication.
    """

    def __init__(self, content_min_length: int = 100):
        self.seen_hashes = set()
        self.content_min_length = content_min_length

    @classmethod
    def from_crawler(cls, crawler):
        content_min_length = int(crawler.settings.get("CONTENT_MIN_LENGTH", 100))
        return cls(content_min_length=content_min_length)

    def convert_html_to_markdown(self, item, logger):

        soup = BeautifulSoup(item['content'], 'html.parser')
        item['metadata']['page_type'] = "unknown"

        main = soup.find('main', id='it-main')
        if main is None:
            logger.warning(f"COULDN'T EXTRACT MAIN TEXT FROM {item['metadata']['url']}")
            return
        
        # Remove images and base64 content
        for img in main.find_all('img'):
            img.decompose()
        for elem in main.find_all(attrs={'src': re.compile(r'^data:')}):
            elem.decompose()
        
        # share_div contains some text to remove (mainly social names)
        if share := main.find('div', id='it-share'):
            share.decompose()
        if sidebar := main.find('aside', id='menu-sezione'):
            sidebar.decompose()
        if breadcrumb := main.find('section', id='breadcrumb'):
            breadcrumb.decompose()

        content = md(str(main)) if main else ""
        
        # Remove ambiguous unicode characters
        content = re.sub(r'[^\x20-\x7E\n\r\t]', '', content)
        
        item['content'] = content
        item['metadata']['page_type'] = "info"


    def calculate_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def process_item(self, item, spider):
        
        self.convert_html_to_markdown(item, spider.logger) # spider used for logging

        # Basic filtering
        if item['metadata'].get("page_type") == "unknown":
            spider.logger.warning(f"Unknown page type for URL: {item['metadata']['url']}")
            raise DropItem(f"Unknown page type")
        if len(item['content']) < self.content_min_length:
            spider.logger.info(f"Content too short ({len(item['content'])} chars), skipping URL: {item['metadata']['url']}")
            raise DropItem(f"Content too short,")
        
        # Filter out old academic year pages from /insegnamenti/
        url = item['metadata'].get('url', '')
        if '/insegnamenti/' in url:
            # Look for academic year pattern that's not 2024/2025
            academic_year_pattern = r'Anno accademico (\d{4}/\d{4})'
            match = re.search(academic_year_pattern, item['content'])
            if match and match.group(1) != '2024/2025':
                spider.logger.info(f"Filtering old academic year content ({match.group(1)}), skipping URL: {url}")
                raise DropItem(f"Old academic year content: {match.group(1)}")
        
        # TEST CODE
        # if item['metadata'].get('title'):
        #     if "personale" in item['metadata']['title'].lower():
        #         spider.logger.info(f"PROCESSING PERSONAL INFO: {item['metadata']['url']}")
        #         item['content'] = f" {item['metadata'].get('title')} PAGINA PERSONALE - CONTENUTO NON DISPONIBILE"
        
        # Calculate hash
        hash_value = self.calculate_hash(item['content'])

        # Deduplicate
        if hash_value in self.seen_hashes:
            spider.logger.info(f"Duplicate content found, skipping URL: {item['metadata']['url']}")
            raise DropItem(f"Duplicate item found")
        
        self.seen_hashes.add(hash_value)
        item['metadata']['content_hash'] = hash_value
        return item
    
class AzureBlobPipeline:
    """
    Upload each scraped item to Azure Blob Storage as {sha1(url)}.md.
    Supports two auth modes:
      1) Connection string (e.g., Azurite/local dev)
      2) Managed Identity/Entra ID via DefaultAzureCredential + account URL

    Env/config (evaluated in this order):
      - AZURE_STORAGE_CONNECTION_STRING (or settings.AZURE_STORAGE_CONNECTION_STRING)
      - AZURE_STORAGE_ACCOUNT_URL (e.g., https://<account>.blob.core.windows.net)
      - AZURE_BLOB_CONTAINER (default: 'pages')
    """
    def __init__(
        self,
        conn_str: Optional[str] = None,
        container: str = "pages",
        account_url: Optional[str] = None,
    ):
        # Resolve settings from args or env
        self.conn_str = conn_str or os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        self.account_url = account_url or os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        self.container_name = container or os.getenv("AZURE_BLOB_CONTAINER", "pages")

        # Determine if SDKs are available
        if BlobServiceClient is None:
            self.enabled = False
            return

        # Initialize client using either connection string or DefaultAzureCredential
        self.service = None
        if self.account_url and DefaultAzureCredential is not None:
            # Managed Identity / Azure CLI / VS Code signed-in via DefaultAzureCredential
            cred = DefaultAzureCredential()
            self.service = BlobServiceClient(account_url=self.account_url, credential=cred)
        elif self.conn_str:
            # Local dev/Azurite path
            self.service = BlobServiceClient.from_connection_string(self.conn_str)
        else:
            # Not enough information to initialize
            self.enabled = False
            return

        self.container = self.service.get_container_client(self.container_name)
        self.enabled = True
        try:
            self.container.create_container()
        except Exception:
            # Already exists or insufficient permission to create; continue if we can write
            pass
    
    @classmethod
    def from_crawler(cls, crawler):
        conn_str = (
            crawler.settings.get("AZURE_STORAGE_CONNECTION_STRING")
            or os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        )
        container = crawler.settings.get(
            "AZURE_BLOB_CONTAINER", os.getenv("AZURE_BLOB_CONTAINER", "pages")
        )
        account_url = (
            crawler.settings.get("AZURE_STORAGE_ACCOUNT_URL")
            or os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        )
        return cls(conn_str=conn_str, container=container, account_url=account_url)
    
    def sanitize(self, val):
        if isinstance(val, str):
        # Azure metadata values must be ASCII and cannot contain certain characters
            # Replace "»" and any non-ASCII chars with "_"
            return re.sub(r'[^\x20-\x7E]', '_', val)
        return str(val)

    def process_item(self, item, spider):
        """Upload item to Azure Blob Storage, if changed."""

        if not self.enabled:
            return item

        content = item.get("content") or ""
        raw_metadata = item.get("metadata") or {}
        metadata = {k: self.sanitize(v) for k, v in raw_metadata.items()}
        url = metadata.get("url")
        if not url:
            spider.logger.warning("Item missing 'url' metadata; skipping.")
            return item

        new_hash = metadata.get("content_hash")
        blob_name = f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.md"
        blob_client = self.container.get_blob_client(blob_name)

        try:
            props = blob_client.get_blob_properties() # raises if not exists
            remote_hash = (props.metadata or {}).get("content_hash")
            if remote_hash == new_hash:
                spider.logger.info(f"Skipping (unchanged): {url}")
                return item

            spider.logger.info(f"Content changed: {url}; updating.")
            blob_client.upload_blob(
                content,
                overwrite=True,
                metadata=metadata,
                content_settings=ContentSettings(content_type="text/markdown; charset=utf-8"),
                #if_match=props.etag,  # optimistic concurrency
            )
        except ResourceNotFoundError:
            spider.logger.info(f"Uploading new blob: {url}")
            blob_client.upload_blob(
                content,
                overwrite=True,
                metadata=metadata,
                content_settings=ContentSettings(content_type="text/markdown; charset=utf-8"),
            )
        except Exception as e:
            spider.logger.error(f"Error uploading blob for {url}: {e}")

        return item


class NoOpPipeline:
    """Simple No-Op pipeline."""
    def process_item(self, item, spider):
        return item