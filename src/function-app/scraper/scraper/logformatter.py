import logging
from scrapy.logformatter import LogFormatter

class MinimalLogFormatter(LogFormatter):
    """
    Suppress verbose item and drop logs unless DEBUG is enabled.
    """
    def item_scraped(self, item, response, spider):
        return {
            "level": logging.DEBUG,
            "msg": "Item scraped",
            "args": {}
        }

    def dropped(self, item, exception, response, spider):
        # Keep reason (DropItem message) but hide item contents
        return {
            "level": logging.DEBUG,
            "msg": f"Dropped: {exception}",
            "args": {}
        }