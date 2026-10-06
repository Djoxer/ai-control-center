"""Follow growing log files: complete new lines of the newest file, across rotation.

Shared by the logs module (live tail) and the dashboard (crash detection in Ollama's server.log).
Like `tail -F`, but open-read-close on every poll: an open handle would block rotation on Windows.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

MAX_POLL_BYTES = 1024 * 1024        # per poll; a burst is delivered over several polls


def resolve_files(patterns: list[str]) -> list[Path]:
    """All files matching the patterns, newest (by modification time) first, duplicates removed."""
    found: dict[str, Path] = {}
    for pattern in patterns:
        expanded = os.path.expandvars(os.path.expanduser(pattern))
        for name in glob.glob(expanded):
            p = Path(name)
            if p.is_file():
                found[str(p.resolve())] = p
    # mtime desc: base file (being written) first, then .1, .2 ... / server-1.log, server-2.log ...
    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)


class FileTail:
    """Remembers file identity and offset of the newest matching file. Blocking I/O: call from a thread."""

    def __init__(self, patterns: list[str], max_bytes: int = MAX_POLL_BYTES) -> None:
        self.patterns = list(patterns)
        self.max_bytes = max_bytes
        self.file: Path | None = None
        self.offset = 0
        self._file_id: tuple[int, int] | None = None     # (device, inode): changes when rotation creates a new file

    def attach(self, from_end: bool = True) -> None:
        """Start at the newest file. from_end=True: what is already in it is not replayed."""
        files = resolve_files(self.patterns)
        if not files:
            self.file, self._file_id, self.offset = None, None, 0
            return
        st = files[0].stat()
        self.file, self._file_id = files[0], (st.st_dev, st.st_ino)
        self.offset = st.st_size if from_end else 0

    def poll(self) -> list[str]:
        """Complete lines written since the last call. A half-written last line waits for the next poll."""
        files = resolve_files(self.patterns)
        if not files:
            self.file, self._file_id, self.offset = None, None, 0
            return []
        newest = files[0]
        st = newest.stat()
        fid = (st.st_dev, st.st_ino)
        if self.file is None or fid != self._file_id or st.st_size < self.offset:
            # new file (first appearance or rotation) or truncated: everything in it is new
            self.file, self._file_id, self.offset = newest, fid, 0
        if st.st_size == self.offset:
            return []
        with newest.open("rb") as f:
            f.seek(self.offset)
            data = f.read(min(st.st_size - self.offset, self.max_bytes))
        last_nl = data.rfind(b"\n")
        if last_nl < 0:
            if len(data) < self.max_bytes:
                return []                                  # line still being written
            complete = data                                # one giant line: deliver cut, never get stuck
        else:
            complete = data[: last_nl + 1]
        self.offset += len(complete)
        lines = []
        for raw in complete.split(b"\n"):
            text = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            if text.strip():
                lines.append(text)
        return lines
