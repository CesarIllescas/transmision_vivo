"""Manage an FFmpeg subprocess without depending on any UI module."""

import logging
import subprocess
import threading

logger = logging.getLogger(__name__)


class Streamer:
    """Own a streaming process and drain its diagnostics in the background."""

    def __init__(self, video_output, audio_output, fps, comand, *, on_stderr=None):
        self.video_output = video_output
        self.audio_output = audio_output
        self.fps = fps
        self.command = list(comand)
        self.comand = self.command  # Preserve the legacy public attribute.
        self.process = None
        self._on_stderr = on_stderr
        self._stderr_thread = None
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()

    def start_streaming(self):
        """Start once, returning the existing process if it is still running."""
        with self._state_lock:
            if self.is_streaming():
                return self.process
            self.stop_streaming()
            options = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.PIPE if self._on_stderr else subprocess.DEVNULL,
            }
            if hasattr(subprocess, "STARTUPINFO"):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                options["startupinfo"] = startupinfo
            process = subprocess.Popen(self.command, **options)
            self.process = process
            if self._on_stderr:
                self._stderr_thread = threading.Thread(
                    target=self._read_stderr,
                    args=(process,),
                    name="ffmpeg-diagnostics",
                    daemon=True,
                )
                self._stderr_thread.start()
            return process

    def _read_stderr(self, process):
        try:
            for line in iter(process.stderr.readline, b""):
                message = line.decode("utf-8", errors="replace").strip()
                if message:
                    try:
                        self._on_stderr(message)
                    except Exception:
                        # A UI callback must not stop draining the process pipe.
                        logger.exception("FFmpeg diagnostic callback failed")
        except (OSError, ValueError):
            # Closing the process during shutdown can close this pipe first.
            return

    def stop_streaming(self, timeout=5):
        """Stop safely, escalating to kill if graceful termination times out."""
        with self._state_lock:
            process = self.process
            reader = self._stderr_thread
            self.process = None
            self._stderr_thread = None
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass  # The child may exit between poll() and terminate().
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=timeout)
            else:
                process.wait(timeout=timeout)
        finally:
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=timeout)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass

    def is_streaming(self):
        """Report actual process liveness rather than only object existence."""
        with self._state_lock:
            return self.process is not None and self.process.poll() is None

    def write(self, data):
        """Send one encoded frame, failing clearly if the stream is unavailable."""
        with self._write_lock:
            with self._state_lock:
                process = self.process
                if process is None or process.poll() is not None or process.stdin is None:
                    raise BrokenPipeError("Streaming process is not running")
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (OSError, ValueError) as error:
                with self._state_lock:
                    if self.process is process:
                        self.stop_streaming()
                raise BrokenPipeError("Unable to write to the streaming process") from error

    def wait(self, timeout=None):
        """Wait for the active process, returning its exit status when available."""
        with self._state_lock:
            process = self.process
        return process.wait(timeout=timeout) if process is not None else None
