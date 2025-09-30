BOT_NAME = "scraper"

SPIDER_MODULES = ["scraper.spiders"]
NEWSPIDER_MODULE = "scraper.spiders"

ROBOTSTXT_OBEY = True

CONTENT_MIN_LENGTH = 200
CONCURRENT_REQUESTS = 4
DOWNLOAD_DELAY = 0

COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en",
    "User-Agent": "Mozilla/5.0 (compatible; ScrapyBot/1.0; +https://www.web.dmi.unict.it)"
}

ITEM_PIPELINES = {
    "scraper.pipelines.CleaningPipeline": 100,
    #"scraper.pipelines.StreamingPipeline": 200,
    "scraper.pipelines.AzureBlobPipeline": 300,
}

LOG_LEVEL = "INFO"
LOG_FORMATTER = "scraper.logformatter.MinimalLogFormatter"
LOG_SHORT_NAMES = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(name)s %(levelname)s: %(message)s"}
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        }
    },
    "loggers": {
        "scrapy": {"level": "INFO"},
        "scrapy.core.scraper": {"level": "INFO"},
        "scrapy.core.engine": {"level": "INFO"},
        "scrapy.middleware": {"level": "ERROR"},
        "scrapy.extensions": {"level": "WARNING"},
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

# Suppress overly verbose Azure SDK logs
import logging
for _n in (
    "azure",
    "azure.storage",
    "azure.core",
    "azure.core.pipeline.policies.http_logging_policy",
):
    logging.getLogger(_n).setLevel(logging.WARNING)

FEED_EXPORT_ENCODING = "utf-8"
