# claim-task-dynamo

An asynchronous Python API client that polls for claimable Handshake tasks,
claims each returned page concurrently, and can inspect available or previously
claimed tasks.

## Warning

This script sends state-changing requests and may issue multiple claims at the
same time. Only run it for a project and account you are authorized to use.

## Requirements

- Python 3.9 or newer
- An authenticated Handshake session

Create a virtual environment and install the required dependency. In a
Codespace or another Linux environment, run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Desktop notifications and sounds are optional. Install `plyer` and `playsound`
separately if needed.

## Configuration

Create your private credentials file from the tracked example:

```bash
cp credentials.example.json credentials.json
```

Add each authenticated browser cookie to the `cookies` object in
`credentials.json`. The script combines those entries into the request's
`Cookie` header automatically. It also reads `annotationProjectId`, `baggage`,
`traceparent`, and `tracestate` from that file when present.

`credentials.json` is ignored by Git and must not be committed. The
`HANDSHAKE_COOKIE`, `HANDSHAKE_ANNOTATION_PROJECT_ID`, `HANDSHAKE_BAGGAGE`,
`HANDSHAKE_TRACEPARENT`, and `HANDSHAKE_TRACESTATE` environment variables can
override file values. Set `HANDSHAKE_CREDENTIALS_FILE` to use a different
credentials file path.

Browser session cookies grant account access and should be rotated immediately
if they are exposed.

## Usage

```powershell
python claim_task.py
```

The menu provides three modes:

1. Continuously send concurrent batched `task.claimNextTask` requests.
2. Test authentication and list currently available tasks.
3. Fetch active and past claimed tasks.

Runtime activity is written to the console and `sniper.log`.
