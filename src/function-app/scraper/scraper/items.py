import scrapy

class PageItem(scrapy.Item):
    metadata = scrapy.Field()
    content = scrapy.Field()
