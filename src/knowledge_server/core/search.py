"""Literal search through ripgrep.

Search runs in two stages. The load stage finds candidate notes with the path
policy and loads each one with `load_note_text`, the reader's bounded loader.
The match stage sends the loaded text of the eligible notes, in sorted path
order, to one ripgrep process on standard input. ripgrep never opens a vault
file, so a file changed or replaced after loading cannot affect the matches.

Each note's text is sent as the reader sees it: without a BOM, with CRLF
normalized to LF, and with every line ending in LF. A reported line number maps
back to a note and a line in it; a match cannot cross notes because a query
cannot contain a newline. Because the stream is in result order, ripgrep's
`--max-count` stops the search once one hit more than requested is found.

The load stage runs in a worker thread so that it does not block the event
loop. It checks the deadline and a stop signal between entries, files, and
read chunks. A filesystem call that is already blocked cannot be interrupted:
the search still returns at the deadline, and the worker stops when that call
returns.
"""

import asyncio
import bisect
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    KnowledgeError,
    SearchMatch,
    SearchRequest,
    SearchResult,
    SearchSkipped,
)
from knowledge_server.core.paths import PathPolicy, ResolvedPath, TargetKind
from knowledge_server.core.reader import load_note_text

# ripgrep needs no environment variables. An empty environment keeps
# RIPGREP_CONFIG_PATH and similar settings from reaching it, in addition to
# --no-config. The working directory does not affect a search of standard
# input; "/" avoids depending on the server's own directory.
_RIPGREP_ENV: dict[str, str] = {}
_RIPGREP_CWD = "/"
_WRITE_CHUNK_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class _Stopped(Exception):
    """Raised in the load worker after the search stopped waiting for it."""


@dataclass(frozen=True, slots=True)
class _NoteSpan:
    """Where one note with at least one line sits in the input stream.

    Attributes:
        path: Root-relative path of the note.
        first_line: Stream line number of the note's first line, from 1.
        line_count: Number of lines in the note.
        start: Byte offset of the note's text in the stream.
        end: Byte offset just after the note's text.
    """

    path: str
    first_line: int
    line_count: int
    start: int
    end: int


@dataclass(slots=True)
class _Corpus:
    """The loaded notes of one search, ready to send to ripgrep."""

    stream: bytearray = field(default_factory=bytearray)
    spans: list[_NoteSpan] = field(default_factory=list)
    # The spans' first line numbers, in order, for mapping a line to a span.
    first_lines: list[int] = field(default_factory=list)
    skipped: dict[str, int] = field(
        default_factory=lambda: {"too_large": 0, "invalid_text": 0, "unreadable": 0}
    )


async def search_notes(
    policy: PathPolicy,
    request: SearchRequest,
    limits: Limits = DEFAULT_LIMITS,
    *,
    ripgrep: str,
) -> SearchResult:
    """Find the lines of visible notes that contain a literal query.

    A directory target is searched recursively. Notes that cannot be searched
    are skipped and counted; for an explicit file target, the same conditions
    are errors. Cancelling the call stops the search and kills ripgrep.

    Args:
        policy: The policy for the vault root.
        request: The query, target path, result limit, and case option.
        limits: Limits for the request, the search budgets, and snippets.
        ripgrep: Path of the ripgrep executable.

    Returns:
        Up to `request.max_results` hits ordered by path and line number.

    Raises:
        KnowledgeError: `INVALID_ARGUMENT` if the query or result limit
            exceeds `limits`; `SEARCH_LIMIT_EXCEEDED` if a search budget or
            the deadline is exceeded; `SEARCH_FAILED` if ripgrep fails or
            reports output that does not agree with its input; for an explicit
            file target, `FILE_TOO_LARGE` or `INVALID_ENCODING`; or any code
            from the path checks and loading of the target.
    """
    if (
        len(request.query) > limits.max_query_length
        or request.max_results > limits.max_search_results
    ):
        raise KnowledgeError(DomainErrorCode.INVALID_ARGUMENT)

    deadline = time.monotonic() + limits.search_deadline_seconds
    stop = threading.Event()
    try:
        async with asyncio.timeout(limits.search_deadline_seconds):
            try:
                corpus = await asyncio.to_thread(
                    _load_corpus, policy, request.path, limits, deadline, stop
                )
            finally:
                stop.set()
            records = await _run_ripgrep(ripgrep, request, corpus, limits)
    except TimeoutError:
        raise KnowledgeError(DomainErrorCode.SEARCH_LIMIT_EXCEEDED) from None

    matches = [_to_match(record, corpus, limits) for record in records]
    skipped = SearchSkipped(**corpus.skipped)
    return SearchResult(
        matches=matches[: request.max_results],
        truncated=len(matches) > request.max_results,
        incomplete=any(corpus.skipped.values()),
        skipped=skipped,
    )


# Load stage


def _load_corpus(
    policy: PathPolicy,
    api_path: str,
    limits: Limits,
    deadline: float,
    stop: threading.Event,
) -> _Corpus:
    """Discover and load the target's notes; runs in a worker thread."""

    def checkpoint() -> None:
        if stop.is_set():
            raise _Stopped
        if time.monotonic() >= deadline:
            raise KnowledgeError(DomainErrorCode.SEARCH_LIMIT_EXCEEDED)

    # Every chunk read counts, including chunks from notes that fail to read
    # later or are skipped.
    used = 0

    def charge(size: int) -> None:
        nonlocal used
        used += size
        if used > limits.max_search_source_bytes:
            raise KnowledgeError(DomainErrorCode.SEARCH_LIMIT_EXCEEDED)

    corpus = _Corpus()
    target = policy.resolve(api_path, TargetKind.EITHER)
    if target.kind is TargetKind.FILE:
        loaded = load_note_text(
            policy,
            target,
            limits,
            byte_budget=limits.max_search_source_bytes,
            checkpoint=checkpoint,
            on_read=charge,
        )
        if loaded.unreadable_reason == "too_large":
            raise KnowledgeError(DomainErrorCode.FILE_TOO_LARGE)
        if loaded.text is None:
            raise KnowledgeError(DomainErrorCode.INVALID_ENCODING)
        _append_note(corpus, target.relative_path, loaded.text)
        return corpus

    for relative_path in _discover(policy, target, limits, checkpoint, corpus):
        checkpoint()
        resolved = ResolvedPath(
            policy.root / relative_path, relative_path, TargetKind.FILE
        )
        try:
            loaded = load_note_text(
                policy,
                resolved,
                limits,
                byte_budget=limits.max_search_source_bytes - used,
                checkpoint=checkpoint,
                on_read=charge,
            )
        except KnowledgeError as error:
            if error.code not in {
                DomainErrorCode.NOT_FOUND,
                DomainErrorCode.ACCESS_DENIED,
                DomainErrorCode.NOT_A_FILE,
            }:
                raise
            corpus.skipped["unreadable"] += 1
            continue
        if loaded.unreadable_reason is not None:
            corpus.skipped[loaded.unreadable_reason] += 1
        elif loaded.text is not None:
            _append_note(corpus, relative_path, loaded.text)
    return corpus


def _discover(
    policy: PathPolicy,
    directory: ResolvedPath,
    limits: Limits,
    checkpoint: Callable[[], None],
    corpus: _Corpus,
) -> list[str]:
    """Return the visible notes below a directory, sorted by path.

    Every scanned entry counts toward the entry budget, including hidden and
    rejected ones, but rejected directories are not entered. A subdirectory
    that cannot be scanned counts as one unreadable item.
    """
    notes: list[str] = []
    pending = [directory.relative_path]
    inspected = 0
    while pending:
        parent = pending.pop()
        try:
            with os.scandir(policy.root / parent if parent else policy.root) as scan:
                for entry in scan:
                    inspected += 1
                    if inspected > limits.max_search_entries:
                        raise KnowledgeError(DomainErrorCode.SEARCH_LIMIT_EXCEEDED)
                    checkpoint()
                    child = policy.visible_child(parent, entry.name)
                    if child is None:
                        continue
                    if child.kind is TargetKind.DIRECTORY:
                        pending.append(child.relative_path)
                    else:
                        notes.append(child.relative_path)
        except OSError as error:
            if parent != directory.relative_path:
                corpus.skipped["unreadable"] += 1
            elif isinstance(error, FileNotFoundError):
                raise KnowledgeError(DomainErrorCode.NOT_FOUND) from None
            else:
                raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
    return sorted(notes)


def _append_note(corpus: _Corpus, relative_path: str, text: str) -> None:
    """Add a note's text to the stream and record where it sits."""
    if not text:
        return
    data = text.encode()
    if not data.endswith(b"\n"):
        data += b"\n"
    first_line = (
        corpus.spans[-1].first_line + corpus.spans[-1].line_count if corpus.spans else 1
    )
    start = len(corpus.stream)
    corpus.stream += data
    corpus.spans.append(
        _NoteSpan(
            relative_path, first_line, data.count(b"\n"), start, len(corpus.stream)
        )
    )
    corpus.first_lines.append(first_line)


# Match stage


async def _run_ripgrep(
    ripgrep: str, request: SearchRequest, corpus: _Corpus, limits: Limits
) -> list[dict[str, Any]]:
    """Run ripgrep on the corpus and return its match records.

    Returns at most `request.max_results + 1` records, in stream order.
    """
    if not corpus.spans:
        return []
    args = [
        ripgrep,
        "--no-config",
        "--json",
        "--fixed-strings",
        "--encoding",
        "none",
        "--case-sensitive" if request.case_sensitive else "--ignore-case",
        "--max-count",
        str(request.max_results + 1),
        "-e",
        request.query,
        "-",
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_RIPGREP_ENV,
            cwd=_RIPGREP_CWD,
        )
    except OSError:
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED) from None

    try:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)
        budget = _OutputBudget(limits.max_search_output_bytes)
        parser = _RecordParser(request.max_results + 1)
        tasks = [
            asyncio.create_task(_write_input(process.stdin, corpus.stream)),
            asyncio.create_task(_read_output(process.stdout, budget, parser.feed)),
            asyncio.create_task(_read_output(process.stderr, budget, None)),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        parser.finish()
        returncode = await process.wait()
    finally:
        await _kill_and_reap(process)

    # ripgrep exits with 0 when it found matches and 1 when it found none.
    # Anything else, including a signal, is a failure.
    if (returncode, bool(parser.matches)) not in {(0, True), (1, False)}:
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)
    return parser.matches


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """Kill ripgrep if it is still running and wait until it has exited.

    A cancellation that arrives during the wait is deferred until the process
    has been reaped and is then raised. The wait is short because SIGKILL
    cannot be caught or ignored; only the kernel can delay the exit.
    """
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    wait = asyncio.ensure_future(process.wait())
    cancelled = False
    while not wait.done():
        try:
            await asyncio.shield(wait)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


async def _write_input(stdin: asyncio.StreamWriter, stream: bytearray) -> None:
    """Send the stream, tolerating ripgrep closing its input early.

    ripgrep stops reading once `--max-count` is reached, so a broken pipe is
    expected; the exit status decides whether the search succeeded.
    """
    view = memoryview(stream)
    try:
        for offset in range(0, len(view), _WRITE_CHUNK_BYTES):
            stdin.write(view[offset : offset + _WRITE_CHUNK_BYTES])
            await stdin.drain()
        stdin.close()
        await stdin.wait_closed()
    except BrokenPipeError, ConnectionResetError:
        pass
    finally:
        view.release()


class _OutputBudget:
    """Count ripgrep's output bytes, stdout and stderr together."""

    def __init__(self, limit: int) -> None:
        self._remaining = limit

    def charge(self, size: int) -> None:
        self._remaining -= size
        if self._remaining < 0:
            raise KnowledgeError(DomainErrorCode.SEARCH_LIMIT_EXCEEDED)


async def _read_output(
    reader: asyncio.StreamReader,
    budget: _OutputBudget,
    sink: Callable[[bytes], None] | None,
) -> None:
    """Read a pipe in fixed-size chunks, charging each chunk to the budget.

    Reading chunks instead of lines keeps one long record from being buffered
    before the budget is checked.
    """
    while chunk := await reader.read(_READ_CHUNK_BYTES):
        budget.charge(len(chunk))
        if sink is not None:
            sink(chunk)


class _RecordParser:
    """Split ripgrep's JSON Lines output and keep the match records."""

    def __init__(self, max_matches: int) -> None:
        self.matches: list[dict[str, Any]] = []
        self._max_matches = max_matches
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        end = self._buffer.rfind(b"\n")
        if end < 0:
            return
        # Split on LF bytes only: records can contain raw U+2028 or U+0085,
        # which str.splitlines() would treat as line breaks.
        for line in bytes(self._buffer[:end]).split(b"\n"):
            self._parse(line)
        del self._buffer[: end + 1]

    def finish(self) -> None:
        if self._buffer:
            raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)

    def _parse(self, line: bytes) -> None:
        try:
            record = json.loads(line)
        except ValueError:
            raise KnowledgeError(DomainErrorCode.SEARCH_FAILED) from None
        if not isinstance(record, dict):
            raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)
        kind = record.get("type")
        data = record.get("data")
        if kind == "match":
            if not isinstance(data, dict):
                raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)
            if len(self.matches) < self._max_matches:
                self.matches.append(data)
        elif kind == "end":
            # ripgrep's binary handling treats NUL as a line break, which
            # would change line numbers. The loader rejects NUL bytes, so a
            # reported binary offset means the output cannot be trusted.
            if not isinstance(data, dict) or data.get("binary_offset") is not None:
                raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)


def _to_match(record: dict[str, Any], corpus: _Corpus, limits: Limits) -> SearchMatch:
    """Map a match record to a note and line, after checking it against the input.

    The record must describe a line of the stream exactly as it was sent:
    its text, its byte offset, and its line number must all agree.
    """
    try:
        line_number = record["line_number"]
        offset = record["absolute_offset"]
        text = record["lines"]["text"]
        start = record["submatches"][0]["start"]
        end = record["submatches"][0]["end"]
    except KeyError, IndexError, TypeError:
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED) from None
    if not (
        all(type(value) is int for value in (line_number, offset, start, end))
        and isinstance(text, str)
    ):
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)

    index = bisect.bisect_right(corpus.first_lines, line_number) - 1
    if index < 0:
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)
    span = corpus.spans[index]
    line_bytes = text.encode()
    stream = corpus.stream
    if not (
        line_number < span.first_line + span.line_count
        and span.start <= offset < span.end
        and line_bytes.endswith(b"\n")
        and stream[offset : offset + len(line_bytes)] == line_bytes
        and (offset == span.start or stream[offset - 1] == ord("\n"))
        and span.first_line + stream.count(b"\n", span.start, offset) == line_number
    ):
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)

    content = line_bytes[:-1]
    if not 0 <= start < end <= len(content):
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED)
    try:
        match_start = len(content[:start].decode())
        match_length = len(content[start:end].decode())
    except UnicodeDecodeError:
        raise KnowledgeError(DomainErrorCode.SEARCH_FAILED) from None
    snippet, truncated = _snippet_window(
        text[:-1], match_start, match_length, limits.max_snippet_length
    )
    return SearchMatch(
        path=span.path,
        line=line_number - span.first_line + 1,
        snippet=snippet,
        snippet_truncated=truncated,
    )


def _snippet_window(
    line: str, match_start: int, match_length: int, limit: int
) -> tuple[str, bool]:
    """Cut the contract's snippet window from a line, in code points.

    A match that fits is shown whole with the remaining space split as evenly
    as possible, the odd code point after it; the window then slides to stay
    inside the line. A longer match shows its first `limit` code points.

    Returns:
        The snippet and whether it is shorter than the line.
    """
    if len(line) <= limit:
        return line, False
    if match_length <= limit:
        first = match_start - (limit - match_length) // 2
        first = min(max(first, 0), len(line) - limit)
    else:
        first = match_start
    return line[first : first + limit], True
