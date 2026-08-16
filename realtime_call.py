#!/usr/bin/env python3
"""Orchestrate one SignalWire call with an OpenAI Realtime patient bot.

Artifact generation is delegated to capture_call.py.

Install: pip install fastapi uvicorn websockets signalwire python-dotenv certifi
Run:     python realtime_call.py
"""

import asyncio
import base64
import json
import os
import ssl
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


load_dotenv()

# Guardrail: this project may call only the assessment destination.
TEST_NUMBER = "+18054398008"
PORT = int(os.getenv("PORT", "8000"))
MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-mini")
VOICE = os.getenv("OPENAI_REALTIME_VOICE", "marin")
MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "180"))

PATIENT_PROMPT = """
You are Jamie Carter, a realistic patient calling a medical practice.
Your goal is to schedule a routine annual physical on a weekday morning.
Always speak English. Ignore recorded disclosures and phone-menu instructions,
including requests to press a number for another language. Do not answer until
the live office agent greets you or asks how they can help.
Speak naturally using complete thoughts. You may use more than one sentence
when that is how a person would normally answer, but avoid unusually long pauses
in the middle of a sentence. Listen carefully, answer follow-up questions
coherently, and politely steer back to scheduling if needed.
Do not say you are an AI or that this is a test. Your date of birth is July 4th,
2000. Invent sensitive details such as address, insurance ID, or payment data.
Once the appointment outcome is clear, thank the agent and say goodbye. The
remote office agent speaks first, so wait for its greeting.
""".strip()

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


def should_answer_office(transcript: str, conversation_started: bool) -> bool:
    """Reject recorded menus and obvious fragments without harming real turns."""
    cleaned = " ".join(transcript.split()).strip()
    lowered = cleaned.casefold()
    if not cleaned:
        return False

    # The opening disclosure is not a conversational turn.
    if not conversation_started and any(phrase in lowered for phrase in MENU_PHRASES):
        return False

    # A one- or two-word fragment without sentence-ending punctuation (such as
    # "You're") is likely an interrupted office utterance. Short questions like
    # "Anything else?" remain valid because punctuation shows completion.
    if len(cleaned.split()) <= 2 and cleaned[-1] not in ".?!":
        return False

    return True

# The capture component owns this run's UUID folder, transcript, and recording.
capture = CallCapture(Path(__file__).resolve().parent / "output", TEST_NUMBER)


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


async def place_call() -> None:
    """Place the outbound call and point SignalWire at this app's cXML route."""
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
    print(f"Artifacts will be saved under: {capture.run_dir}")
    asyncio.create_task(end_call_after_timeout(call.sid))


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize capture, then start exactly one call with the web server."""
    capture.initialize()
    call_task = asyncio.create_task(place_call())
    yield
    if not call_task.done():
        call_task.cancel()


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
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 650,
                                # VAD still divides office speech into turns, but
                                # the application decides which turns deserve an
                                # answer after seeing their transcripts.
                                "create_response": False,
                                # The remote endpoint is another voice bot. Do not
                                # let its early VAD response cancel our sentence.
                                "interrupt_response": False,
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
                nonlocal response_in_progress
                async for raw in openai_ws:
                    event = json.loads(raw)
                    event_type = event.get("type")
                    if event_type == "response.output_audio.delta" and stream_sid:
                        patient_audio_playing = True
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

                        conversation_started = True
                        response_in_progress = True
                        await openai_ws.send(json.dumps({"type": "response.create"}))
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
    uvicorn.run(app, host="0.0.0.0", port=PORT)
