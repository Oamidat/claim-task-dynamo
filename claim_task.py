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

# ---------- CONFIGURATION ----------
ANNOTATION_PROJECT_ID = "a1d39753-ae51-41df-8c86-2b7e73c6bd6b"
CLAIMER_ID = "3f64c23c-3892-4cb5-9248-2b07862e4de0"
BASE_URL = "https://ai.joinhandshake.com/api/trpc"

COOKIE = os.environ.get("HANDSHAKE_COOKIE")
HEADERS = {
    "Content-Type": "application/json",
}
if COOKIE:
    HEADERS["Cookie"] = COOKIE

POLL_INTERVAL = 1
INITIAL_BACKOFF = 60
MAX_BACKOFF = 300
backoff = INITIAL_BACKOFF

GET_TASKS_URL = f"{BASE_URL}/task.getAllClaimableTasksForFellow"
CLAIM_URL = f"{BASE_URL}/task.claimTask"
GET_MY_TASKS_URL = f"{BASE_URL}/task.listClaimedTasksForFellow"

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


async def claim_task(
    session: aiohttp.ClientSession,
    task_id: str,
) -> tuple[bool, bool]:
    payload = {
        "json": {
            "taskId": task_id,
            "annotationProjectId": ANNOTATION_PROJECT_ID,
            "claimerId": CLAIMER_ID,
        }
    }
    try:
        async with session.post(CLAIM_URL, json=payload, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Claimed task %s", task_id)
                return True, False
            if resp.status == 429:
                logger.warning("Rate limited while claiming %s", task_id)
                return False, True
            if resp.status == 409:
                logger.warning(
                    "Too slow! Task %s was just claimed by someone else.",
                    task_id,
                )
                return False, False

            text = await resp.text()
            logger.error(
                "Claim failed for %s: %s - %s",
                task_id,
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
        "Async Sniper started. Polling every %d seconds.",
        POLL_INTERVAL,
    )

    # TCPConnector enables connection pooling for speed.
    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(
        headers=HEADERS,
        connector=connector,
    ) as session:
        while True:
            offset = 0
            total_claimed = 0
            while True:
                tasks = await fetch_tasks(session, offset)
                if tasks is None:
                    await asyncio.sleep(5)
                    continue
                if not tasks:
                    break

                logger.info(
                    "Found %d task(s) on page %d. Firing parallel claims!",
                    len(tasks),
                    offset // 10 + 1,
                )

                claim_coroutines = []
                for task in tasks:
                    task_id = task.get("id")
                    if task_id:
                        claim_coroutines.append(claim_task(session, task_id))

                if claim_coroutines:
                    results = await asyncio.gather(*claim_coroutines)
                    for success, rate_limited in results:
                        if rate_limited:
                            logger.info(
                                "Rate limited. Waiting %s seconds...",
                                backoff,
                            )
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 1.5, MAX_BACKOFF)
                        if success:
                            total_claimed += 1
                            backoff = INITIAL_BACKOFF
                            notify("Sniper", "Claimed a task!")
                            play_sound()

                offset += 10
                await asyncio.sleep(0.1)

            if total_claimed == 0:
                logger.info("No tasks available.")
            else:
                logger.info("Claimed %d task(s) this cycle.", total_claimed)

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
            "Authentication cookie missing. Set the HANDSHAKE_COOKIE "
            "environment variable before running this script."
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
