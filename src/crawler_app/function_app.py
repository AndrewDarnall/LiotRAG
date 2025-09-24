import azure.functions as func
import logging
from pathlib import Path
import os
import subprocess
import sys

app = func.FunctionApp()

@app.timer_trigger(
    schedule="0 0 1 * * *",  # every day at 01:00 UTC
    arg_name="myTimer",
    run_on_startup=True,
    use_monitor=True,
)
def scrapePapardo(myTimer: func.TimerRequest) -> None:

    env = os.environ.copy()
    env["SCRAPY_SETTINGS_MODULE"] = "scraper.settings"

    # Mirror Functions' default setting
    # Only set connection string from AzureWebJobsStorage if explicit account URL isn't provided
    if "AZURE_STORAGE_ACCOUNT_URL" not in env:
        if "AZURE_STORAGE_CONNECTION_STRING" not in env and "AzureWebJobsStorage" in env:
            env["AZURE_STORAGE_CONNECTION_STRING"] = env["AzureWebJobsStorage"]

    # Where to store blobs: container
    env.setdefault("AZURE_BLOB_CONTAINER", "ao-papardo-kb")
    # Allow passing through explicit account URL for Managed Identity
    if "AZURE_STORAGE_ACCOUNT_URL" in os.environ:
        env["AZURE_STORAGE_ACCOUNT_URL"] = os.environ["AZURE_STORAGE_ACCOUNT_URL"]

    # Run the spider via 'python -m scrapy'
    # This works best as subprocess to avoid Twisted reactor issues
    crawler_dir = Path(__file__).resolve().parent / "scraper"
    cmd = [sys.executable, "-m", "scrapy", "crawl", "dmi_full", "-s", "LOG_LEVEL=INFO"]
    logging.info(f"Starting crawl: {cmd}")
    try:
        subprocess.run(cmd, cwd=crawler_dir, check=True, env=env)
        logging.info("Crawl finished successfully.")
    except subprocess.CalledProcessError as e:
        logging.exception("Crawl failed with non-zero exit code")
        raise