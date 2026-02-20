import asyncio
import feedparser
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

## basic config
def _parse_interval(raw: str) -> int:
    try:
        val = int(raw)
        if val < 10:
            logger.warning("POLL_INTERVAL too low (%s), defaulting to 30s", val)
            return 30
        return val
    except (ValueError, TypeError):
        logger.warning("Invalid POLL_INTERVAL '%s', defaulting to 30s", raw)
        return 30

CHECK_INTERVAL: int = _parse_interval(os.getenv("POLL_INTERVAL", "30"))

FEEDS: List[str] = [
    url.strip().rstrip("/") + "/history.atom"
    for url in os.getenv("STATUS_PAGES", "https://status.openai.com").split(",")
    if url.strip()
]

IST = timezone(timedelta(hours=5, minutes=30))

## retry config
MAX_RETRIES = 3
RETRY_BACKOFF = 5  # seconds between retries



def clean_html(text: str) -> str:
    return re.sub(r"<[^<]+?>", "", text).strip()


## extracting component names
def extract_components(html: str) -> List[str]:
    raw = re.findall(r"<li>(.*?)</li>", html, re.DOTALL)
    return [re.sub(r"<[^>]+>", "", item).strip() for item in raw if item.strip()]

## converting timezones for better readability
def format_time(struct_time) -> str:
    if not struct_time:
        return "Unknown time"
    dt = datetime(*struct_time[:6], tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def extract_status_line(clean_summary: str) -> str:
   
    lines = [line.strip() for line in clean_summary.split("\n") if line.strip()]
    return lines[-1] if lines else clean_summary.strip()

## formatting and printing status in desired format - as given in the problem statement
def print_event(product: str, status: str, timestamp: str) -> None:
    print(f"\n[{timestamp}] Product: {product}")
    print(f"Status: {status}")
    print("-" * 60)

async def fetch_feed(feed_url: str):
    loop = asyncio.get_event_loop()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            feed = await loop.run_in_executor(None, feedparser.parse, feed_url)

            # bozo flag: feedparser marks malformed/unparseable feeds
            if feed.bozo:
                logger.warning(
                    "Malformed feed at %s (bozo: %s)", feed_url,
                    getattr(feed, "bozo_exception", "unknown error")
                )
                return None

            return feed

        except Exception as e:
            logger.error("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, feed_url, e)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF * attempt)  # exponential-ish backoff

    logger.error("All retries exhausted for %s — skipping this cycle", feed_url)
    return None


## Main logic to identify incidents
async def watch_feed(feed_url: str) -> None:
   ## each feed runs independently
    seen_entries: set = set()
    is_initialized: bool = False

    logger.info("Subscribed to %s", feed_url)

    while True:
        feed = await fetch_feed(feed_url)

        if feed is None:
            ## fetch failed after retries
            await asyncio.sleep(CHECK_INTERVAL)
            continue

        for entry in feed.entries:
            ## skip entries with no ID to avoid None in seen_entries
            eid: Optional[str] = entry.get("id")
            if not eid:
                logger.debug("Skipping entry with no ID in %s", feed_url)
                continue

            summary: str = entry.get("summary", "")
            clean_summary: str = clean_html(summary)

            if not is_initialized:
                seen_entries.add(eid)
                continue

            if eid in seen_entries:
                continue

            seen_entries.add(eid)

            ## strip inner tags from component names
            components = extract_components(summary)
            product = "OpenAI API - " + ", ".join(components) if components else "OpenAI Service"

            timestamp = format_time(entry.get("updated_parsed") or entry.get("published_parsed"))

            status_line = extract_status_line(clean_summary)

            print_event(product, status_line, timestamp)

        is_initialized = True
        await asyncio.sleep(CHECK_INTERVAL)


## entry point
async def main() -> None:
    logger.info("Starting OpenAI status tracker | Interval: %ds | Feeds: %d", CHECK_INTERVAL, len(FEEDS))
    print("=" * 60)

    results = await asyncio.gather(
        *[watch_feed(feed) for feed in FEEDS],
        return_exceptions=True
    )

    for feed_url, result in zip(FEEDS, results):
        if isinstance(result, Exception):
            logger.error("Watcher for %s crashed: %s", feed_url, result)


if __name__ == "__main__":
    asyncio.run(main())
