# claim-task-dynamo

An asynchronous Python client for claiming tasks from Handshake's batched tRPC
endpoint. It inspects claim responses, records results, and produces
CSV and JSON reports.

## Warning

This script sends state-changing requests. Its defaults start 80 concurrent
workers for 90 seconds, so only run it against a project you are authorized to
use. A successful discovery request can also claim a task.

## Requirements

- Python 3.8 or newer
- An authenticated Handshake session

In GitHub Codespaces or another Linux environment, create a virtual environment
and install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, use:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Configuration

Create a private credentials file from the included example:

```bash
cp credentials.example.json credentials.json
```

Add each authenticated browser cookie to the `cookies` object in
`credentials.json`:

```json
{
  "cookies": {
    "cookie-name": "cookie-value",
    "another-cookie": "another-value"
  },
  "annotationProjectId": "a1d39753-ae51-41df-8c86-2b7e73c6bd6b",
  "baggage": "session.id=...,user.id=...",
  "traceparent": "00-...",
  "tracestate": "dd=s:1;o:rum"
}
```

The copied browser `fetch()` code does not include cookies because the browser
adds them through `credentials: "include"`. Retrieve the cookie names and
values from Chrome DevTools under **Application > Cookies** or from the
request's `cookie` header in the **Network** panel.

`credentials.json` is excluded from Git and must never be committed. Environment
variables remain available as overrides:

```powershell
$env:HANDSHAKE_COOKIE = 'cookie-name=cookie-value; another-cookie=another-value'
$env:HANDSHAKE_ANNOTATION_PROJECT_ID = "your-project-id"
$env:HANDSHAKE_BAGGAGE = "session.id=..."
$env:HANDSHAKE_TRACEPARENT = "00-..."
$env:HANDSHAKE_TRACESTATE = "dd=s:1;o:rum"
```

## Usage

```powershell
python claim_task.py
```

Press `Ctrl+C` to request a graceful stop. Runtime behavior can be adjusted
with the constants near the top of `claim_task.py`, including `NUM_WORKERS`
and `DURATION_SECONDS`.

## Outputs

- `debug_first_response.txt`: first discovery response
- `debug_swarm.csv`: per-attempt status and latency data
- `qc_report.json`: aggregate claim counts and latency statistics

These generated files are excluded from Git.
