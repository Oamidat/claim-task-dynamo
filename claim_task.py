#!/usr/bin/env python3
"""
INC-0420-D / Emergency Exploit Validation – Self‑debugging swarm.
Discovery mode and robust ID extraction.
Prints live claim confirmations and generates qc_report.json.
"""

import asyncio
import aiohttp
import csv
import json
import os
import re
import time
import sys
import signal
import math
from typing import Optional, List, Dict, Any, Tuple

# ----------------------------------------------------------------------
# Request configuration
# ----------------------------------------------------------------------
def load_credentials() -> Dict[str, Any]:
    """Load optional local credentials without requiring another dependency."""
    path = os.environ.get("HANDSHAKE_CREDENTIALS_FILE", "credentials.json")
    if not os.path.isfile(path):
        return {}

    try:
        with open(path, encoding="utf-8") as credentials_file:
            credentials = json.load(credentials_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load {path}: {exc}") from exc

    if not isinstance(credentials, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return credentials


CREDENTIALS = load_credentials()
CLAIM_URL = "https://ai.joinhandshake.com/api/trpc/task.claimNextTask?batch=1"
ANNOTATION_PROJECT_ID = os.environ.get(
    "HANDSHAKE_ANNOTATION_PROJECT_ID",
    CREDENTIALS.get(
        "annotationProjectId",
        "a1d39753-ae51-41df-8c86-2b7e73c6bd6b"
    )
)
CLAIM_PAYLOAD = {
    "0": {"json": {"annotationProjectId": ANNOTATION_PROJECT_ID}}
}
COOKIE = os.environ.get("HANDSHAKE_COOKIE", "")
if not COOKIE:
    cookies = CREDENTIALS.get("cookies", {})
    if not isinstance(cookies, dict):
        raise ValueError("credentials.json 'cookies' must be a JSON object.")
    COOKIE = "; ".join(f"{name}={value}" for name, value in cookies.items())
BASE_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://ai.joinhandshake.com",
    "Priority": "u=1, i",
    "Referer": "https://ai.joinhandshake.com/fellow/projects",
    "Sec-CH-UA": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
}
if COOKIE:
    BASE_HEADERS["Cookie"] = COOKIE

for env_name, config_name, header_name in (
    ("HANDSHAKE_BAGGAGE", "baggage", "Baggage"),
    ("HANDSHAKE_TRACEPARENT", "traceparent", "Traceparent"),
    ("HANDSHAKE_TRACESTATE", "tracestate", "Tracestate"),
):
    if value := os.environ.get(env_name) or CREDENTIALS.get(config_name):
        BASE_HEADERS[header_name] = value

NUM_WORKERS = 80
DURATION_SECONDS = 90
QUEUE_EMPTY_RETRY_SECONDS = 60
CSV_FILENAME = "debug_swarm.csv"
REPORT_FILENAME = "qc_report.json"
DEBUG_RESPONSE_FILE = "debug_first_response.txt"

# ----------------------------------------------------------------------
# Global state (protected by locks)
# ----------------------------------------------------------------------
stop_event = asyncio.Event()
counter_lock = asyncio.Lock()

total_claims_attempted = 0
total_claims_successful = 0
claimed_task_ids: List[str] = []
invite_confirmations = 0   # number of times second claim returned 200 or 409 (proof)
claim_latencies: List[int] = []  # latency from first claim


# ----------------------------------------------------------------------
# Utility functions
# ----------------------------------------------------------------------
def find_id_recursive(obj) -> Optional[str]:
    """Recursively search for 'id' or 'taskId' in a JSON object."""
    if isinstance(obj, dict):
        # Check for known keys at this level
        for key in ['id', 'taskId', 'task_id']:
            if key in obj and isinstance(obj[key], str):
                # Try to validate it's a UUID or at least not too short
                val = obj[key]
                if re.match(r'[a-f0-9\-]+', val):
                    return val
        # Recurse into values
        for value in obj.values():
            result = find_id_recursive(value)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_id_recursive(item)
            if result:
                return result
    return None

def extract_id_from_text(text: str) -> Optional[str]:
    """Fallback: use regex to find a UUID-like ID."""
    # Match UUID pattern or alphanumeric with hyphens
    # Try typical UUID pattern: 8-4-4-4-12 hex
    match = re.search(r'"[Ii]d"\s*:\s*"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})"', text)
    if match:
        return match.group(1)
    # fallback: any "id":"..." with alnum and hyphens
    match = re.search(r'"[Ii]d"\s*:\s*"([a-f0-9\-]+)"', text)
    if match:
        return match.group(1)
    return None

async def discovery(session: aiohttp.ClientSession) -> Tuple[Dict, str, str]:
    """
    Perform a single test claim to discover the task ID path.
    Returns: (body_format, extraction_method, raw_response_text)
    where body_format is the batched tRPC claim payload
    and extraction_method is 'json' or 'regex'.
    """
    formats = [
        {"name": "trpc", "payload": CLAIM_PAYLOAD}
    ]
    for fmt in formats:
        try:
            async with session.post(CLAIM_URL, json=fmt["payload"]) as resp:
                raw = await resp.text()
                # Write debug
                with open(DEBUG_RESPONSE_FILE, "w") as f:
                    f.write(raw)
                print(f"\n[DEBUG] Response status: {resp.status}")
                print(f"[DEBUG] Raw response body (first 500 chars):\n{raw[:500]}...")

                if resp.status == 200:
                    # Try to parse JSON
                    try:
                        data = json.loads(raw)
                        task_id = find_id_recursive(data)
                        if task_id:
                            print(f"[DISCOVERY] ✅ Found task ID: {task_id} using JSON search")
                            return fmt["payload"], "json", raw
                        else:
                            # Fallback to regex
                            task_id = extract_id_from_text(raw)
                            if task_id:
                                print(f"[DISCOVERY] ✅ Found task ID: {task_id} using regex fallback")
                                return fmt["payload"], "regex", raw
                            else:
                                print(f"[DISCOVERY] ⚠️ 200 OK but no ID found in response. Treating as success.")
                                # We still treat it as a success and use regex extraction later.
                                return fmt["payload"], "regex", raw
                    except json.JSONDecodeError:
                        # Not JSON, try regex
                        task_id = extract_id_from_text(raw)
                        if task_id:
                            print(f"[DISCOVERY] ✅ Found task ID: {task_id} using regex on non-JSON")
                            return fmt["payload"], "regex", raw
                        else:
                            print(f"[DISCOVERY] ⚠️ 200 OK but response not JSON and no ID found.")
                            return fmt["payload"], "regex", raw
                elif resp.status in (400, 415):
                    print(f"[DISCOVERY] Format '{fmt['name']}' returned {resp.status}, trying next...")
                    continue
                else:
                    print(f"[DISCOVERY] Format '{fmt['name']}' returned {resp.status}, trying next...")
                    continue
        except Exception as e:
            print(f"[DISCOVERY] Error with format '{fmt['name']}': {e}")
            continue

    print("[DISCOVERY] Claim request failed. Retaining the configured tRPC payload.")
    return CLAIM_PAYLOAD, "regex", ""

# ----------------------------------------------------------------------
# Worker and swarm logic
# ----------------------------------------------------------------------
async def worker(
    worker_id: int,
    session: aiohttp.ClientSession,
    body_format: Dict,
    extraction_method: str,
    log_queue: asyncio.Queue
) -> None:
    """Single worker: claim → second claim (proof), with retry on queue empty."""
    global total_claims_attempted, total_claims_successful, claimed_task_ids, invite_confirmations, claim_latencies

    while not stop_event.is_set():
        # ---------- First claim ----------
        claim_status = None
        claimed_task_id = None
        x_request_id = ""
        latency_ms = ""
        error_msg = ""

        try:
            async with session.post(CLAIM_URL, json=body_format) as resp:
                claim_status = resp.status
                x_request_id = resp.headers.get("x-request-id", "")
                latency_ms = resp.headers.get("x-envoy-upstream-service-time", "")
                raw = await resp.text()

                if claim_status == 200:
                    # Extract ID
                    if extraction_method == "json":
                        try:
                            data = json.loads(raw)
                            claimed_task_id = find_id_recursive(data)
                        except:
                            claimed_task_id = extract_id_from_text(raw)
                    else:
                        claimed_task_id = extract_id_from_text(raw)

                    if not claimed_task_id:
                        claimed_task_id = "UNKNOWN"

                    # Store latency
                    if latency_ms:
                        try:
                            claim_latencies.append(int(latency_ms))
                        except ValueError:
                            pass

                    # Print real-time claim
                    print(f"[CLAIMED] Task ID: {claimed_task_id} (worker {worker_id})")

                    # Update counters
                    async with counter_lock:
                        total_claims_attempted += 1
                        total_claims_successful += 1
                        claimed_task_ids.append(claimed_task_id)

                    # ---------- Second claim (proof) ----------
                    # Immediately try to claim again to prove the first consumed a task
                    try:
                        async with session.post(CLAIM_URL, json=body_format) as resp2:
                            invite_status = resp2.status
                            # If 409 or 200, it's a proof (409 means no tasks left, 200 means another task claimed)
                            if invite_status in (200, 409):
                                async with counter_lock:
                                    invite_confirmations += 1
                            # Log the second status
                            log_entry = {
                                "timestamp": time.time(),
                                "worker": worker_id,
                                "claim_status": claim_status,
                                "claimed_task_id": claimed_task_id or "UNKNOWN",
                                "invite_status": invite_status,
                                "x_request_id": x_request_id,
                                "latency_ms": latency_ms
                            }
                            await log_queue.put(log_entry)
                    except Exception as e2:
                        # Log error but continue
                        log_entry = {
                            "timestamp": time.time(),
                            "worker": worker_id,
                            "claim_status": claim_status,
                            "claimed_task_id": claimed_task_id or "UNKNOWN",
                            "invite_status": f"error: {e2}",
                            "x_request_id": x_request_id,
                            "latency_ms": latency_ms
                        }
                        await log_queue.put(log_entry)

                elif claim_status in (404, 410):
                    # Queue empty detection
                    print(f"[QUEUE EMPTY] Worker {worker_id} got {claim_status}, retrying...")
                    # Wait a bit and retry (but only if we haven't exceeded QUEUE_EMPTY_RETRY_SECONDS)
                    # The worker will just loop; we implement a simple retry delay
                    await asyncio.sleep(1)
                    continue
                else:
                    # Other errors, log and continue
                    log_entry = {
                        "timestamp": time.time(),
                        "worker": worker_id,
                        "claim_status": claim_status,
                        "claimed_task_id": "",
                        "invite_status": "skipped",
                        "x_request_id": x_request_id,
                        "latency_ms": latency_ms
                    }
                    await log_queue.put(log_entry)
                    async with counter_lock:
                        total_claims_attempted += 1

        except asyncio.TimeoutError:
            error_msg = "TimeoutError"
            print(f"[ERROR] Worker {worker_id} timeout on claim")
            async with counter_lock:
                total_claims_attempted += 1
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            print(f"[ERROR] Worker {worker_id} claim exception: {error_msg}")
            async with counter_lock:
                total_claims_attempted += 1

        # Small sleep to avoid tight CPU loop
        await asyncio.sleep(0.01)


async def log_writer(log_queue: asyncio.Queue) -> None:
    """Write logs to CSV."""
    with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "worker",
            "claim_status",
            "claimed_task_id",
            "invite_status",
            "x_request_id",
            "latency_ms"
        ])
        while True:
            entry = await log_queue.get()
            if entry is None:
                break
            writer.writerow([
                entry.get("timestamp", ""),
                entry.get("worker", ""),
                entry.get("claim_status", ""),
                entry.get("claimed_task_id", ""),
                entry.get("invite_status", ""),
                entry.get("x_request_id", ""),
                entry.get("latency_ms", "")
            ])
            log_queue.task_done()


async def timer_task() -> None:
    """Stop after DURATION_SECONDS."""
    await asyncio.sleep(DURATION_SECONDS)
    stop_event.set()


async def main() -> None:
    """Run discovery then swarm."""
    global total_claims_attempted, total_claims_successful, claimed_task_ids, invite_confirmations, claim_latencies

    if not COOKIE:
        print(
            "Authentication cookies are required in credentials.json or "
            "HANDSHAKE_COOKIE.",
            file=sys.stderr
        )
        sys.exit(2)

    loop = asyncio.get_running_loop()

    # Ctrl+C handler
    def signal_handler():
        loop.call_soon_threadsafe(stop_event.set)
        print("\nSIGINT received, stopping gracefully...")
    signal.signal(signal.SIGINT, lambda s, f: signal_handler())

    # Discovery phase
    print("=" * 60)
    print("DISCOVERY PHASE")
    print("Sending a test claim to determine the extraction method...")
    async with aiohttp.ClientSession(headers=BASE_HEADERS) as session:
        body_format, extraction_method, raw = await discovery(session)
        print(f"[DISCOVERY] Using body format: {body_format}")
        print(f"[DISCOVERY] Using extraction method: {extraction_method}")
        if raw:
            print(f"[DISCOVERY] Raw response saved to {DEBUG_RESPONSE_FILE}")
        print("=" * 60)
        print("Starting swarm...")

    # Shared log queue
    log_queue = asyncio.Queue(maxsize=5000)
    writer = asyncio.create_task(log_writer(log_queue))
    timer = asyncio.create_task(timer_task())

    # Launch workers with the discovered settings
    async with aiohttp.ClientSession(headers=BASE_HEADERS) as session:
        workers = [
            asyncio.create_task(worker(i, session, body_format, extraction_method, log_queue))
            for i in range(NUM_WORKERS)
        ]

        # Wait for completion (timer or stop)
        await asyncio.gather(*workers, return_exceptions=True)

        # Stop timer
        stop_event.set()
        timer.cancel()
        try:
            await timer
        except asyncio.CancelledError:
            pass

        # Flush logs
        await log_queue.put(None)
        await writer

    # Prepare report
    async with counter_lock:
        total_attempted = total_claims_attempted
        total_success = total_claims_successful
        task_ids = claimed_task_ids.copy()
        invites = invite_confirmations
        latencies = claim_latencies.copy()

    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    sorted_lats = sorted(latencies)
    p95_lat = sorted_lats[int(math.ceil(0.95 * len(sorted_lats))) - 1] if sorted_lats else 0.0

    report = {
        "total_claims_attempted": total_attempted,
        "total_claims_successful": total_success,
        "claimed_task_ids": task_ids[:100],  # limit for size
        "invite_confirmations": invites,
        "avg_latency_ms": avg_lat,
        "p95_latency_ms": p95_lat
    }

    with open(REPORT_FILENAME, "w") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 60)
    print("SWARM COMPLETE")
    print(f"Total claims attempted: {total_attempted}")
    print(f"Total claims successful (200): {total_success}")
    print(f"Unique claimed task IDs logged: {len(task_ids)}")
    print(f"Invite confirmations (second claim 200/409): {invites}")
    print(f"Average latency (ms): {avg_lat:.2f}")
    print(f"95th percentile latency (ms): {p95_lat:.2f}")
    print(f"Report saved to {REPORT_FILENAME}")
    print(f"CSV log saved to {CSV_FILENAME}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        stop_event.set()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
