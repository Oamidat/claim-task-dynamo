#!/usr/bin/env python3
"""
Handshake Task Async Sniper - pure API, no browser.
Uses aiohttp and asyncio for concurrent task claiming.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import aiohttp

try:
    from plyer import notification
except ImportError:
    notification = None

try:
    import playsound
except ImportError:
    playsound = None


def load_credentials() -> Dict[str, Any]:
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


# ---------- CONFIGURATION ----------
CREDENTIALS = load_credentials()
ANNOTATION_PROJECT_ID = os.environ.get(
    "HANDSHAKE_ANNOTATION_PROJECT_ID",
    CREDENTIALS.get(
        "annotationProjectId",
        "a1d39753-ae51-41df-8c86-2b7e73c6bd6b",
    ),
)
BASE_URL = "https://ai.joinhandshake.com/api/trpc"

COOKIE = os.environ.get("HANDSHAKE_COOKIE")
if not COOKIE:
    cookies = CREDENTIALS.get("cookies", {})
    if not isinstance(cookies, dict):
        raise ValueError("credentials.json 'cookies' must be a JSON object.")
    COOKIE = "; ".join(f"{name}={value}" for name, value in cookies.items())

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://ai.joinhandshake.com",
    "Priority": "u=1, i",
    "Referer": "https://ai.joinhandshake.com/fellow/projects",
    "Sec-CH-UA": (
        '"Chromium";v="148", "Google Chrome";v="148", '
        '"Not/A)Brand";v="99"'
    ),
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
}
if COOKIE:
    HEADERS["Cookie"] = COOKIE

for env_name, config_name, header_name in (
    ("HANDSHAKE_BAGGAGE", "baggage", "Baggage"),
    ("HANDSHAKE_TRACEPARENT", "traceparent", "Traceparent"),
    ("HANDSHAKE_TRACESTATE", "tracestate", "Tracestate"),
):
    value = os.environ.get(env_name) or CREDENTIALS.get(config_name)
    if value:
        HEADERS[header_name] = str(value)

POLL_INTERVAL = 1
CLAIM_CONCURRENCY = 10
INITIAL_BACKOFF = 60
MAX_BACKOFF = 300
backoff = INITIAL_BACKOFF

GET_TASKS_URL = f"{BASE_URL}/task.getAllClaimableTasksForFellow"
CLAIM_NEXT_URL = f"{BASE_URL}/task.claimNextTask?batch=1"
GET_MY_TASKS_URL = f"{BASE_URL}/task.listClaimedTasksForFellow"
CLAIM_NEXT_PAYLOAD = {
    "0": {
        "json": {
            "annotationProjectId": ANNOTATION_PROJECT_ID,
        }
    }
}

log_format = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler("sniper.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Sniper")


def notify(title: str, message: str):
    if notification:
        try:
            notification.notify(title=title, message=message, timeout=5)
        except Exception as exc:
            logger.debug("Notification failed: %s", exc)


def play_sound():
    if playsound:
        try:
            playsound.playsound(
                "/usr/share/sounds/freedesktop/stereo/complete.oga",
                block=False,
            )
        except Exception as exc:
            logger.debug("Sound failed: %s", exc)


async def fetch_tasks(
    session: aiohttp.ClientSession,
    offset: int = 0,
) -> Optional[List[Dict[str, Any]]]:
    payload = {
        "0": {
            "json": {
                "annotationProjectId": ANNOTATION_PROJECT_ID,
                "pipelineStageId": None,
                "attempters": None,
                "search": None,
                "sortBy": "default",
                "sortOrder": "desc",
                "limit": 10,
                "offset": offset,
                "categories": None,
                "priorityLevel": None,
            },
            "meta": {
                "values": {
                    "pipelineStageId": ["undefined"],
                    "attempters": ["undefined"],
                    "search": ["undefined"],
                    "categories": ["undefined"],
                    "priorityLevel": ["undefined"],
                },
                "v": 1,
            },
        }
    }
    try:
        params = {
            "batch": "1",
            "input": json.dumps(payload),
        }
        async with session.get(GET_TASKS_URL, params=params, timeout=10) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return (
                data[0]
                .get("result", {})
                .get("data", {})
                .get("json", {})
                .get("tasks", [])
            )
    except Exception as exc:
        logger.error("Error while fetching: %s", exc)
        return None


async def claim_next_task(
    session: aiohttp.ClientSession,
) -> tuple[bool, bool]:
    try:
        async with session.post(
            CLAIM_NEXT_URL,
            json=CLAIM_NEXT_PAYLOAD,
            timeout=10,
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                result = (
                    data[0]
                    .get("result", {})
                    .get("data", {})
                    .get("json", {})
                )
                task_id = result.get("id") or result.get("taskId")
                if task_id:
                    logger.info("Claimed task %s", task_id)
                else:
                    logger.info("Claimed next task")
                return True, False
            if resp.status == 429:
                return False, True
            if resp.status in (404, 409, 410):
                return False, False

            text = await resp.text()
            logger.error(
                "Claim-next request failed: %s - %s",
                resp.status,
                text,
            )
            return False, False
    except Exception as exc:
        logger.error("Claim request error: %s", exc)
        return False, False


async def poll_loop():
    global backoff

    logger.info(
        "Async Sniper started with %d concurrent claim-next requests every "
        "%d seconds.",
        CLAIM_CONCURRENCY,
        POLL_INTERVAL,
    )

    # TCPConnector enables connection pooling for speed.
    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(
        headers=HEADERS,
        connector=connector,
    ) as session:
        while True:
            results = await asyncio.gather(
                *(claim_next_task(session) for _ in range(CLAIM_CONCURRENCY))
            )
            total_claimed = sum(success for success, _ in results)
            rate_limited = any(limited for _, limited in results)

            if total_claimed == 0:
                logger.info("No tasks available.")
            else:
                logger.info("Claimed %d task(s) this cycle.", total_claimed)
                backoff = INITIAL_BACKOFF
                notify("Sniper", f"Claimed {total_claimed} task(s)!")
                play_sound()

            if rate_limited:
                logger.info("Rate limited. Waiting %s seconds...", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, MAX_BACKOFF)
                continue

            await asyncio.sleep(POLL_INTERVAL)


async def test_connection():
    logger.info("Testing connection to Handshake API...")
    payload = {
        "0": {
            "json": {
                "annotationProjectId": ANNOTATION_PROJECT_ID,
                "pipelineStageId": None,
                "attempters": None,
                "search": None,
                "sortBy": "default",
                "sortOrder": "desc",
                "limit": 10,
                "offset": 0,
                "categories": None,
                "priorityLevel": None,
            },
            "meta": {
                "values": {
                    "pipelineStageId": ["undefined"],
                    "attempters": ["undefined"],
                    "search": ["undefined"],
                    "categories": ["undefined"],
                    "priorityLevel": ["undefined"],
                },
                "v": 1,
            },
        }
    }
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            params = {
                "batch": "1",
                "input": json.dumps(payload),
            }
            async with session.get(
                GET_TASKS_URL,
                params=params,
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(
                        "Connection failed! Status Code: %s",
                        resp.status,
                    )
                    logger.error(text)
                    return

                logger.info(
                    "Authentication successful. The server accepted your cookie."
                )
                logger.info("Here is the exact raw data returned by the server:")
                data = await resp.json()
                print("\n" + json.dumps(data, indent=4) + "\n")

                tasks = (
                    data[0]
                    .get("result", {})
                    .get("data", {})
                    .get("json", {})
                    .get("tasks", [])
                )

                if not tasks:
                    logger.info(
                        "The server returned an empty task list; no tasks are "
                        "available right now."
                    )
                else:
                    logger.info("There are %d task(s) available.", len(tasks))
        except Exception as exc:
            logger.error("Connection test crashed: %s", exc)


async def test_my_tasks():
    logger.info("Fetching your past tasks from 'My Tasks'...")
    payload = {
        "0": {
            "json": {
                "annotationProjectId": ANNOTATION_PROJECT_ID,
                "pipelineStageId": None,
                "statuses": None,
                "attempters": None,
                "search": None,
                "limit": 10,
                "offset": 0,
                "sortBy": "taskId",
                "sortOrder": "desc",
                "removeSkipped": True,
                "statusFilter": "all",
                "categories": None,
                "priorityLevel": None,
            },
            "meta": {
                "values": {
                    "pipelineStageId": ["undefined"],
                    "statuses": ["undefined"],
                    "attempters": ["undefined"],
                    "search": ["undefined"],
                    "categories": ["undefined"],
                    "priorityLevel": ["undefined"],
                },
                "v": 1,
            },
        }
    }
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            params = {
                "batch": "1",
                "input": json.dumps(payload),
            }
            async with session.get(
                GET_MY_TASKS_URL,
                params=params,
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(
                        "Fetch failed! Status Code: %s",
                        resp.status,
                    )
                    logger.error(text)
                    return

                data = await resp.json()
                response_json = (
                    data[0]
                    .get("result", {})
                    .get("data", {})
                    .get("json", {})
                )
                active = response_json.get("activeTasks", [])
                past = response_json.get("pastTasks", [])

                logger.info(
                    "Successfully fetched 'My Tasks': found %d active task(s) "
                    "and %d past task(s).",
                    len(active),
                    len(past),
                )
                for index, task in enumerate(past, start=1):
                    category = task.get("data", {}).get(
                        "attribute:Category",
                        "Unknown",
                    )
                    logger.info(
                        "   Task %d: %s (ID: %s)",
                        index,
                        category,
                        task.get("id"),
                    )
        except Exception as exc:
            logger.error("Test crashed: %s", exc)


if __name__ == "__main__":
    if not COOKIE:
        logger.error(
            "Authentication cookie missing. Add cookies to credentials.json "
            "or set the HANDSHAKE_COOKIE environment variable."
        )
        sys.exit(2)

    print("\n=== DYNAMO TASK SNIPER (ASYNC EDITION) ===")
    print("1. Start Polling (Sniper Mode)")
    print("2. Test Connection (Available Tasks)")
    print("3. Test Connection (My Past Tasks)")
    try:
        choice = input("Enter 1, 2, or 3: ").strip()
        if choice == "2":
            asyncio.run(test_connection())
        elif choice == "3":
            asyncio.run(test_my_tasks())
        else:
            asyncio.run(poll_loop())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
