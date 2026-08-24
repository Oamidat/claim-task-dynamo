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

Create a virtual environment and install the required dependency:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Desktop notifications and sounds are optional. Install `plyer` and `playsound`
separately if needed.

## Configuration

The authentication cookie is read only from the `HANDSHAKE_COOKIE` environment
variable. Set it to the complete value of the authenticated request's `Cookie`
header before running the script:

```powershell
$env:HANDSHAKE_COOKIE = '<your Cookie header value>'
```

Do not commit the cookie or place it directly in `claim_task.py`. Browser
session cookies grant account access and should be rotated immediately if they
are exposed.

## Usage

```powershell
python claim_task.py
```

The menu provides three modes:

1. Continuously poll and claim available tasks.
2. Test authentication and list currently available tasks.
3. Fetch active and past claimed tasks.

Runtime activity is written to the console and `sniper.log`.
