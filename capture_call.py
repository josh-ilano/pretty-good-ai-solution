#!/usr/bin/env python3
"""Capture transcripts and recordings for calls orchestrated by realtime_call.py.

This module does not place calls or conduct conversations. It owns artifact
creation and the SignalWire webhooks used to save each call's outputs.
"""

import asyncio
import base64
import json
import os
import ssl
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs
from urllib.request import Request as UrlRequest, urlopen

import certifi
from fastapi import APIRouter, Request
from fastapi.responses import Response


class CallCapture:
    """Manage the transcript and MP3 recording for one application run."""

    def __init__(self, output_root: Path, destination: str) -> None:
        # A UUID prevents one call from overwriting another call's artifacts.
        self.run_id = str(uuid.uuid4())
        self.run_dir = output_root / self.run_id
        self.transcript_path = self.run_dir / "transcript.txt"
        self.recording_path = self.run_dir / "recording.mp3"
        self.error_log_path = self.run_dir / "error.log"
        self.destination = destination
        self._transcript_lock = asyncio.Lock()
        # Patient text is held until SignalWire confirms its audio was played.
        self._pending_patient_transcripts: dict[str, str] = {}
        self._played_patient_items: set[str] = set()
        self._logged_failure_sids: set[str] = set()
        self.recording_ready = asyncio.Event()
        # SignalWire can deliver the same status webhook more than once. Keep a
        # strong reference to one download task so callbacks remain idempotent.
        self._recording_download_task: asyncio.Task[None] | None = None

    def initialize(self) -> None:
        """Create the run folder and a human-readable transcript header."""
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.transcript_path.write_text(
            "Call transcript\n"
            f"Run ID: {self.run_id}\n"
            f"Destination: {self.destination}\n\n",
            encoding="utf-8",
        )

    async def append_transcript(self, speaker: str, text: str) -> None:
        """Append one finalized utterance without interleaving concurrent writes."""
        cleaned = " ".join(text.split())
        if not cleaned:
            return

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"[{timestamp}] {speaker}: {cleaned}\n"
        async with self._transcript_lock:
            with self.transcript_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
        print(line, end="", flush=True)

    async def log_call_failure(self, fields: dict[str, str]) -> None:
        """Persist a terminal call failure with provider diagnostics."""
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status = fields.get("CallStatus", "unknown")
        call_sid = fields.get("CallSid", "unknown")
        if call_sid in self._logged_failure_sids:
            return
        self._logged_failure_sids.add(call_sid)
        error_code = fields.get("ErrorCode", "not provided")
        error_message = fields.get("ErrorMessage", "not provided")
        entry = (
            f"[{timestamp}] SignalWire call failed\n"
            f"Status: {status}\n"
            f"Call SID: {call_sid}\n"
            f"Error code: {error_code}\n"
            f"Error message: {error_message}\n\n"
        )
        async with self._transcript_lock:
            with self.error_log_path.open("a", encoding="utf-8") as stream:
                stream.write(entry)
        print(entry, end="", flush=True)
        print(f"Failure log saved: {self.error_log_path}", flush=True)

    async def consume_realtime_event(self, event: dict) -> None:
        """Capture office speech and stage generated patient speech."""
        event_type = event.get("type")
        if event_type == "conversation.item.input_audio_transcription.completed":
            await self.append_transcript("OFFICE AGENT", event.get("transcript", ""))
        elif event_type in {
            "response.output_audio_transcript.done",
            "response.audio_transcript.done",  # Retains compatibility with older events.
        }:
            item_id = event.get("item_id")
            transcript = event.get("transcript", "")
            if not item_id:
                # Without an item ID there is no reliable playback mark to match.
                print("Patient transcript omitted: OpenAI event had no item_id")
                return
            self._pending_patient_transcripts[item_id] = transcript
            await self._commit_patient_if_played(item_id)

    async def confirm_patient_playback(self, item_id: str) -> None:
        """Commit patient text after SignalWire acknowledges complete playback."""
        self._played_patient_items.add(item_id)
        await self._commit_patient_if_played(item_id)

    async def _commit_patient_if_played(self, item_id: str) -> None:
        """Write a patient turn once both transcript and playback are complete."""
        if item_id not in self._played_patient_items:
            return
        transcript = self._pending_patient_transcripts.pop(item_id, None)
        if transcript is None:
            return
        self._played_patient_items.discard(item_id)
        await self.append_transcript("PATIENT BOT", transcript)

    def recording_options(self, public_base_url: str) -> dict:
        """Return SignalWire call options needed to produce a dual-channel MP3."""
        base = public_base_url.rstrip("/")
        return {
            "record": True,
            "recording_channels": "dual",
            "recording_status_callback": f"{base}/capture/recording-status",
            "recording_status_callback_event": "completed",
            "status_callback": f"{base}/capture/call-status",
            "status_callback_event": ["completed"],
            "status_callback_method": "POST",
        }

    @staticmethod
    async def _webhook_fields(request: Request) -> dict[str, str]:
        """Accept either JSON or form-encoded SignalWire webhook payloads."""
        body = await request.body()
        if "application/json" in request.headers.get("content-type", ""):
            return {key: str(value) for key, value in json.loads(body).items()}
        return {
            key: values[-1]
            for key, values in parse_qs(
                body.decode("utf-8"), keep_blank_values=True
            ).items()
        }

    @staticmethod
    def _tls_context() -> ssl.SSLContext:
        """Use certifi so downloads work with the project's Python installation."""
        return ssl.create_default_context(cafile=certifi.where())

    async def _download_recording_with_retry(self, recording_url: str) -> None:
        """Wait for SignalWire's completed recording to become downloadable."""
        await asyncio.sleep(3)

        attempts = 5
        for attempt in range(1, attempts + 1):
            try:
                # urlopen is blocking, so run it outside FastAPI's event loop.
                await asyncio.to_thread(self._download_recording, recording_url)
                self.recording_ready.set()
                print(f"Recording saved: {self.recording_path}")
                print(f"Transcript saved: {self.transcript_path}")
                print("Artifacts ready. You may now stop the script with Ctrl+C.")
                return
            except Exception as exc:
                if attempt == attempts:
                    print(
                        f"Recording download failed after {attempts} attempts: {exc}"
                    )
                    return

                # A completed webhook can precede availability of the media URL
                # by a few seconds. Retry here instead of asking SignalWire to
                # retry the entire webhook by returning an HTTP error.
                delay = min(2 ** (attempt - 1), 10)
                print(
                    f"Recording is not downloadable yet ({exc}); "
                    f"retrying in {delay}s..."
                )
                await asyncio.sleep(delay)

    def _download_recording(self, recording_url: str) -> None:
        """Download the completed SignalWire recording into this run's folder."""
        url = recording_url if recording_url.lower().endswith(".mp3") else recording_url + ".mp3"
        credentials = (
            f"{self._required_env('SIGNALWIRE_PROJECT_ID')}:"
            f"{self._required_env('SIGNALWIRE_API_TOKEN')}"
        ).encode("utf-8")
        request = UrlRequest(url)
        token = base64.b64encode(credentials).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")

        with urlopen(request, context=self._tls_context(), timeout=60) as response:
            audio = response.read()
        if len(audio) < 100:
            raise RuntimeError("SignalWire returned an unexpectedly small recording")
        self.recording_path.write_bytes(audio)

    @staticmethod
    def _required_env(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return value

    def router(self) -> APIRouter:
        """Expose only the webhook routes that belong to artifact capture."""
        router = APIRouter(prefix="/capture")

        @router.post("/call-status")
        async def call_status(request: Request) -> Response:
            fields = await self._webhook_fields(request)
            status = fields.get("CallStatus")
            if status == "completed":
                print("Call completed. Waiting for SignalWire to finish the recording...")
            elif status in {"failed", "canceled", "busy", "no-answer"}:
                await self.log_call_failure(fields)
            return Response(status_code=204)

        @router.post("/recording-status")
        async def recording_status(request: Request) -> Response:
            fields = await self._webhook_fields(request)
            if fields.get("RecordingStatus") != "completed":
                return Response(status_code=204)

            recording_url = fields.get("RecordingUrl")
            if not recording_url:
                return Response(status_code=204)

            if self._recording_download_task is None:
                self._recording_download_task = asyncio.create_task(
                    self._download_recording_with_retry(recording_url)
                )

            return Response(status_code=204)

        return router
