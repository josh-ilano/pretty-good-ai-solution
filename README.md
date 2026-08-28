# Pretty Good AI Voicebot Evaluator

This project places one guarded test call to the Pretty Good AI assessment line,
uses OpenAI Realtime to act as a fictional patient, and saves the call transcript
and dual-channel recording for review.

> [!IMPORTANT]
> The destination is hard-coded to `+1-805-439-8008`. Running the script places
> a real outbound call and may incur SignalWire and OpenAI usage charges.

## How it works

- `realtime_call.py` starts the local FastAPI server, places the SignalWire call,
  and bridges PCMU telephone audio to and from OpenAI Realtime.
- `capture_call.py` collects the transcript and downloads the completed MP3.
- Each run writes its artifacts to a unique `output/<uuid>/` directory.

## Prerequisites

- Python 3.11 or newer
- An OpenAI API key with Realtime API access
- A SignalWire project, API token, Space URL, and outbound phone number
- [ngrok](https://ngrok.com/download) installed and authenticated

## Setup

From the repository root, create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install fastapi uvicorn websockets signalwire python-dotenv certifi
```

Create `.env` and add the following values:

```dotenv
OPENAI_API_KEY=your_openai_api_key
SIGNALWIRE_PROJECT_ID=your_signalwire_project_id
SIGNALWIRE_API_TOKEN=your_signalwire_api_token
SIGNALWIRE_SPACE_URL=your-space.signalwire.com
SIGNALWIRE_FROM_NUMBER=+15551234567
PUBLIC_BASE_URL=https://your-ngrok-domain.ngrok-free.app

# Optional settings
OPENAI_REALTIME_MODEL=gpt-realtime-mini
OPENAI_REALTIME_VOICE=marin
MAX_CALL_SECONDS=180
PORT=8000
```

`SIGNALWIRE_FROM_NUMBER` must be a SignalWire number that can place outbound
calls. Do not set it to the hard-coded assessment destination. Keep `.env`
private and never commit credentials.

## Run with ngrok

The local server must be publicly reachable so SignalWire can request the call
instructions, open the audio stream, and deliver recording webhooks.

1. In one terminal, start an HTTPS tunnel to the app's default port:

   ```bash
   ngrok http 8000
   ```

2. Copy the HTTPS forwarding URL shown by ngrok, for example
   `https://abc123.ngrok-free.app`, and set it as `PUBLIC_BASE_URL` in `.env`.
   Do not include a trailing path such as `/cxml`.

3. In a second terminal, activate the environment and execute the script:

   ```bash
   source .venv/bin/activate
   python realtime_call.py
   ```

The script validates the required environment variables, starts the web server
on port `8000`, and places exactly one call. Leave both terminals running until
the console reports that the recording and transcript were saved. Then stop the
Python server and ngrok with `Ctrl+C`.

If the ngrok URL changes between runs, update `PUBLIC_BASE_URL` before executing
the script again. If you set a custom `PORT`, use the same port in the ngrok
command (for example, `ngrok http 9000`).

## Output

Every execution creates a new directory like this:

```text
output/<uuid>/
├── transcript.txt
└── recording.mp3
```

The transcript contains finalized turns from the office agent and patient bot.
The recording is downloaded after SignalWire sends its completed-recording
webhook, which may arrive shortly after the call ends.

## Configuration reference

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Yes | Authenticates the OpenAI Realtime connection. |
| `SIGNALWIRE_PROJECT_ID` | Yes | Identifies the SignalWire project. |
| `SIGNALWIRE_API_TOKEN` | Yes | Authenticates SignalWire API and recording requests. |
| `SIGNALWIRE_SPACE_URL` | Yes | SignalWire Space hostname or URL. |
| `SIGNALWIRE_FROM_NUMBER` | Yes | SignalWire-owned outbound caller ID in E.164 format. |
| `PUBLIC_BASE_URL` | Yes | Public ngrok HTTPS origin that forwards to this app. |
| `OPENAI_REALTIME_MODEL` | No | Realtime model; defaults to `gpt-realtime-mini`. |
| `OPENAI_REALTIME_VOICE` | No | Realtime voice; defaults to `marin`. |
| `MAX_CALL_SECONDS` | No | Maximum call duration; defaults to `180`. |
| `PORT` | No | Local web-server port; defaults to `8000`. |

## Troubleshooting

- **Missing required environment variable:** confirm `.env` is in the repository
  root and contains every required variable listed above.
- **`PUBLIC_BASE_URL must be a public HTTPS URL`:** use ngrok's `https://` URL,
  not the local URL or the `http://` forwarding URL.
- **SignalWire cannot reach the app:** start ngrok before the Python script and
  confirm its forwarding port matches `PORT`.
- **No recording yet:** keep the server and tunnel running after the call ends;
  SignalWire delivers the completed-recording callback asynchronously.
- **Call ends automatically:** the default safety timeout is three minutes. Set
  `MAX_CALL_SECONDS` to another duration if needed.

## Safety

Use only fictional patient details. Do not enter real patient, insurance,
payment, or other sensitive data into the prompt or resulting artifacts.
