# OpenAI Status Tracker

A production-ready Python script that watches the OpenAI status page and prints incident alerts to the console the moment they happen.
There are no explicit dashboards to monitor. No manual intervention is required to identify the incident. 

---

## Understanding the Problem

The task was built to:
- Detects new incidents, outages, or degradations on OpenAI's status page
- Prints the affected product and the latest status message
- Works automatically without any manual intervention
- Scales cleanly to 100+ status pages without rewriting anything

### What "Event-Driven" Actually Means Here

The problem specifically asks for an event-based approach. Most status pages, including OpenAI's, don't offer public WebSocket or push notification endpoints for external consumers.
The practical answer is a combination of two things:

**Atom Feed** — OpenAI publishes a `/history.atom` feed. Atom is a syndication format where every incident update is a discrete entry with a stable, unique ID. Instead of asking "what's the current state?", we ask "what new events have appeared since I last checked?" That's the semantic difference between polling a snapshot and consuming an event stream.

**asyncio coroutines** — Rather than spinning up one OS thread per status page (expensive, hard to manage, doesn't scale), we run one lightweight coroutine per page inside a single event loop. When one coroutine is waiting on a network response, the event loop runs the others. 100 pages means 100 coroutines — not 100 threads.

Together, these two choices give us the event-driven, horizontally scalable design that is required explicitly to solve this problem.

## Project Structure

openai-status-tracker/
├── tracker.py <- main application
├── requirements.txt <- dependencies
├── .env <- local config
└── README.md


## Why Each Decision Was Made The Way It Was Made (IYKYK, kinda. But there's an explanation below, just in case XD)
### Atom Feed over the JSON API

OpenAI's `/api/v2/summary.json` only includes an `incidents` array when there are currently active incidents. When everything is healthy, the key is simply missing from the response. That makes it unreliable as a trigger.

The `/history.atom` feed always contains a full history of entries. Each update has a stable, unique ID that makes deduplication natural and reliable. It's designed for exactly this kind of machine consumption.

### Non-Blocking Feed Fetching

`feedparser.parse()` is a synchronous, blocking HTTP call. Calling it directly inside an async function blocks the entire event loop while it waits for a response, which defeats the whole point of using asyncio. We wrap it with `loop.run_in_executor()` to offload it to a thread pool, keeping the event loop free to serve other coroutines while the network call is in flight.

### Per-Coroutine State Instead of a Global Flag

The original code used a global `initialized` flag shared across all coroutines. The problem is that the first coroutine to finish its startup run sets `initialized = True`, which can cause other coroutines to start firing alerts before they've finished recording their own existing entries. This produces false positives on startup.

The fix is simple: move `is_initialized` and `seen_entries` inside each `watch_feed()` coroutine so every feed manages its own state independently.

### Deduplication with a Set

We use a Python `set` for O(1) average-case lookup. On the very first poll (`is_initialized = False`), All existing feed entries are recorded silently without triggering output. This seeds the deduplication set so we don't replay old incidents on startup. From the second poll onward, only entries with new IDs are treated as events.

### Retry Logic with Backoff

Network calls fail sometimes. Without handling this, a single failed request silently drops an entire poll cycle with no indication that anything went wrong. We retry up to `MAX_RETRIES` times with a growing wait between attempts. If all retries fail, the error is logged and the cycle is skipped gracefully.
The watcher keeps going on the next interval.

### The `bozo` Flag Check

`feedparser` sets `feed.bozo = True` when the XML it receives is malformed or unparseable. Without checking this, you'd silently process garbage data and wonder why nothing makes sense. We log the `bozo_exception` and skip the cycle when this happens.

## Setup

### Prerequisites

- Python 3.8 or higher
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/openai-status-tracker.git
cd openai-status-tracker

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```
### Running the script

```bash
python tracker.py
```

## What To Expect

When you start the tracker with no active incidents:
```
2026-02-20 21:00:00 [INFO] Starting OpenAI status tracker | Interval: 30s | Feeds: 1
============================================================
2026-02-20 21:00:00 [INFO] Subscribed to https://status.openai.com/history.atom
```

When a new incident is detected:
```
[2026-02-20 21:05:12] Product: OpenAI API - Chat Completions
Status: We are investigating elevated error rates affecting Chat Completions.
------------------------------------------------------------
```

When the incident is resolved:
```
[2026-02-20 21:45:00] Product: OpenAI API - Chat Completions
Status: This incident has been resolved.
------------------------------------------------------------
```

