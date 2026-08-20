#!/usr/bin/env python3
"""Orchestrate one SignalWire call with an OpenAI Realtime patient bot.

Artifact generation is delegated to capture_call.py.

Install: pip install fastapi uvicorn websockets signalwire python-dotenv certifi
Run:     python realtime_call.py
"""

import argparse
import asyncio
import base64
import json
import os
import ssl
import sys
import termios
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import certifi
import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from signalwire.rest import Client

from capture_call import CallCapture
from scenario_generator.generate import (
    AUTHORIZED_DESTINATION,
    DEFAULT_INDEX,
    DEFAULT_OUTPUT,
    choose_diverse_topic,
    generate_manual_patient_prompt,
    generate_scenario,
    save_scenario,
)
from scenario_generator.call_controls import contains_ctrl_t
from scenario_generator.scenario_contract import (
    load_patient_prompt,
    read_patient_prompt_file,
)


load_dotenv()

# Guardrail: this project may call only the assessment destination.
TEST_NUMBER = "+18054398008"
PORT = int(os.getenv("PORT", "8000"))
MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-mini")
VOICE = os.getenv("OPENAI_REALTIME_VOICE", "marin")
MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "180"))
# Require a meaningful quiet gap before handing the conversational floor over.
OFFICE_VAD_SILENCE_MS = int(os.getenv("OFFICE_VAD_SILENCE_MS", "1000"))
OFFICE_TURN_SETTLE_MS = int(os.getenv("OFFICE_TURN_SETTLE_MS", "900"))
def runtime_options() -> argparse.Namespace:
    """Parse RAG and manual execution options while tolerating importer arguments."""
    parser = argparse.ArgumentParser(
        description="Place a guarded RAG or manual patient test call."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--rag",
        action="store_true",
        help="Generate and use a random policy-grounded RAG scenario",
    )
    group.add_argument(
        "--patient-prompt",
        help="Complete replacement for PATIENT_PROMPT",
    )
    group.add_argument(
        "--patient-prompt-file",
        type=Path,
        help="Path to a UTF-8 text file containing the complete PATIENT_PROMPT",
    )
    parser.add_argument(
        "--rag-index",
        type=Path,
        default=DEFAULT_INDEX,
        help="SQLite policy index used with --rag",
    )
    parser.add_argument(
        "--rag-evidence-limit",
        type=int,
        default=3,
        help="Number of policy chunks to retrieve with --rag (1-10)",
    )
    args, _ = parser.parse_known_args()
    if not 1 <= args.rag_evidence_limit <= 10:
        parser.error("--rag-evidence-limit must be between 1 and 10")
    return args


def manual_prompt_override(args: argparse.Namespace) -> str | None:
    """Resolve a manual full-prompt override from CLI or environment."""
    if args.patient_prompt:
        return args.patient_prompt
    if args.patient_prompt_file:
        return read_patient_prompt_file(args.patient_prompt_file)

    env_prompt = os.getenv("MANUAL_PATIENT_PROMPT")
    env_prompt_file = os.getenv("MANUAL_PATIENT_PROMPT_FILE")
    if env_prompt and env_prompt_file:
        parser.error(
            "MANUAL_PATIENT_PROMPT and MANUAL_PATIENT_PROMPT_FILE cannot be used together"
        )
    if env_prompt_file:
        return read_patient_prompt_file(Path(env_prompt_file))
    return env_prompt


_runtime_options = runtime_options()
if _runtime_options.rag:
    _rag_topic = choose_diverse_topic(DEFAULT_OUTPUT)
    print(f"Random RAG test selected: {_rag_topic}", flush=True)
    _rag_scenario = generate_scenario(
        _rag_topic,
        index=_runtime_options.rag_index,
        destination=AUTHORIZED_DESTINATION,
        evidence_limit=_runtime_options.rag_evidence_limit,
    )
    SCENARIO_JSON_PATH = save_scenario(
        _rag_scenario,
        DEFAULT_OUTPUT
        / f"{_rag_scenario['category']}-{_rag_scenario['scenario_id']}.json",
    )
    PATIENT_PROMPT, SCENARIO_JSON_PATH = load_patient_prompt(SCENARIO_JSON_PATH)
    print(f"RAG scenario saved: {SCENARIO_JSON_PATH}", flush=True)
else:
    _patient_prompt = manual_prompt_override(_runtime_options)
    if not _patient_prompt:
        raise SystemExit(
            "Manual mode requires --patient-prompt-file custom_input/<file>.txt "
            "or --patient-prompt. Use --rag for a generated RAG scenario."
        )
    SCENARIO_JSON_PATH = generate_manual_patient_prompt(_patient_prompt)
    PATIENT_PROMPT, SCENARIO_JSON_PATH = load_patient_prompt(SCENARIO_JSON_PATH)

_scenario_record = json.loads(SCENARIO_JSON_PATH.read_text(encoding="utf-8"))
SCENARIO_TOPIC = str(
    _scenario_record.get("test_topic")
    or _scenario_record.get("category")
    or "scenario"
)

# Common phrases in the assessment number's recorded preamble. These are still
# transcribed for an accurate record, but they should not trigger the patient.
MENU_PHRASES = (
    "call may be recorded",
    "quality and training",
    "para español",
    "para espanol",
    "oprima el",
    "press one",
    "press 1",
    "press two",
    "press 2",
)

# A provider greeting may be transcribed in the same VAD turn as the recorded
# disclosure. Its presence makes the combined turn conversationally actionable.
GREETING_PHRASES = (
    "how may i help",
    "how can i help",
    "what can i help",
    "thanks for calling",
    "thank you for calling",
)


def should_answer_office(transcript: str, conversation_started: bool) -> bool:
    """Reject recorded menus and obvious fragments without harming real turns."""
    cleaned = " ".join(transcript.split()).strip()
    lowered = cleaned.casefold()
    if not cleaned:
        return False

    # The opening disclosure is not a conversational turn.
    if not conversation_started and any(phrase in lowered for phrase in MENU_PHRASES):
        contains_greeting = any(phrase in lowered for phrase in GREETING_PHRASES)
        if not contains_greeting:
            return False

    # A one- or two-word fragment without sentence-ending punctuation (such as
    # "You're") is likely an interrupted office utterance. Short questions like
    # "Anything else?" remain valid because punctuation shows completion.
    if len(cleaned.split()) <= 2 and cleaned[-1] not in ".?!":
        return False

    return True

# The capture component owns this run's UUID folder, transcript, and recording.
capture = CallCapture(
    Path(__file__).resolve().parent / "output",
    TEST_NUMBER,
    SCENARIO_TOPIC,
)
active_call_sid: str | None = None
call_started = asyncio.Event()
uvicorn_server: uvicorn.Server | None = None


def required_env(name: str) -> str:
    """Read mandatory configuration and fail before placing a paid call."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def public_urls() -> tuple[str, str]:
    """Build the public HTTP and WebSocket endpoints SignalWire will contact."""
    base = required_env("PUBLIC_BASE_URL").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("PUBLIC_BASE_URL must be a public HTTPS URL")
    return f"{base}/cxml", f"wss://{parsed.netloc}/media-stream"


def signalwire_space() -> str:
    """Normalize the SignalWire Space URL into the SDK's expected hostname."""
    raw = required_env("SIGNALWIRE_SPACE_URL").strip().rstrip("/")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.hostname:
        raise RuntimeError("SIGNALWIRE_SPACE_URL must look like example.signalwire.com")
    return parsed.hostname


def signalwire_client() -> Client:
    """Create an authenticated SignalWire compatibility client."""
    return Client(
        required_env("SIGNALWIRE_PROJECT_ID"),
        required_env("SIGNALWIRE_API_TOKEN"),
        signalwire_space_url=signalwire_space(),
    )


def tls_context() -> ssl.SSLContext:
    """Use certifi for reliable TLS verification with Python 3.14."""
    return ssl.create_default_context(cafile=certifi.where())


async def end_call_after_timeout(call_sid: str) -> None:
    """Prevent a stalled conversation from exceeding the configured duration."""
    await asyncio.sleep(MAX_CALL_SECONDS)
    if capture.recording_ready.is_set():
        return
    print(f"Maximum call duration reached ({MAX_CALL_SECONDS}s); ending call.")
    try:
        await asyncio.to_thread(
            signalwire_client().calls(call_sid).update, status="completed"
        )
    except Exception as exc:
        print(f"Could not end call automatically: {exc}")


async def end_active_call_from_keyboard() -> None:
    """End the live call on Ctrl+T, then wait for captured artifacts."""
    if not sys.stdin.isatty():
        print("Ctrl+T early termination unavailable: stdin is not a terminal.")
        return

    loop = asyncio.get_running_loop()
    input_ready = asyncio.Event()
    input_buffer: list[str] = []
    file_descriptor = sys.stdin.fileno()
    original_settings = termios.tcgetattr(file_descriptor)
    control_settings = termios.tcgetattr(file_descriptor)
    # Read individual keys without echo so Ctrl+T reaches this process at once.
    control_settings[0] &= ~(termios.IXON | termios.IXOFF)
    control_settings[3] &= ~(termios.ICANON | termios.ECHO)
    control_settings[6][termios.VMIN] = 1
    control_settings[6][termios.VTIME] = 0

    def read_terminal_input() -> None:
        data = os.read(file_descriptor, 32).decode("utf-8", errors="ignore")
        input_buffer.append(data)
        input_ready.set()

    termios.tcsetattr(file_descriptor, termios.TCSANOW, control_settings)
    loop.add_reader(file_descriptor, read_terminal_input)
    print("Press Ctrl+T to end the call early and finalize its artifacts.")
    try:
        while True:
            await input_ready.wait()
            input_ready.clear()
            data = "".join(input_buffer)
            input_buffer.clear()
            if contains_ctrl_t(data):
                break

        print("Ctrl+T received; ending the active call...", flush=True)
        await call_started.wait()
        if active_call_sid is None:
            print("No active SignalWire call was available to terminate.")
            return
        await asyncio.to_thread(
            signalwire_client().calls(active_call_sid).update,
            status="completed",
        )
        print(
            "Call ended. Waiting for SignalWire to finalize the recording...",
            flush=True,
        )
        await capture.recording_ready.wait()
        print(f"Transcript saved: {capture.transcript_path}", flush=True)
        print(f"Recording saved: {capture.recording_path}", flush=True)
        if uvicorn_server is not None:
            uvicorn_server.should_exit = True
    except Exception as exc:
        print(f"Could not end the call with Ctrl+T: {exc}", flush=True)
    finally:
        loop.remove_reader(file_descriptor)
        termios.tcsetattr(file_descriptor, termios.TCSANOW, original_settings)


async def monitor_call_status(call_sid: str) -> None:
    """Detect pre-dial failures even when the public webhook is unreachable."""
    terminal_failures = {"failed", "canceled", "busy", "no-answer"}
    while True:
        await asyncio.sleep(1)
        try:
            call = await asyncio.to_thread(
                signalwire_client().calls(call_sid).fetch
            )
        except Exception as exc:
            print(f"Could not retrieve SignalWire call status: {exc}", flush=True)
            continue

        if call.status in terminal_failures:
            await capture.log_call_failure({
                "CallStatus": call.status,
                "CallSid": call_sid,
                "ErrorMessage": (
                    "SignalWire did not expose a detailed failure reason through "
                    "the Call API; inspect the call timeline in the dashboard."
                ),
            })
            return
        if call.status == "completed":
            return


async def place_call() -> None:
    """Place the outbound call and point SignalWire at this app's cXML route."""
    global active_call_sid
    # Give Uvicorn time to bind before SignalWire requests the instructions.
    await asyncio.sleep(1)
    cxml_url, _ = public_urls()
    from_number = required_env("SIGNALWIRE_FROM_NUMBER")
    if from_number == TEST_NUMBER:
        raise RuntimeError("SIGNALWIRE_FROM_NUMBER cannot be the assessment destination")

    # Capture supplies only recording-related options; orchestration stays here.
    recording_options = capture.recording_options(required_env("PUBLIC_BASE_URL"))
    call = await asyncio.to_thread(
        signalwire_client().calls.create,
        to=TEST_NUMBER,
        from_=from_number,
        url=cxml_url,
        method="POST",
        **recording_options,
    )
    print(f"SignalWire call initiated safely: sid={call.sid}, to={TEST_NUMBER}")
    active_call_sid = call.sid
    call_started.set()
    print(f"Artifacts will be saved under: {capture.run_dir}")
    asyncio.create_task(end_call_after_timeout(call.sid))
    asyncio.create_task(monitor_call_status(call.sid))


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize capture, then start exactly one call with the web server."""
    capture.initialize()
    print(f"Patient prompt loaded from: {SCENARIO_JSON_PATH}")
    call_task = asyncio.create_task(place_call())
    keyboard_task = asyncio.create_task(end_active_call_from_keyboard())
    yield
    if not call_task.done():
        call_task.cancel()
    if not keyboard_task.done():
        keyboard_task.cancel()


app = FastAPI(lifespan=lifespan)
# Capture owns its callback handlers; the orchestrator only mounts them.
app.include_router(capture.router())


@app.api_route("/cxml", methods=["GET", "POST"])
async def cxml() -> Response:
    """Tell SignalWire to open a bidirectional PCMU media stream."""
    _, stream_url = public_urls()
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Connect><Stream url="{stream_url}" '
        'codec="PCMU@8000h" realtime="true" /></Connect></Response>'
    )
    return Response(body, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(signalwire_ws: WebSocket) -> None:
    """Bridge audio in both directions between SignalWire and OpenAI."""
    await signalwire_ws.accept()
    openai_url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    stream_sid: str | None = None
    # Each mark maps SignalWire's playback acknowledgment to an OpenAI item.
    pending_playback_marks: dict[str, str] = {}
    # Realtime audio deltas can arrive with network or generation gaps. Buffer a
    # complete utterance so those gaps do not sound like the end of the turn to
    # the remote office bot's VAD.
    patient_audio_buffers: dict[str, bytearray] = {}
    # Inbound office audio is briefly retained while patient audio is playing.
    buffered_inbound_audio: list[str] = []
    patient_audio_playing = False
    conversation_started = False
    response_in_progress = False
    office_turn_version = 0
    office_is_speaking = False
    pending_response_task: asyncio.Task | None = None

    try:
        async with websockets.connect(
            openai_url,
            additional_headers={
                "Authorization": f"Bearer {required_env('OPENAI_API_KEY')}"
            },
            ssl=tls_context(),
            max_size=None,
        ) as openai_ws:
            # Configure the bot, audio codecs, transcription, and turn detection.
            await openai_ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": MODEL,
                    "instructions": PATIENT_PROMPT,
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcmu"},
                            "transcription": {
                                "model": "gpt-4o-mini-transcribe",
                                "language": "en",
                            },
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 175,
                                # Synthetic voices sometimes pause inside a
                                # sentence. A longer silence threshold reduces
                                # false end-of-turn detection.
                                "silence_duration_ms": OFFICE_VAD_SILENCE_MS,
                                # VAD still divides office speech into turns, but
                                # the application decides which turns deserve an
                                # answer after seeing their transcripts.
                                "create_response": False,
                                # The remote endpoint is another voice bot. Do not
                                # let its early VAD response cancel our sentence.
                                "interrupt_response": True,
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcmu"},
                            "voice": VOICE,
                        },
                    },
                },
            }))

            async def signalwire_to_openai() -> None:
                """Forward office audio and process playback acknowledgments."""
                nonlocal stream_sid, patient_audio_playing

                async def send_audio(payload: str) -> None:
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": payload,
                    }))

                while True:
                    event = json.loads(await signalwire_ws.receive_text())
                    event_type = event.get("event")
                    if event_type == "start":
                        stream_sid = event["start"]["streamSid"]
                    elif event_type == "media":
                        payload = event["media"]["payload"]
                        if patient_audio_playing:
                            # Preserve speech from an early-interrupting office bot
                            # without letting it start a competing patient turn.
                            buffered_inbound_audio.append(payload)
                        else:
                            await send_audio(payload)
                    elif event_type == "mark":
                        mark_name = event.get("mark", {}).get("name")
                        item_id = pending_playback_marks.pop(mark_name, None)
                        if item_id:
                            print(f"Patient audio playback completed: {mark_name}")
                            await capture.confirm_patient_playback(item_id)

                        # No patient responses remain queued. Release any office
                        # audio accumulated during playback in its original order.
                        if not pending_playback_marks:
                            patient_audio_playing = False
                            queued = buffered_inbound_audio.copy()
                            buffered_inbound_audio.clear()
                            for payload in queued:
                                await send_audio(payload)
                    elif event_type == "stop":
                        if pending_playback_marks:
                            names = ", ".join(pending_playback_marks)
                            print(
                                "SignalWire stream stopped before patient audio "
                                f"finished playing; pending marks: {names}"
                            )
                        return

            async def openai_to_signalwire() -> None:
                """Return bot audio and delegate transcript events to capture."""
                nonlocal patient_audio_playing, conversation_started
                nonlocal response_in_progress, office_turn_version
                nonlocal office_is_speaking, pending_response_task

                async def answer_after_quiet(transcript: str, version: int) -> None:
                    """Create a response only if the office remains silent."""
                    nonlocal patient_audio_playing, conversation_started
                    nonlocal response_in_progress
                    try:
                        await asyncio.sleep(OFFICE_TURN_SETTLE_MS / 1000)
                    except asyncio.CancelledError:
                        return

                    # Any newer speech-start event means the office reclaimed
                    # the floor during the settling window.
                    if version != office_turn_version or office_is_speaking:
                        return
                    if response_in_progress or patient_audio_playing:
                        print(f"Office turn held because patient floor is busy: {transcript!r}")
                        return

                    conversation_started = True
                    response_in_progress = True
                    print(f"Office turn complete; creating patient response: {transcript!r}")
                    await openai_ws.send(json.dumps({"type": "response.create"}))

                async for raw in openai_ws:
                    event = json.loads(raw)
                    event_type = event.get("type")
                    if event_type == "input_audio_buffer.speech_started":
                        # Cancel the pending response if the office resumes after
                        # a pause. This is the core half-duplex floor control.
                        office_turn_version += 1
                        office_is_speaking = True
                        if pending_response_task and not pending_response_task.done():
                            pending_response_task.cancel()
                            print("Office resumed speaking; pending patient response cancelled.")
                        if response_in_progress and not patient_audio_playing:
                            # The office reclaimed the floor while OpenAI was
                            # generating but before any patient audio was sent.
                            # Cancel safely; the next complete office turn will
                            # trigger a fresh response.
                            await openai_ws.send(json.dumps({"type": "response.cancel"}))
                            patient_audio_buffers.clear()
                            response_in_progress = False
                            print("Office resumed speaking; generated patient reply cancelled.")
                    elif event_type == "input_audio_buffer.speech_stopped":
                        office_is_speaking = False
                    elif event_type == "response.output_audio.delta" and stream_sid:
                        item_id = event.get("item_id")
                        if not item_id:
                            print("Cannot buffer patient audio: delta event had no item_id")
                            continue
                        try:
                            audio_bytes = base64.b64decode(event["delta"], validate=True)
                        except (KeyError, ValueError) as exc:
                            print(f"Invalid OpenAI audio delta: {exc}")
                            continue
                        patient_audio_buffers.setdefault(item_id, bytearray()).extend(
                            audio_bytes
                        )
                    elif event_type in {
                        "response.output_audio.done",
                        "response.audio.done",  # Compatibility with older events.
                    } and stream_sid:
                        item_id = event.get("item_id")
                        if not item_id:
                            print("Cannot track patient playback: audio event had no item_id")
                            continue

                        audio = patient_audio_buffers.pop(item_id, None)
                        if not audio:
                            print(f"Cannot play patient response: no audio for {item_id}")
                            patient_audio_playing = False
                            continue

                        # The complete office turn stayed quiet throughout model
                        # generation. The patient may now take the floor.
                        patient_audio_playing = True
                        # Sending one contiguous PCMU payload prevents transport
                        # gaps between Realtime deltas from triggering the other
                        # bot's end-of-speech detector mid-sentence.
                        await signalwire_ws.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": base64.b64encode(audio).decode("ascii")
                            },
                        })

                        # SignalWire echoes this mark only after all previously
                        # queued media has finished playing on the phone call.
                        mark_name = f"patient-{uuid.uuid4()}"
                        pending_playback_marks[mark_name] = item_id
                        await signalwire_ws.send_json({
                            "event": "mark",
                            "streamSid": stream_sid,
                            "mark": {"name": mark_name},
                        })
                        print(f"Waiting for patient audio playback: {mark_name}")
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        # Capture every office utterance, including ignored ones,
                        # so the transcript continues to reflect the recording.
                        await capture.consume_realtime_event(event)
                        transcript = event.get("transcript", "")
                        if not should_answer_office(transcript, conversation_started):
                            print(f"Ignoring non-conversational office audio: {transcript!r}")
                            continue
                        if response_in_progress:
                            print(f"Deferring office turn while response is active: {transcript!r}")
                            continue

                        # Restart the settling timer for the newest complete
                        # office utterance; only the latest turn may be answered.
                        if pending_response_task and not pending_response_task.done():
                            pending_response_task.cancel()
                        pending_response_task = asyncio.create_task(
                            answer_after_quiet(transcript, office_turn_version)
                        )
                    elif event_type == "response.done":
                        response_in_progress = False
                        await capture.consume_realtime_event(event)
                    elif event_type == "error":
                        response_in_progress = False
                        print("OpenAI Realtime error:", event.get("error", event))
                    else:
                        await capture.consume_realtime_event(event)

            # End the bridge when either side closes, then cancel its peer task.
            tasks = {
                asyncio.create_task(signalwire_to_openai()),
                asyncio.create_task(openai_to_signalwire()),
            }
            _, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending_response_task and not pending_response_task.done():
                pending_response_task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"Media stream failed: {exc}")


if __name__ == "__main__":
    # Validate all credentials before opening the server or placing a paid call.
    for variable in (
        "OPENAI_API_KEY",
        "SIGNALWIRE_PROJECT_ID",
        "SIGNALWIRE_API_TOKEN",
        "SIGNALWIRE_SPACE_URL",
        "SIGNALWIRE_FROM_NUMBER",
        "PUBLIC_BASE_URL",
    ):
        required_env(variable)

    print("Using prompt: ", PATIENT_PROMPT)

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.run()
