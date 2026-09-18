#!/usr/bin/env python3
"""
cfs -- a persistent filesystem for Claude, backed by a scoped Dropbox app folder.

Guardrails are enforced structurally, not by convention:

  * Every mutation of an existing file requires the file's current ``rev``.
    The rev is only obtainable by reading the file, so "you must read before
    you write" is enforced by the fact that you cannot name the rev otherwise.
  * Dropbox performs the compare-and-swap server-side (``mode=update`` +
    ``strict_conflict``), so a stale rev is rejected even if two conversations
    race. No local state is consulted, and none is trusted.
  * ``edit`` refuses ambiguous matches: old_str must appear exactly once.

Stdlib only -- the sandbox has no package installation.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

API = "https://api.dropboxapi.com"
CONTENT = "https://content.dropboxapi.com"

CREDS_ENV = "CFS_CREDENTIALS"
DEFAULT_CREDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "credentials.json")
TOKEN_CACHE = os.path.join(tempfile.gettempdir(), ".cfs-token.json")

MAX_VIEW_CHARS = 16000


class CfsError(Exception):
    """An error we want reported to Claude as a clean message, not a traceback.

    ``stdout`` carries a payload that belongs on stdout rather than inside the
    message -- currently the diff attached to a stale-rev rejection. It rides the
    stream every other rev disclosure uses, so a harness that truncates stderr
    cannot leave the caller holding a rev without the content licensing it.
    """

    def __init__(self, message: str, stdout: str | None = None):
        super().__init__(message)
        self.stdout = stdout


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


def load_credentials() -> dict:
    path = os.environ.get(CREDS_ENV, DEFAULT_CREDS)
    if not os.path.exists(path):
        raise CfsError(
            f"No credentials at {path}. Expected a JSON file with app_key, "
            f"app_secret and refresh_token (see bootstrap_auth.py)."
        )
    with open(path, encoding="utf-8") as fh:
        creds = json.load(fh)
    for key in ("app_key", "app_secret", "refresh_token"):
        if not creds.get(key):
            raise CfsError(f"Credentials at {path} are missing '{key}'.")
    return creds


def access_token() -> str:
    """Mint (or reuse) a short-lived access token from the long-lived refresh token."""
    creds = load_credentials()
    # Fingerprint the refresh token so that rotating credentials -- or changing
    # the app's granted scopes, which requires re-authorising -- invalidates the
    # cache. Without this, a cached token silently outlives the grant it came
    # from and every call fails with a missing_scope that the credentials on
    # disk do not explain.
    fingerprint = hashlib.sha256(creds["refresh_token"].encode()).hexdigest()[:16]

    try:
        with open(TOKEN_CACHE, encoding="utf-8") as fh:
            cached = json.load(fh)
        if (
            cached.get("fingerprint") == fingerprint
            and cached.get("expires_at", 0) > time.time() + 60
        ):
            return cached["access_token"]
    except (OSError, ValueError, KeyError):
        pass

    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]}
    ).encode()
    basic = base64.b64encode(
        f"{creds['app_key']}:{creds['app_secret']}".encode()
    ).decode()
    req = urllib.request.Request(
        f"{API}/oauth2/token",
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise CfsError(f"Could not refresh access token ({exc.code}): {detail}") from exc

    token = payload["access_token"]
    try:
        with open(TOKEN_CACHE, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "access_token": token,
                    "expires_at": time.time() + payload.get("expires_in", 14400),
                    "fingerprint": fingerprint,
                },
                fh,
            )
        os.chmod(TOKEN_CACHE, 0o600)
    except OSError:
        pass  # cache is an optimisation; failing to write it is not fatal
    return token


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def rpc(endpoint: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{API}{endpoint}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/json",
        },
    )
    return _send(req)


def content_upload(payload: dict, data: bytes) -> dict:
    req = urllib.request.Request(
        f"{CONTENT}/2/files/upload",
        data=data,
        headers={
            "Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": _api_arg(payload),
        },
    )
    return _send(req)


def content_download(payload: dict) -> tuple[bytes, dict]:
    req = urllib.request.Request(
        f"{CONTENT}/2/files/download",
        data=b"",
        headers={
            "Authorization": f"Bearer {access_token()}",
            "Dropbox-API-Arg": _api_arg(payload),
        },
    )
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read(), json.loads(resp.headers["Dropbox-API-Result"])
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            delay = _retry_after(exc, body, attempt)
            if delay is None:
                raise _translate(exc, body) from exc
            time.sleep(delay)
    raise CfsError("Unreachable")  # pragma: no cover


def _api_arg(payload: dict) -> str:
    """Dropbox-API-Arg must be HTTP-header-safe: escape non-ASCII."""
    return json.dumps(payload, ensure_ascii=True)


MAX_RETRIES = 5


def _retry_after(exc: urllib.error.HTTPError, body: str, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None if this is not retryable.

    Dropbox serialises writes per namespace and rejects concurrent ones with
    too_many_write_operations. The request is refused, not partially applied, so
    retrying is safe -- and it is the only fix that works, since the contention
    can come from another session or device that no local lock could see.
    """
    retryable = exc.code in (429, 503) or "too_many_" in body or "rate_limit" in body
    if not retryable or attempt >= MAX_RETRIES:
        return None

    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return min(float(header), 30.0)
        except ValueError:
            pass
    # Dropbox reports retry_after at the top level on some responses and inside
    # the error object on others; accept either rather than betting on one.
    try:
        parsed = json.loads(body)
        for hint in (parsed.get("retry_after"), parsed.get("error", {}).get("retry_after")):
            if hint is not None:
                return min(float(hint), 30.0)
    except (ValueError, AttributeError, TypeError):
        pass
    # Exponential backoff with jitter, so parallel callers do not resynchronise
    # onto the same retry instant and collide again.
    return min(2.0**attempt, 16.0) * (0.5 + random.random())


def _send(req: urllib.request.Request) -> dict:
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            delay = _retry_after(exc, body, attempt)
            if delay is None:
                raise _translate(exc, body) from exc
            time.sleep(delay)
    raise CfsError("Unreachable")  # pragma: no cover


def _translate(exc: urllib.error.HTTPError, body: str) -> CfsError:
    """Turn a Dropbox error body into something actionable."""
    try:
        tag = json.loads(body).get("error", {})
    except ValueError:
        tag = {}
    summary = json.dumps(tag) if tag else body.strip()

    if "conflict" in summary:
        return CfsError(
            "Write rejected: the file changed since you read it (rev is stale). "
            "Re-read the file, re-apply your change to the current content, and retry.\n"
            f"Dropbox said: {summary}"
        )
    if "not_found" in summary:
        return CfsError(f"Path does not exist.\nDropbox said: {summary}")
    if exc.code == 401:
        return CfsError(f"Dropbox rejected the credentials (401): {summary}")
    return CfsError(f"Dropbox API error {exc.code}: {summary}")


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def normalise(path: str) -> str:
    """Reject traversal and normalise to a Dropbox-style absolute path.

    The app folder is already the security boundary; this is defence in depth
    plus a guard against typos that would silently address the wrong file.
    """
    if not path.startswith("/"):
        path = "/" + path
    path = urllib.parse.unquote(path)
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise CfsError(f"Refusing path containing '..': {path}")
        parts.append(part)
    return "/" + "/".join(parts)


def api_path(path: str) -> str:
    """Dropbox names the app-folder root as the empty string, not '/'."""
    norm = normalise(path)
    return "" if norm == "/" else norm


def check_rev_belongs(meta: dict, path: str, rev: str) -> None:
    """Refuse a rev that names a different file.

    Dropbox resolves `rev:<id>` globally, so a rev belonging to another file
    downloads happily. The mistake is likeliest exactly when the caller has lost
    track of which rev it holds, so it has to be named rather than served.
    """
    actual = meta.get("path_display")
    if actual and normalise(actual).lower() != normalise(path).lower():
        raise CfsError(
            f"rev {rev} belongs to {actual}, not {normalise(path)}. Nothing was "
            "read. Read the file you meant and use the rev it reports."
        )


MAX_STALE_REV_ATTEMPTS = 5


def _rev_if_content_unchanged(path: str, held_rev: str) -> str | None:
    """If `path` is byte-identical now to what `held_rev` held (an X->Y->X rev
    bump), return the current rev to retry with. None otherwise -- content
    that actually differs needs the real rejection, since a diff might change
    what the caller writes; so does a held_rev that can't even be fetched
    (invented, expired, foreign), which the caller's stale_rev_error already
    handles.
    """
    try:
        held_data, held_meta = content_download({"path": f"rev:{held_rev}"})
        check_rev_belongs(held_meta, path, held_rev)
    except CfsError:
        return None
    current_data, current_meta = content_download({"path": api_path(path)})
    if current_data != held_data:
        return None
    return current_meta["rev"]


# --------------------------------------------------------------------------
# argument values: literal, @file, or - for stdin
# --------------------------------------------------------------------------


def read_payload(required: tuple[str, ...], example: str) -> dict:
    """Read a JSON object from stdin.

    Strings arrive as JSON rather than as shell arguments deliberately. It keeps
    multi-line content free of shell quoting hazards, and -- for `edit` -- it
    means old_str must be produced inline in the command. There is intentionally
    no way to read old_str from a file: that would let the bytes be extracted
    mechanically (sed, grep) so that the edit never demonstrates knowledge of
    what it is changing, which is the entire point of matching on old_str.
    """
    if sys.stdin.isatty():
        raise CfsError(
            "This command expects a JSON object on stdin. Use a quoted heredoc so "
            f"the shell does not interpret the content:\n\n{example}"
        )
    return parse_payload(sys.stdin.read(), required, example)


def parse_payload(raw: str, required: tuple[str, ...], example: str) -> dict:
    if not raw.strip():
        raise CfsError(f"Empty stdin; expected a JSON object.\n\n{example}")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise CfsError(
            f"Could not parse stdin as JSON: {exc}. Newlines inside strings must be "
            f"escaped as \\n.\n\n{example}"
        ) from exc
    if not isinstance(payload, dict):
        raise CfsError(f"Expected a JSON object, got {type(payload).__name__}.")
    missing = [key for key in required if key not in payload]
    if missing:
        raise CfsError(f"Missing key(s) in JSON payload: {', '.join(missing)}.\n\n{example}")
    for key in required:
        if not isinstance(payload[key], str):
            raise CfsError(f"'{key}' must be a string.")
    return payload


def _stdin_source_file() -> str | None:
    """Best-effort: was stdin redirected from a named file rather than a heredoc?

    bash backs heredocs with temp files too, but names them distinctively, so
    this is a heuristic and only ever warns. It exists because routing text
    through a scratch file is a real failure mode: it does not fix heredoc
    syntax errors, it hides them, and the agent then attributes the fix to the
    wrong cause.
    """
    try:
        if not stat.S_ISREG(os.fstat(0).st_mode):
            return None  # a pipe or tty, so not a file redirect
        target = os.readlink("/proc/self/fd/0")
    except (OSError, AttributeError, ValueError):
        return None  # not Linux, or /proc unavailable: stay silent
    name = os.path.basename(target)
    if name.startswith(("sh-thd", "sh-np", "sh-he")):
        return None  # a shell heredoc temp file
    return target


def warn_if_piped_from_file(command: str) -> None:
    source = _stdin_source_file()
    if source:
        print(
            f"Warning: {command} read its text from the file {source} rather than "
            "from an inline heredoc. Write the strings inline in the command "
            "instead. Routing text through a scratch file does not fix heredoc "
            "mistakes, it conceals them -- and for a whole-file replacement from "
            "a file on disk, `cfs upload` is the command you want.",
            file=sys.stderr,
        )


def warn_if_json_envelope(content: str) -> None:
    """Warn when raw content is exactly the JSON envelope write used to require.

    Unwrapping it instead would corrupt every real JSON file in the store, since
    file content legitimately starts with '{'. So write what was asked, and say
    so rather than letting the old habit fail silently.
    """
    if not content.lstrip().startswith("{"):
        return
    try:
        parsed = json.loads(content)
    except ValueError:
        return
    if isinstance(parsed, dict) and set(parsed) == {"content"}:
        print(
            "Warning: stdin was a JSON object whose only key is 'content', and it "
            "has been written verbatim -- braces, quotes and escapes included. "
            "write takes RAW stdin; pass --json if you meant that object to be "
            "unwrapped.",
            file=sys.stderr,
        )


def read_stdin_raw(what: str) -> str:
    """Read stdin verbatim -- no escaping, no interpretation.

    A quoted heredoc is a transparent layer: it transforms nothing on the way
    through. JSON on stdin is not, and nesting the two means holding two
    rulesets that contradict each other on the character markdown is made of --
    the heredoc invites literal newlines, the JSON inside rejects them.
    """
    if sys.stdin.isatty():
        raise CfsError(
            f"Expected {what} on stdin. Use a quoted heredoc so the shell leaves "
            "the content alone:\n\n" + STDIN_EXAMPLE
        )
    return sys.stdin.read()


SR_START = "<<<<<<< SEARCH"
SR_DIVIDER = "======="
SR_END = ">>>>>>> REPLACE"

SR_EXAMPLE = f"""\
  cfs edit /memory/notes.md --rev 0165932a <<'EOF'
  {SR_START}
  - Hotel: unbooked
  {SR_DIVIDER}
  - Hotel: booked 3 Mar
  {SR_END}
  EOF"""


def markers_for(tag: str | None) -> tuple[str, str, str]:
    if not tag:
        return SR_START, SR_DIVIDER, SR_END
    return f"{SR_START} {tag}", f"{SR_DIVIDER} {tag}", f"{SR_END} {tag}"


def looks_like_marker(line: str, tag: str | None) -> str | None:
    """Which marker, if any, this line is. Strict on the divider by design.

    The divider is matched exactly (seven '=' and nothing else, plus the tag),
    because markdown setext headings are underlined with '=' and a loose match
    would split blocks on ordinary headings.
    """
    start, divider, end = markers_for(tag)
    if tag:
        stripped = line.strip()
        if stripped == start:
            return "start"
        if stripped == divider:
            return "divider"
        if stripped == end:
            return "end"
        return None
    if line.startswith("<<<<<<<"):
        return "start"
    if line.strip() == SR_DIVIDER:
        return "divider"
    if line.startswith(">>>>>>>"):
        return "end"
    return None


def file_has_marker_lines(text: str) -> bool:
    """Does the target file itself contain lines we would parse as markers?"""
    return any(looks_like_marker(line, None) for line in text.split("\n"))


class EditBlock:
    """One old -> new replacement, or the syntax error that stopped it parsing."""

    def __init__(self, old: str = "", new: str = "", error: str | None = None):
        self.old = old
        self.new = new
        self.error = error


def parse_search_replace(raw: str, tag: str | None = None) -> list[EditBlock]:
    """Parse one or more SEARCH/REPLACE blocks from raw stdin.

    Three distinct markers rather than one repeated separator, because a single
    symmetric delimiter collides with a strong prior: every delimiter-shaped
    token is either self-closing or a bracket, so agents reliably emit the
    separator a second time to "close" the block. Here the closing instinct is
    satisfied by a marker that is not the divider, so it cannot be misspent.

    A malformed block records its error and parsing resumes at the next opening
    marker, so one report covers every block.
    """
    lines = raw.split("\n")
    start_m, divider_m, end_m = markers_for(tag)

    def kind(j: int) -> str | None:
        return looks_like_marker(lines[j], tag)

    blocks: list[EditBlock] = []
    i = 0
    while i < len(lines):
        if kind(i) != "start":
            i += 1
            continue

        block = EditBlock()
        blocks.append(block)
        where = f"The block starting on line {i + 1}"
        i += 1
        old: list[str] = []
        while i < len(lines) and kind(i) is None:
            old.append(lines[i])
            i += 1
        if i >= len(lines):
            block.error = (
                f"{where} has no {divider_m!r} line, so there is nothing "
                "separating old from new."
            )
            break
        if kind(i) == "end":
            block.error = (
                f"{where} reached {end_m!r} without a {divider_m!r} line "
                "separating the old text from the new one."
            )
            i += 1
            continue
        if kind(i) == "start":
            block.error = (
                f"{where} has no {divider_m!r} line before line {i + 1} opens "
                "another block."
            )
            continue

        i += 1
        new: list[str] = []
        while i < len(lines) and kind(i) is None:
            new.append(lines[i])
            i += 1
        if i >= len(lines):
            block.error = f"{where} was never closed. End it with a {end_m!r} line."
            break
        if kind(i) == "divider":
            block.error = (
                f"{where} has a second {divider_m!r} line at line {i + 1}. Each "
                f"block takes exactly one divider, then closes with {end_m!r} -- "
                "do not repeat the divider to close it."
            )
            while i < len(lines) and kind(i) != "start":
                i += 1
            continue
        if kind(i) == "start":
            block.error = (
                f"{where} was never closed: line {i + 1} opens another one. "
                f"Close each block with {end_m!r}."
            )
            continue
        i += 1
        block.old, block.new = "\n".join(old), "\n".join(new)

    if not blocks:
        example = SR_EXAMPLE if not tag else tagged_example(tag)
        raise CfsError(
            f"No SEARCH/REPLACE block found on stdin. Expected a line {start_m!r}. "
            f"Wrap the old and new text like this:\n\n{example}"
        )
    return blocks


def tagged_example(tag: str) -> str:
    start, divider, end = markers_for(tag)
    return (
        f"  cfs edit /memory/notes.md --rev 0165932a --tag {tag} <<'EOF'\n"
        f"  {start}\n  - Hotel: unbooked\n  {divider}\n  - Hotel: booked 3 Mar\n"
        f"  {end}\n  EOF"
    )


def read_delimited(delim: str) -> tuple[str, str]:
    """Split raw stdin into old/new on a caller-chosen marker line.

    The marker is chosen by the caller precisely so collisions are avoidable,
    and it is validated so they are also detectable: failing loudly with "pick
    another delimiter" beats a missed escape failing at a byte offset.
    """
    if not delim:
        raise CfsError(
            "--delim must not be empty: a blank marker matches the empty final "
            "line every heredoc produces, so it can never be unique."
        )
    raw = read_stdin_raw("an old/new payload")
    lines = raw.split("\n")
    hits = [i for i, line in enumerate(lines) if line == delim]

    if not hits:
        raise CfsError(
            f"Delimiter {delim!r} not found. The payload needs one line consisting "
            f"of exactly {delim!r}, separating the old text from the new."
        )
    if len(hits) > 1:
        rows = ", ".join(str(i + 1) for i in hits)
        raise CfsError(
            f"Delimiter {delim!r} appears {len(hits)} times (lines {rows}); it must "
            "appear exactly once. Pass --delim with a marker that does not occur "
            "in your content."
        )

    cut = hits[0]
    old = "\n".join(lines[:cut])
    new = "\n".join(lines[cut + 1 :])
    # A heredoc terminates its last line with a newline that is not part of the
    # fragment being matched, so drop exactly one. (write --stdin keeps its
    # trailing newline: there the content IS the file, and files end with one.)
    if new.endswith("\n"):
        new = new[:-1]
    return old, new


STDIN_EXAMPLE = """\
  cfs write /memory/notes.md --new <<'EOF'
  # Notes

  Real newlines, "quotes", $vars and \\backslashes all pass through untouched.
  EOF"""

DELIM_EXAMPLE = """\
  cfs edit /memory/notes.md --rev 0165932a --delim @@ <<'EOF'
  - Hotel: unbooked
  @@
  - Hotel: booked 3 Mar
  EOF"""

EDIT_EXAMPLE = """\
  cfs edit /memory/notes.md --rev 0165932a <<'JSON'
  {"old_str": "- Hotel: unbooked", "new_str": "- Hotel: booked 3 Mar"}
  JSON"""

WRITE_EXAMPLE = """\
  cfs write /memory/notes.md --new <<'JSON'
  {"content": "# Notes\\n\\nFirst line.\\n"}
  JSON"""


def human_size(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.0f}B"


def numbered(text: str, start: int = 1) -> str:
    lines = text.split("\n")
    return "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(lines, start))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_list(args) -> str:
    entries: list[dict] = []
    # A non-recursive list_folder returns exactly the immediate children, which
    # is depth 1. Recursing regardless made `list / --depth 1` fetch the whole
    # store to show one level, since depth is filtered client-side.
    payload = {"path": api_path(args.path), "recursive": args.depth > 1}
    try:
        result = rpc("/2/files/list_folder", payload)
    except CfsError as exc:
        if "not_found" in str(exc):
            raise CfsError(f"The path {normalise(args.path)} does not exist.") from exc
        raise
    entries.extend(result["entries"])
    while result.get("has_more"):
        result = rpc("/2/files/list_folder/continue", {"cursor": result["cursor"]})
        entries.extend(result["entries"])

    root = normalise(args.path)
    depth_of_root = root.rstrip("/").count("/")
    kept = []
    for entry in entries:
        rel_depth = entry["path_display"].count("/") - depth_of_root
        if rel_depth <= args.depth:
            kept.append(entry)

    kept.sort(key=lambda e: e["path_display"].lower())
    lines = [f"Contents of {root} (depth {args.depth}):"]
    if not kept:
        lines.append("  (empty)")
    for entry in kept:
        if entry[".tag"] == "folder":
            lines.append(f"  {'dir':>7}\t{entry['path_display']}/")
        else:
            # No rev here on purpose: read is the only way to obtain one, which
            # is what makes the rev evidence that the file has been read.
            lines.append(f"  {human_size(entry['size']):>7}\t{entry['path_display']}")
    return "\n".join(lines)


def cmd_read(args) -> str:
    """Content first, rev last -- on purpose.

    A rev only means anything if it is evidence you saw the bytes it describes.
    Printing it up front lets `read ... | head -3` harvest a valid-looking rev
    while discarding the content it is supposed to certify -- the exactness
    check on write then protects against clobbering bytes you looked at, but
    not against an edit built on a stale memory of parts of the file that never
    reached you. Putting the rev after the content does not stop a determined
    `tail`, but it does mean the reflexive `head` shortcut yields no rev at all.
    """
    path = normalise(args.path)
    if args.rev:
        data, meta = content_download({"path": f"rev:{args.rev}"})
        check_rev_belongs(meta, path, args.rev)
    else:
        data, meta = content_download({"path": api_path(path)})
    text = data.decode("utf-8", "replace")
    # A single trailing newline terminates the last line; it does not start a
    # further, empty one. Without stripping it, split("\n") reports one line
    # too many on almost every file we write, which would make the "read all
    # N lines" warning below wrong on the common case rather than the edge case.
    display_text = text[:-1] if text.endswith("\n") else text
    all_lines = display_text.split("\n")
    total = len(all_lines)

    top = f"{path}  ({total} line(s))\n"

    # An unambiguous marker, not "---": memory files are markdown, where "---"
    # is legitimately YAML frontmatter or a horizontal rule. A separator that
    # can appear in real content is a separator that can mislabel where the
    # content actually ends.
    MARK = "[end of file content]"

    if args.rev:
        # Deliberately never discloses the current rev: this call proves you
        # have seen an old version, not the live one, so it must not license a
        # write.
        footer = (
            f"\n{MARK}\nThis is a historical revision (rev {args.rev}), not the "
            f"current file. It cannot be used to write -- read {path} without "
            "--rev for that, or use restore."
        )
    else:
        footer = (
            f"\n{MARK}\nrev: {meta['rev']}   (pass this rev to edit/write/delete -- "
            f"only valid if you read all {total} line(s) above, not a piped "
            "excerpt)"
        )

    if args.lines:
        try:
            start_s, end_s = args.lines.split("-", 1)
            start, end = int(start_s), int(end_s)
        except ValueError as exc:
            raise CfsError("--lines expects START-END, e.g. 1-40 or 20--1") from exc
        if end == -1:
            end = total
        selected = all_lines[max(start - 1, 0) : end]
        body = numbered("\n".join(selected), start=max(start, 1))
        if not args.rev:
            footer += (
                f"\nThis rev covers the WHOLE file, not just lines {start}-{end}. "
                "A partial view is fine for reading, but do not edit content "
                "outside the range you actually saw."
            )
        return top + body + footer

    if len(display_text) > MAX_VIEW_CHARS and not args.full:
        shown = display_text[:MAX_VIEW_CHARS]
        truncated_at = numbered(shown).count("\n") + 1
        footer += (
            f"\n[truncated after {truncated_at} of {total} lines ({MAX_VIEW_CHARS} "
            "chars); the rev above still describes the WHOLE file. Use --lines "
            "START-END to page through the rest, or --full, before editing "
            "anything past what you have actually read.]"
        )
        return top + numbered(shown) + footer

    return top + numbered(display_text) + footer


MEMORY_ROOT = "/memory"
PROTECTED = {MEMORY_ROOT}


def guard_protected(path: str, verb: str) -> None:
    """Refuse to destroy an area root.

    Losing /memory is a silent failure: nothing errors afterwards, memory simply
    stops loading and every later conversation starts blank. Individual entries
    are still freely deletable -- only the root is pinned.
    """
    if normalise(path) in PROTECTED:
        raise CfsError(
            f"Refusing to {verb} {normalise(path)}: it is the root of an area that "
            "other sessions rely on, and losing it fails silently rather than "
            "loudly. Remove entries inside it individually, or do this from the "
            "Dropbox UI if you really mean it."
        )


def upload_bytes(path: str, data: bytes, args, verb: str = "write") -> dict:
    """Upload with the right write mode, and diagnose an 'add' conflict correctly.

    Dropbox reports both failures as a 'conflict': a stale rev under mode=update,
    and an existing path under mode=add. They need opposite advice -- re-read
    versus pick another path -- so they must not share an error message.

    A stale rev with identical content retries against the current rev
    instead of failing (see ``_rev_if_content_unchanged``), bounded by
    MAX_STALE_REV_ATTEMPTS so sustained contention still fails loudly.
    """
    mode = write_mode(args, path)
    for _ in range(MAX_STALE_REV_ATTEMPTS):
        try:
            return content_upload(
                {
                    "path": api_path(path),
                    "mode": mode,
                    "autorename": False,
                    "strict_conflict": True,
                    "mute": True,
                },
                data,
            )
        except CfsError as exc:
            if args.new and "conflict" in str(exc):
                raise CfsError(
                    f"{path} already exists, so --new refused to create it. Nothing was "
                    "written. Read the file and pass --rev <rev> if you meant to "
                    "overwrite it, or choose a different path."
                ) from exc
            if args.rev and "conflict" in str(exc):
                upgraded = _rev_if_content_unchanged(path, args.rev)
                if upgraded is None:
                    raise stale_rev_error(path, args.rev, verb) from exc
                mode = {".tag": "update", "update": upgraded}
                continue
            raise
    raise CfsError(
        f"Gave up on {verb} to {path}: it keeps being rewritten to identical "
        "content faster than the write can land. Try again."
    )


def write_mode(args, path: str):
    """Shared by write and upload: 'add' for new files, CAS update otherwise."""
    if args.new:
        if args.rev:
            raise CfsError("--new and --rev are mutually exclusive.")
        return "add"
    if not args.rev:
        raise CfsError(
            f"Refusing to overwrite {path} without --rev. Read the file first and "
            "pass the rev it reports, or use --new if you intend to create a new file."
        )
    return {".tag": "update", "update": args.rev}


def cmd_write(args) -> str:
    path = normalise(args.path)
    if args.content is not None:
        if args.stdin or args.json:
            raise CfsError("--content and --stdin/--json are mutually exclusive.")
        content = args.content
    elif args.json:
        if args.stdin:
            raise CfsError("--stdin and --json are mutually exclusive.")
        content = read_payload(("content",), WRITE_EXAMPLE)["content"]
    else:
        # Raw by default, matching edit; --stdin is the older spelling of it.
        warn_if_piped_from_file("write")
        content = read_stdin_raw("the file content")
        warn_if_json_envelope(content)
    data = content.encode("utf-8")

    meta = upload_bytes(path, data, args)
    verb = "Created" if args.new else "Wrote"
    return f"{verb} {path} ({len(data)} bytes).\nnew rev: {meta['rev']}"


def cmd_edit(args) -> str:
    path = normalise(args.path)
    if not args.rev:
        raise CfsError(
            f"Refusing to edit {path} without --rev. Read the file first and pass "
            "the rev it reports."
        )

    warn_if_piped_from_file("edit")
    syntax_help = ""
    if args.delim:
        raw = None
        edits = [EditBlock(*read_delimited(args.delim))]
    else:
        raw = read_stdin_raw("a SEARCH/REPLACE block")
        edits = None  # parsed below, once the file is in hand for the guard

    data, meta = content_download({"path": api_path(path)})
    text = data.decode("utf-8", "replace")

    if edits is None:
        assert raw is not None
        if raw.lstrip().startswith(("{", "[")):
            edits = parse_json_edits(raw)
        else:
            # The guard: refuse rather than risk splitting a block on the file's
            # own content. Exact, not heuristic -- the bytes are right here.
            if not args.tag and file_has_marker_lines(text):
                raise CfsError(
                    f"{path} itself contains conflict-marker lines, so a plain "
                    "SEARCH/REPLACE block could be split on the file's own content "
                    "rather than on your markers. Nothing was written. Re-run with "
                    f"--tag to make the markers unambiguous:\n\n{tagged_example('@@X@@')}"
                )
            edits = parse_search_replace(raw, args.tag)
            syntax_help = SR_EXAMPLE if not args.tag else tagged_example(args.tag)

    rev = meta["rev"]
    if rev != args.rev:
        # Tolerate the stale rev if content hasn't actually moved (`text` above
        # is already current); otherwise the real rejection, diff included.
        upgraded = _rev_if_content_unchanged(path, args.rev)
        if upgraded is None:
            raise stale_rev_error(path, args.rev, "edit")
        rev = upgraded

    updated = apply_edits(text, edits, path, args.all, syntax_help)
    summary = f"Edited {path}." if len(edits) == 1 else f"Edited {path} ({len(edits)} blocks)."

    payload_bytes = updated.encode("utf-8")
    for _ in range(MAX_STALE_REV_ATTEMPTS):
        try:
            result = content_upload(
                {
                    "path": api_path(path),
                    "mode": {".tag": "update", "update": rev},
                    "autorename": False,
                    "strict_conflict": True,
                    "mute": True,
                },
                payload_bytes,
            )
            return f"{summary}\nnew rev: {result['rev']}"
        except CfsError as exc:
            # The check above races, so this can conflict too; same tolerance.
            if "conflict" not in str(exc):
                raise
            upgraded = _rev_if_content_unchanged(path, args.rev)
            if upgraded is None:
                raise stale_rev_error(path, args.rev, "edit") from exc
            rev = upgraded
    raise CfsError(
        f"Gave up editing {path}: it keeps being rewritten to identical "
        "content faster than the edit can land. Try again."
    )


def parse_json_edits(raw: str) -> list[EditBlock]:
    """One {old_str, new_str} object, or an array of them."""
    if raw.lstrip().startswith("{"):
        payload = parse_payload(raw, ("old_str", "new_str"), EDIT_EXAMPLE)
        return [EditBlock(payload["old_str"], payload["new_str"])]
    try:
        items = json.loads(raw)
    except ValueError as exc:
        raise CfsError(
            f"Could not parse stdin as JSON: {exc}. Newlines inside strings must be "
            f"escaped as \\n.\n\n{EDIT_EXAMPLE}"
        ) from exc
    if not isinstance(items, list) or not items:
        raise CfsError(f"Expected a non-empty JSON array of edits.\n\n{EDIT_EXAMPLE}")
    edits = []
    for n, item in enumerate(items, 1):
        if not (
            isinstance(item, dict)
            and isinstance(item.get("old_str"), str)
            and isinstance(item.get("new_str"), str)
        ):
            raise CfsError(
                f"Edit {n} of the JSON array must be an object with string "
                f"'old_str' and 'new_str'.\n\n{EDIT_EXAMPLE}"
            )
        edits.append(EditBlock(item["old_str"], item["new_str"]))
    return edits


def apply_edits(
    text: str,
    edits: list[EditBlock],
    path: str,
    replace_all: bool = False,
    syntax_help: str = "",
) -> str:
    """Apply every edit in order, each to the previous one's output, or none.

    All-or-nothing because edits in one call often depend on each other (a
    rename plus its references). Every edit is still attempted after a failure,
    so the refusal names every problem and one corrected retry fixes them all.
    """
    current = text
    outcomes: list[str] = []
    failures: list[str] = []

    def fail(error: str) -> None:
        failures.append(error)
        outcomes.append(f"FAILED. {error}")

    for edit in edits:
        if edit.error:
            fail(edit.error)
            continue
        if edit.old == edit.new:
            fail("The old and new text are identical; nothing to do.")
            continue
        count = current.count(edit.old)
        try:
            updated = apply_replacement(current, edit.old, edit.new, path, replace_all)
        except CfsError as exc:
            error = str(exc)
            if count == 0 and edit.old in text:
                error += (
                    "\n\nIt does appear in the file as read, so an earlier block in "
                    "this call changed or removed it. Blocks apply in order: match "
                    "against the text as the earlier blocks leave it."
                )
            fail(error)
            continue
        at = current.count("\n", 0, current.index(edit.old)) + 1
        outcomes.append(f"OK, matched line {at}" if count == 1 else f"OK, {count} matches")
        current = updated

    help_suffix = f"\n\n{syntax_help}" if syntax_help and any(e.error for e in edits) else ""
    if len(edits) == 1 and failures:
        raise CfsError(failures[0] + help_suffix)
    if failures:
        report = "\n".join(
            f"  block {n}: " + textwrap.indent(outcome, "      ").lstrip()
            for n, outcome in enumerate(outcomes, 1)
        )
        raise CfsError(
            f"{len(failures)} of {len(edits)} blocks failed. Nothing was written, "
            f"and your --rev is still current.\n\n{report}\n\nFix the failed blocks "
            f"and resend all {len(edits)} in one call, with the same --rev."
            + help_suffix
        )
    if current == text:
        raise CfsError("Replacement produced no change; nothing written.")
    return current


def _diagnose_no_match(text: str, old: str) -> str:
    """Explain *why* old_str missed, when the reason is boring.

    A failed match is usually a stray trailing space or an indentation
    difference, not a misremembered line. Saying which turns a retry-and-hope
    loop into a single corrected edit.
    """
    def strip_trailing(s: str) -> str:
        return "\n".join(line.rstrip() for line in s.split("\n"))

    def collapse(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    if strip_trailing(old) in strip_trailing(text):
        return (
            "\n\nThe text matches once trailing whitespace is ignored, so the file "
            "has trailing spaces your old_str does not (or vice versa). Re-read the "
            "file and copy the line exactly."
        )
    if collapse(old) and collapse(old) in collapse(text):
        return (
            "\n\nThe text matches once all whitespace is normalised, so the "
            "difference is indentation or line breaks. Re-read the file and copy "
            "the exact bytes, including leading whitespace."
        )

    first = old.split("\n", 1)[0].strip()
    if first:
        lines = text.split("\n")
        # Match on stripped lines so indentation cannot hide an obvious suggestion,
        # but report the original line so the exact bytes are visible.
        stripped = [line.strip() for line in lines]
        close = difflib.get_close_matches(first, stripped, n=1, cutoff=0.7)
        if close:
            idx = stripped.index(close[0])
            return (
                f"\n\nThe closest line in the file is line {idx + 1}:\n"
                f"  {lines[idx]!r}\nagainst your first line:\n  {first!r}"
            )
    return ""


def apply_replacement(
    text: str, old: str, new: str, path: str, replace_all: bool = False
) -> str:
    """Replace old with new, refusing anything but an unambiguous single match.

    replace_all lifts the uniqueness requirement, which is safe only because the
    caller has asked for every occurrence explicitly.
    """
    count = text.count(old)
    if count == 0:
        raise CfsError(
            f"No replacement performed: old_str did not appear verbatim in {path}."
            + _diagnose_no_match(text, old)
        )
    if replace_all:
        return text.replace(old, new)
    if count > 1:
        positions = []
        offset = 0
        for _ in range(count):
            offset = text.index(old, offset)
            positions.append(text.count("\n", 0, offset) + 1)
            offset += 1
        raise CfsError(
            f"No replacement performed: old_str appears {count} times in {path} "
            f"(lines {', '.join(map(str, positions))}). Include more surrounding "
            "context to make it unique, or pass --all to replace every occurrence."
        )
    return text.replace(old, new, 1)


def cmd_delete(args) -> str:
    path = normalise(args.path)
    if path == "/":
        raise CfsError("Refusing to delete the filesystem root.")
    guard_protected(path, "delete")
    payload: dict = {"path": api_path(path)}
    if args.rev:
        payload["parent_rev"] = args.rev
    elif not args.force:
        raise CfsError(
            f"Refusing to delete {path} without --rev (read it first) or --force "
            "(required for directories, which have no rev)."
        )
    try:
        rpc("/2/files/delete_v2", payload)
    except CfsError as exc:
        if args.rev and "conflict" in str(exc):
            raise stale_rev_error(path, args.rev, "delete") from exc
        raise
    return f"Deleted {path}"


def cmd_rename(args) -> str:
    old, new = normalise(args.old_path), normalise(args.new_path)
    if old == "/" or new == "/":
        raise CfsError("Refusing to rename the filesystem root.")
    guard_protected(old, "rename")
    rpc(
        "/2/files/move_v2",
        {"from_path": api_path(old), "to_path": api_path(new), "autorename": False},
    )
    return f"Renamed {old} -> {new}"


def cmd_copy(args) -> str:
    src, dst = normalise(args.src), normalise(args.dst)
    rpc(
        "/2/files/copy_v2",
        {"from_path": api_path(src), "to_path": api_path(dst), "autorename": False},
    )
    return f"Copied {src} -> {dst}"


def cmd_search(args) -> str:
    options: dict = {"max_results": args.limit, "filename_only": args.names_only}
    if args.path:
        options["path"] = api_path(args.path)
    result = rpc("/2/files/search_v2", {"query": args.query, "options": options})

    matches = result.get("matches", [])
    if not matches:
        return (
            f"No matches for {args.query!r}. Note that Dropbox indexes content "
            "asynchronously, so a file written moments ago may not be searchable yet."
        )

    lines = [f"{len(matches)} match(es) for {args.query!r}:"]
    for match in matches:
        meta = match.get("metadata", {}).get("metadata", {})
        if meta.get("path_display"):
            lines.append(f"  {meta['path_display']}")  # no rev; see cmd_list
    if result.get("has_more"):
        lines.append("  ... more results truncated; narrow the query or raise --limit.")
    return "\n".join(lines)


BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip",
    ".gz", ".tar", ".mp3", ".mp4", ".mov", ".wav", ".bin", ".woff", ".woff2",
}

# `grep` is GNU grep, not an imitation of it: the store is mirrored to a temp
# directory and the real binary runs over it. Everything below is plumbing that
# carries store paths across that boundary and back.
#
# Rewriting a path operand means knowing which argv tokens are operands -- in
# `grep -e /memory -r /memory` only grep's option table separates the pattern
# from the path. That table is finite and long stable, so it is enumerated.
# Anything unlisted is assumed to take no value; when that is wrong the value
# is mistaken for a path, fails to exist, and is reported by name. Never
# silent.
GREP_SHORT_WITH_ARG = set("ABCDdefm")
GREP_SHORT_NO_ARG = set("EFGPiyvwxcLloqsbnHhTZzaIrRUuV")
GREP_LONG_WITH_ARG = {
    "--after-context", "--before-context", "--context", "--binary-files",
    "--devices", "--directories", "--exclude", "--exclude-from",
    "--exclude-dir", "--file", "--group-separator", "--include", "--label",
    "--max-count", "--regexp",
}
GREP_LONG_NO_ARG = {
    "--extended-regexp", "--fixed-strings", "--basic-regexp", "--perl-regexp",
    "--ignore-case", "--no-ignore-case", "--invert-match", "--word-regexp",
    "--line-regexp", "--count", "--files-with-matches", "--files-without-match",
    "--only-matching", "--quiet", "--silent", "--no-messages", "--byte-offset",
    "--line-number", "--line-buffered", "--with-filename", "--no-filename",
    "--initial-tab", "--null", "--null-data", "--recursive", "--binary",
    "--dereference-recursive", "--text", "--unix-byte-offsets",
    "--color", "--colour", "--version", "--help",
}
# These supply the pattern themselves, so every operand is a path.
GREP_SHORT_PATTERN_OPTS = set("ef")
GREP_LONG_PATTERN_OPTS = {"--regexp", "--file"}
GREP_LONG_RECURSIVE = {"--recursive", "--dereference-recursive"}
GREP_SHORT_RECURSIVE = set("rR")
GREP_DIRECTORIES_OPTS = {"-d", "--directories"}
GREP_SHORT_QUIET = set("q")
GREP_LONG_QUIET = {"--quiet", "--silent"}
# Their values match names in the mirror, which holds Dropbox's lowercased paths.
GREP_GLOB_OPTS = {"--include", "--exclude", "--exclude-dir"}


class GrepArgv:
    """grep's argv, split into options and operands.

    Operands are held as indices rather than values because the caller rewrites
    them in place. That is also what keeps `-f` and `--exclude-from` reading
    from the sandbox rather than the store: their values are option values, so
    they are never operands and never rewritten.
    """

    def __init__(self, argv: list[str]):
        self.argv = list(argv)
        self.operands: list[int] = []
        self.unknown: list[str] = []
        # (option, argv index, offset of the value within that token)
        self.values: list[tuple[str, int, int]] = []
        self.pattern_is_an_option = False
        self.recursive = False
        self.quiet = False
        self._split()

    def _split(self) -> None:
        argv = self.argv
        i = 0
        while i < len(argv):
            token = argv[i]
            if token == "--":
                self.operands.extend(range(i + 1, len(argv)))
                return
            if token.startswith("--") and len(token) > 2:
                name = token.split("=", 1)[0]
                self.pattern_is_an_option |= name in GREP_LONG_PATTERN_OPTS
                self.recursive |= name in GREP_LONG_RECURSIVE
                self.quiet |= name in GREP_LONG_QUIET
                if name in GREP_LONG_WITH_ARG:
                    if "=" in token:
                        self._value(name, i, len(name) + 1)
                    else:
                        self._value(name, i + 1, 0)
                        i += 1
                elif name not in GREP_LONG_NO_ARG:
                    self.unknown.append(name)
            elif token.startswith("-") and len(token) > 1:
                if not token[1:].isdigit():  # -NUM is self-contained: -5 is -C 5
                    i += self._split_cluster(token, i)
            else:
                self.operands.append(i)  # including "-", which is stdin
            i += 1

    def _split_cluster(self, token: str, i: int) -> int:
        """Consume one bundle of short options; 1 if it also eats the next token."""
        for pos, char in enumerate(token[1:], 1):
            self.pattern_is_an_option |= char in GREP_SHORT_PATTERN_OPTS
            self.recursive |= char in GREP_SHORT_RECURSIVE
            self.quiet |= char in GREP_SHORT_QUIET
            if char in GREP_SHORT_WITH_ARG:
                # The rest of the cluster is the value, unless the cluster ends
                # here, in which case the value is the next token.
                if pos == len(token) - 1:
                    self._value("-" + char, i + 1, 0)
                    return 1
                self._value("-" + char, i, pos + 1)
                return 0
            if char not in GREP_SHORT_NO_ARG:
                self.unknown.append("-" + char)
        return 0

    def _value(self, option: str, index: int, offset: int) -> None:
        if index >= len(self.argv):
            return  # missing value; grep itself will say so
        self.values.append((option, index, offset))
        if option in GREP_DIRECTORIES_OPTS and self.argv[index][offset:] == "recurse":
            self.recursive = True

    def path_indices(self) -> list[int]:
        """Operands that are paths: all of them, unless the first is the pattern."""
        if self.pattern_is_an_option:
            return self.operands
        return self.operands[1:]

    def lowercase_globs(self) -> None:
        """Match the mirror's lowercase layout; globs become case-insensitive,
        as the store's names are."""
        for option, index, offset in self.values:
            if option in GREP_GLOB_OPTS:
                token = self.argv[index]
                self.argv[index] = token[:offset] + token[offset:].lower()


def mirror_root() -> str:
    return os.path.join(tempfile.gettempdir(), "cfs-mirror")


def manifest_file() -> str:
    """The rev manifest, kept outside the mirror -- anything inside it is corpus
    that grep -r would search and report as a file in the store."""
    return mirror_root() + "-revs.json"


def mirror_path(lower: str) -> str:
    """Dropbox's path_lower -> its location in the mirror.

    Lowercase because display casing is unreliable above the last component:
    two files in one folder can report that folder in different cases, which
    would split it in two locally. Forward slashes so grep echoes back a prefix
    that string replacement can strip on any platform.
    """
    return mirror_root() + ("/" if lower == "/" else lower)


MIRROR_WORKERS = 8
MANIFEST_CHECKPOINT_SECONDS = 2.0


class Mirror:
    """The parts of the store a grep needs, on local disk.

    Correctness never rests on local state: every operand is checked against
    Dropbox on every run, and a recursive listing prunes whatever has left the
    store. The rev manifest only saves downloads, so losing it costs bandwidth,
    never a wrong answer. The lock is held through the grep itself, so a
    concurrent grep cannot prune files from under it.
    """

    def __init__(self) -> None:
        self.revs: dict[str, str] = {}
        self.wanted: dict[str, str] = {}  # path_lower -> rev
        self.failed: list[tuple[str, str]] = []  # (path_lower, reason)

    def __enter__(self) -> "Mirror":
        os.makedirs(mirror_root(), exist_ok=True)
        # Store content on disk is new with the mirror; hold it to the same
        # owner-only footing as the token cache.
        os.chmod(mirror_root(), 0o700)
        self._lock = open(mirror_root() + ".lock", "w")
        if fcntl:  # absent on Windows, where only the offline tests run
            fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            with open(manifest_file(), encoding="utf-8") as fh:
                revs = json.load(fh)
            if isinstance(revs, dict):
                self.revs = revs
        except (OSError, ValueError):
            pass
        self._saved_at = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._save()
        finally:
            self._lock.close()

    def _save(self) -> None:
        partial = manifest_file() + ".partial"
        with open(partial, "w", encoding="utf-8") as fh:
            json.dump(self.revs, fh)
        os.replace(partial, manifest_file())
        self._saved_at = time.monotonic()

    def add_tree(self, lower: str) -> None:
        files, dirs = {}, {lower}
        for entry in list_tree(lower):
            if entry[".tag"] == "folder":
                dirs.add(entry["path_lower"])
            elif entry[".tag"] == "file":
                if os.path.splitext(entry["path_lower"])[1] not in BINARY_SUFFIXES:
                    files[entry["path_lower"]] = entry["rev"]
        prune(lower, files, dirs)
        for path, rev in files.items():
            self._want(path, rev)

    def add_file(self, meta: dict) -> None:
        # Fetched even with a binary suffix: naming it asks for it.
        self._want(meta["path_lower"], meta["rev"])

    def add_dir(self, lower: str) -> None:
        # Without -r grep only reports "Is a directory", so it need only exist.
        make_room(mirror_path(lower), is_dir=True)

    def _want(self, lower: str, rev: str) -> None:
        if self.revs.get(lower) != rev or not os.path.isfile(mirror_path(lower)):
            self.wanted[lower] = rev

    def fetch(self) -> None:
        """Download in parallel (Dropbox serialises writes, not reads), saving
        the manifest as it goes so a sync cut off by a timeout still makes
        progress for the next attempt."""
        if not self.wanted:
            return
        access_token()  # mint once here, not in eight threads at once
        with ThreadPoolExecutor(MIRROR_WORKERS) as pool:
            futures = {
                pool.submit(content_download, {"path": lower}): lower
                for lower in self.wanted
            }
            for future in as_completed(futures):
                lower = futures[future]
                local = mirror_path(lower)
                try:
                    data, meta = future.result()
                except (CfsError, OSError) as exc:
                    # A stale copy must not be searched in place of the real one.
                    self.revs.pop(lower, None)
                    remove_local(local)
                    if "not_found" not in str(exc):  # deleted since listing: gone is right
                        self.failed.append((lower, str(exc).splitlines()[0]))
                    continue
                make_room(local, is_dir=False)
                # Renamed into place, so a killed run never leaves a truncated
                # file; a leftover .partial is pruned like anything else.
                with open(local + ".partial", "wb") as fh:
                    fh.write(data)
                os.replace(local + ".partial", local)
                # The downloaded rev: the file may have changed since listing.
                self.revs[lower] = meta["rev"]
                if time.monotonic() - self._saved_at > MANIFEST_CHECKPOINT_SECONDS:
                    self._save()


def resolve_operand(path: str) -> dict | None:
    """Metadata for a path operand, or None when the store has nothing there."""
    norm = normalise(path)
    if norm == "/":
        return {".tag": "folder", "path_lower": "/"}  # get_metadata refuses the root
    try:
        return rpc("/2/files/get_metadata", {"path": norm})
    except CfsError as exc:
        if "not_found" in str(exc):
            return None
        raise


def list_tree(scope: str) -> list[dict]:
    """Every entry under the directory `scope`."""
    result = rpc("/2/files/list_folder", {"path": api_path(scope), "recursive": True})
    entries = list(result["entries"])
    while result.get("has_more"):
        result = rpc("/2/files/list_folder/continue", {"cursor": result["cursor"]})
        entries.extend(result["entries"])
    return entries


def prune(scope: str, files: dict, dirs: set) -> None:
    """Delete what the mirror holds under `scope` and the store does not.
    Walks the disk, not the manifest, to catch files a killed run never recorded."""
    root_len = len(mirror_root())
    for dirpath, dirnames, filenames in os.walk(mirror_path(scope)):
        base = dirpath[root_len:].replace("\\", "/").rstrip("/")
        for name in filenames:
            if f"{base}/{name}" not in files:
                os.remove(os.path.join(dirpath, name))
        for name in list(dirnames):
            if f"{base}/{name}" not in dirs:
                shutil.rmtree(os.path.join(dirpath, name))
                dirnames.remove(name)


def make_room(local: str, *, is_dir: bool) -> None:
    """Clear the way at `local`: the store may have swapped a file for a
    directory of the same name, or the reverse."""
    ancestor = os.path.dirname(local)
    while len(ancestor) > len(mirror_root()):
        if os.path.isfile(ancestor):
            os.remove(ancestor)
        ancestor = os.path.dirname(ancestor)
    if is_dir:
        if os.path.isfile(local):
            os.remove(local)
        os.makedirs(local, exist_ok=True)
    else:
        if os.path.isdir(local):
            shutil.rmtree(local)
        os.makedirs(os.path.dirname(local), exist_ok=True)


def remove_local(local: str) -> None:
    if os.path.isdir(local):
        shutil.rmtree(local)
    elif os.path.exists(local):
        os.remove(local)


def rewrite_grep_output(data: bytes) -> bytes:
    """Mirror paths -> store paths.

    Replacement rather than prefixing each line: grep omits the filename when
    searching a single file, so prefixing would corrupt content. Bytes
    throughout, because a match may contain anything at all.
    """
    root = os.fsencode(mirror_root())
    return data.replace(root, b"").replace(root.replace(b"\\", b"/"), b"")


def cmd_grep(raw_argv: list[str]) -> int:
    """Run real GNU grep against a mirror of the store."""
    parsed = GrepArgv(raw_argv)
    argv = parsed.argv

    if not parsed.operands and not parsed.pattern_is_an_option:
        raise CfsError("grep needs a pattern. Usage: grep [OPTION]... PATTERN [PATH]...")

    # Before any insertion below shifts the indices it holds.
    parsed.lowercase_globs()
    # "-" is grep's stdin, not a store path, but it still means a path was given.
    path_idx = [i for i in parsed.path_indices() if argv[i] != "-"]

    with Mirror() as mirror:
        if parsed.path_indices():
            access_token()
            with ThreadPoolExecutor(MIRROR_WORKERS) as pool:
                metas = list(pool.map(resolve_operand, [argv[i] for i in path_idx]))
            for i, meta in zip(path_idx, metas):
                if meta is None:
                    hint = ""
                    if parsed.unknown:
                        hint = (
                            f" -- cfs does not recognise {', '.join(sorted(set(parsed.unknown)))}, "
                            "so if that option takes a value, cfs mistook the value for a path"
                        )
                    raise CfsError(f"No such path in the store: {argv[i]}{hint}")
            # Trees first: a tree's prune would otherwise delete a named file
            # below it that was already in place, such as a binary.
            for meta in metas:
                if meta[".tag"] == "folder" and parsed.recursive:
                    mirror.add_tree(meta["path_lower"])
            for meta in metas:
                if meta[".tag"] == "file":
                    mirror.add_file(meta)
                elif not parsed.recursive:
                    mirror.add_dir(meta["path_lower"])
            for i, meta in zip(path_idx, metas):
                argv[i] = mirror_path(meta["path_lower"])
        else:
            # Real grep would read stdin here. There is no stdin worth reading,
            # so the whole store is the corpus instead -- the one deliberate
            # deviation.
            mirror.add_tree("/")
            if not parsed.recursive:
                argv.insert(0, "-r")
            argv.append(mirror_root())
        mirror.fetch()

        try:
            proc = subprocess.run(["grep"] + argv, capture_output=True)
        except FileNotFoundError:
            raise CfsError("grep is not installed in this sandbox.") from None

    code = proc.returncode
    if mirror.failed:
        # grep's own rule for a file it could not read: exit 2, unless -q
        # already found a match.
        if not (parsed.quiet and code == 0):
            code = 2
    try:
        sys.stdout.buffer.write(rewrite_grep_output(proc.stdout))
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        # Piping into `head` closes the pipe early. Real grep dies quietly on
        # SIGPIPE; a traceback here would look like a failed search.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return code
    sys.stderr.buffer.write(rewrite_grep_output(proc.stderr))
    for lower, reason in mirror.failed:
        sys.stderr.buffer.write(f"cfs grep: {lower}: not searched: {reason}\n".encode())
    return code


# With n=3 context a scattered diff runs to roughly 7x its changed-line count, so
# past about a seventh of the file the diff is the longer read and the file itself
# is returned instead. The fraction binds on small files and the line count on
# large ones: 30 changed lines only clears 10% from 300 lines up.
DIFF_MAX_CHANGED_LINES = 30
DIFF_MAX_CHANGED_FRACTION = 0.10

# Same role as read's marker: an unambiguous end-of-content line, since "---"
# is legitimate markdown and appears inside real memory files.
DIFF_MARK = "[end of diff output]"


def diff_report(
    path: str, old_rev: str, *, to_rev: str | None = None, context: int = 3,
    full: bool = False,
) -> str:
    """Show what changed between two revisions, unless that would be noise.

    A diff of a mostly-rewritten file is worse than useless: pages of -/+ that
    obscure rather than explain. Past a threshold this refuses and tells you to
    read the file, which is the answer you actually wanted.

    --since is mandatory and has no default. Defaulting to the penultimate
    revision looked convenient but answered a different question -- "what did
    the last write change?" is a fact about the file's history with no
    relationship to what the caller has in context. It could report one changed
    line to someone whose whole picture was stale, because two writes had landed
    since their read and it only compared the last two.
    """
    path = normalise(path)

    old_data, old_meta = content_download({"path": f"rev:{old_rev}"})
    check_rev_belongs(old_meta, path, old_rev)
    if to_rev:
        new_data, new_meta = content_download({"path": f"rev:{to_rev}"})
        check_rev_belongs(new_meta, path, to_rev)
    else:
        new_data, new_meta = content_download({"path": api_path(path)})

    try:
        old_text = old_data.decode("utf-8")
        new_text = new_data.decode("utf-8")
    except UnicodeDecodeError:
        return f"{path} is not text at one of these revisions; cannot diff."

    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")

    # diff DOES disclose the newer rev, and that is the point of the command.
    # Passing --rev <mine> means you already hold that revision's content, so
    # base + delta is the current file; the oversized branch hands back the whole
    # file instead. Either way you end up knowing the current bytes, which is the
    # bar for receiving a rev -- withholding it here would force a re-read that
    # tells you nothing you do not already have.
    newer = to_rev if to_rev else new_meta["rev"]

    # A verdict word first, and again beside the rev at the bottom. The two
    # outcomes previously differed only in a sentence buried above a wall of
    # file content, which skims as "here is the file, all fine" regardless of
    # what actually happened.
    if old_text == new_text:
        if to_rev:
            return f"UNCHANGED: {path} is identical at {old_rev} and {to_rev}."
        # Identical content does not imply an identical rev. A file edited x->y
        # and back to x has the bytes it started with but a new rev, and the old
        # one will be rejected -- so answering purely on content would promise a
        # rev that fails, with a rejection message ("the file changed since you
        # read it") that is itself wrong, because the content did not change.
        current = new_meta["rev"]
        if current == old_rev:
            return (
                f"UNCHANGED: {path} has not changed since {old_rev}, and that is "
                "still the current rev -- valid for editing or writing."
            )
        return (
            f"UNCHANGED CONTENT, NEW REV: {path} holds exactly the content it had "
            f"at {old_rev}, but it has been rewritten since, so {old_rev} is stale "
            "and any write using it will be rejected. What you already have is "
            f"current, so nothing needs re-reading -- just use the rev below."
            f"\n{DIFF_MARK}\nrev: {current}"
        )

    diff = list(
        difflib.unified_diff(
            old_lines, new_lines, fromfile=f"{path}@{old_rev}",
            tofile=f"{path}@{newer}", lineterm="", n=context,
        )
    )
    changed = sum(
        1 for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---"))
    )
    largest = max(len(old_lines), len(new_lines), 1)

    eyeballable = (
        changed <= DIFF_MAX_CHANGED_LINES
        and changed / largest <= DIFF_MAX_CHANGED_FRACTION
    )
    if not eyeballable and not full:
        shown = new_text
        truncated = len(shown) > MAX_VIEW_CHARS
        if truncated:
            shown = shown[:MAX_VIEW_CHARS]
        head = (
            f"CHANGED: {changed} of ~{largest} lines differ between {old_rev} and "
            f"{newer} -- too much to read as a diff, so here is the current file "
            f"instead. (--full for the raw diff; read {path} --rev {old_rev} for "
            "the older version.)\n\n"
        )
        if truncated:
            # The one branch that does not earn a rev: the file was too big to
            # show in full, so this call has not put the current bytes in front
            # of you and cannot certify them.
            return (
                head
                + numbered(shown)
                + f"\n{DIFF_MARK}\n[truncated at {MAX_VIEW_CHARS} chars, so no rev "
                f"is given -- you have not seen the whole file. Read {path} "
                "with --lines or --full to page through it and get its rev.]"
            )
        return (
            head
            + numbered(shown)
            + f"\n{DIFF_MARK}\nCHANGED since {old_rev}. The whole current file is "
            f"above, so this rev is valid for edit/write/delete.\nrev: {newer}"
        )

    header = (
        f"CHANGED: {changed} line(s) differ between {old_rev} "
        f"({old_meta.get('server_modified', '?')}) and {newer}:"
    )
    return (
        header
        + "\n"
        + "\n".join(diff)
        + f"\n{DIFF_MARK}\nCHANGED since {old_rev}. This rev is valid for "
        f"edit/write/delete because you hold {old_rev}'s content and the delta "
        "above reconstructs the current file. If you do not actually have that "
        f"older content, read {path} instead.\nrev: {newer}"
    )


def cmd_diff(args) -> str:
    if not args.from_rev:
        # Not argparse's `required`, so that the retired --from can be caught and
        # named instead of dying at the required-check before this runs.
        raise CfsError(
            "diff needs --since <rev>: the rev you are comparing from, normally "
            "the one you last read. There is deliberately no default -- the "
            "file's previous revision has no relationship to what you hold, and "
            "guessing it could report one changed line to someone whose whole "
            "picture is stale."
        )
    return diff_report(
        args.path, args.from_rev, to_rev=args.to_rev, context=args.context,
        full=args.full,
    )


# What to do once you have read the diff. Only delete differs in substance:
# there is no change of yours to re-apply, just a decision to re-confirm.
STALE_NEXT_STEP = {
    "write": "re-apply your change to it and write again",
    "edit": "re-apply your edit to it and try again",
    "upload": "re-apply your change to it and upload again",
    "delete": "confirm you still mean to delete the file, and try again",
}


def stale_rev_error(path: str, rev: str, verb: str) -> CfsError:
    """Reject a stale rev, carrying the diff the caller was going to run next.

    The prescribed recovery has always been `diff --from <your rev>`, so running
    it here collapses three steps into one, against a base the caller already
    holds. It returns rather than raises so the callers stay single-expression.
    """
    refused = (
        f"Stale rev: {path} has changed since you read it, so the {verb} was "
        "refused and nothing changed."
    )
    reread = (
        f"{refused} Re-read {path}, re-apply your change to what you get back, "
        "and retry with the rev from that read."
    )

    if os.path.splitext(path)[1].lower() in BINARY_SUFFIXES:
        return CfsError(
            f"{refused} It is a binary file, so there is no diff to show: fetch "
            f"it with `cfs download {path} --to <local>`, which reports the "
            "current rev, and retry with that."
        )
    try:
        report = diff_report(path, rev)
    except Exception:
        # A diff failure must never replace the error it was decorating. The rev
        # can be past Dropbox's 30-day window, or invented outright.
        return CfsError(reread)
    if "cannot diff" in report:
        return CfsError(reread)  # non-UTF-8 content that the suffix list missed

    return CfsError(
        f"{refused} The diff against {rev} is on stdout -- read it, then "
        f"{STALE_NEXT_STEP.get(verb, STALE_NEXT_STEP['write'])}.",
        stdout=report,
    )


def cmd_history(args) -> str:
    path = normalise(args.path)
    result = rpc(
        "/2/files/list_revisions",
        {"path": api_path(path), "mode": "path", "limit": args.limit},
    )
    entries = result.get("entries", [])
    if not entries:
        return f"No revision history for {path}."

    deleted = result.get("is_deleted", False)
    lines = [f"Revisions of {path} (newest first):"]
    if deleted:
        lines.append("  (the file is currently deleted; restore brings it back)")

    for i, entry in enumerate(entries):
        stamp = f"  {entry['server_modified']}\t{human_size(entry['size']):>7}\t"
        # When the file exists, entries[0] IS the live file, so printing its rev
        # would hand out a writable rev to a caller who has read no content --
        # the same hole `list` and `search` were closed for. Restore only ever
        # needs an older rev, so withholding this one costs nothing.
        if i == 0 and not deleted:
            lines.append(stamp + "(current — read the file to get its rev)")
        else:
            lines.append(stamp + f"rev:{entry['rev']}")

    lines.append("")
    lines.append(f"Restore an older one with: cfs restore {path} --rev <rev>")
    return "\n".join(lines)


def cmd_restore(args) -> str:
    path = normalise(args.path)
    rpc("/2/files/restore", {"path": api_path(path), "rev": args.rev})
    # The new rev is withheld: restoring brings back bytes the caller has not
    # necessarily seen, so it is not evidence of knowing the file's contents.
    # Read it before writing -- which you want to do anyway, to check the
    # rollback landed where you expected.
    return (
        f"Restored {path} to rev {args.rev}. Restoring adds a new revision rather "
        "than erasing anything, so this is itself reversible.\n"
        f"Read {path} to see the restored content and get its current rev."
    )


MAX_SIMPLE_UPLOAD = 150 * 1024 * 1024


def cmd_upload(args) -> str:
    path = normalise(args.path)
    try:
        with open(args.source, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise CfsError(f"Could not read local file '{args.source}': {exc.strerror}") from exc

    if len(data) > MAX_SIMPLE_UPLOAD:
        raise CfsError(
            f"'{args.source}' is {human_size(len(data))}, over the {human_size(MAX_SIMPLE_UPLOAD)} "
            "single-request limit. Chunked upload is not implemented."
        )

    meta = upload_bytes(path, data, args, verb="upload")
    return f"Uploaded {args.source} -> {path} ({human_size(len(data))}).\nnew rev: {meta['rev']}"


def cmd_download(args) -> str:
    path = normalise(args.path)
    data, meta = content_download({"path": api_path(path)})
    try:
        with open(args.dest, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        raise CfsError(f"Could not write local file '{args.dest}': {exc.strerror}") from exc
    return f"Downloaded {path} -> {args.dest} ({human_size(len(data))}).\nrev: {meta['rev']}"


# --------------------------------------------------------------------------


# Retired spellings, kept registered only so they can be refused by name. Each
# collided with the same flag meaning something else elsewhere, and for a reader
# who re-reads SKILL.md every conversation the flag name is the documentation --
# so a working alias would teach the collision rather than retire it.
RETIRED_FLAGS = {
    "old_from": ("--from", "--since", "--from names a local file on upload"),
    "old_to": ("--to", "--until", "--to names a local file on download"),
    "old_force": (
        "--force", "--full", "--force means 'waive a safety requirement' on delete",
    ),
    "old_max": ("--max", "--limit", "history already calls this --limit"),
}


def reject_retired_flags(args) -> None:
    for dest, (old, new, why) in RETIRED_FLAGS.items():
        value = getattr(args, dest, None)
        # Identity, not truthiness: `--max 0` is a value that was passed, but it
        # is falsy, and store_true dests are absent as False rather than None.
        if value is not None and value is not False:
            raise CfsError(
                f"{old} was renamed to {new}, because {why}. Nothing was read or "
                f"written. Re-run with {new}."
            )


def build_parser() -> argparse.ArgumentParser:
    epilog = (
        "write takes raw content on stdin; edit takes a SEARCH/REPLACE block. Feed "
        "both from a quoted heredoc, so the shell leaves the content alone:\n\n"
        + STDIN_EXAMPLE
        + "\n\n"
        + SR_EXAMPLE
        + "\n\nA JSON object on stdin is also accepted for programmatic callers "
        "(write --json; edit detects it). There is deliberately no way to read "
        "old_str from a file."
    )
    parser = argparse.ArgumentParser(
        prog="cfs",
        description=__doc__,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list a directory")
    p.add_argument("path", nargs="?", default="/")
    p.add_argument("--depth", type=int, default=2)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("read", help="read a file (reports the rev you need to write)")
    p.add_argument("path")
    p.add_argument("--rev", help="read a historical revision (cannot be used to write)")
    p.add_argument("--lines", help="START-END, 1-indexed; END may be -1")
    p.add_argument("--full", action="store_true", help="do not truncate long files")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser(
        "write", help="create or overwrite a file; raw content on stdin"
    )
    p.add_argument("path")
    p.add_argument("--content", help="inline content, for short single-line values")
    p.add_argument(
        "--stdin",
        action="store_true",
        help="read the content raw from stdin -- the default; kept for callers "
        "written against the older interface",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help='read stdin as a JSON object {"content": "..."} instead of raw',
    )
    p.add_argument("--rev", help="current rev; required when overwriting")
    p.add_argument("--new", action="store_true", help="create; fails if path exists")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser(
        "edit",
        help="replace text via SEARCH/REPLACE blocks on stdin, all or none",
    )
    p.add_argument("path")
    p.add_argument("--rev", help="current rev, from read")
    p.add_argument(
        "--tag",
        help="suffix for the SEARCH/REPLACE markers, when the file contains "
        "conflict-marker lines of its own",
    )
    p.add_argument(
        "--delim",
        help="alternative input: split old from new on a line equal to this marker",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="replace every occurrence instead of requiring a unique match",
    )
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("delete", help="delete a file or directory")
    p.add_argument("path")
    p.add_argument("--rev")
    p.add_argument("--force", action="store_true", help="allow deleting without a rev")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("rename", help="rename or move a file or directory")
    p.add_argument("old_path")
    p.add_argument("new_path")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("copy", help="copy a file or directory")
    p.add_argument("src")
    p.add_argument("dst")
    p.set_defaults(func=cmd_copy)

    p = sub.add_parser(
        "search", help="full-text search across files", allow_abbrev=False
    )
    p.add_argument("query")
    p.add_argument("--path", help="restrict to a subtree")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--max", dest="old_max", type=int, help=argparse.SUPPRESS)
    p.add_argument(
        "--names-only", action="store_true", help="match filenames rather than content"
    )
    p.set_defaults(func=cmd_search)

    # Listed for --help only. main() routes grep before argparse sees it, so
    # that every flag reaches the real binary exactly as it was typed.
    sub.add_parser(
        "grep", help="GNU grep over the store: grep [OPTION]... PATTERN [PATH]...",
        add_help=False,
    ).add_argument("argv", nargs=argparse.REMAINDER)

    # allow_abbrev off: SUPPRESSed options still take part in prefix matching, so
    # `--f` would report an ambiguity naming --from, --full and --force, which
    # advertises the retired spellings at the moment of confusion.
    p = sub.add_parser(
        "diff", help="show what changed since a rev you hold", allow_abbrev=False
    )
    p.add_argument("path")
    p.add_argument(
        "--since",
        dest="from_rev",
        metavar="REV",
        help="the rev you are comparing from -- normally the one you last read",
    )
    p.add_argument(
        "--until", dest="to_rev", metavar="REV",
        help="newer rev (default: the current file)",
    )
    p.add_argument("-C", "--context", type=int, default=3)
    p.add_argument(
        "--full", action="store_true", help="show the diff even if it is mostly noise"
    )
    p.add_argument("--from", dest="old_from", help=argparse.SUPPRESS)
    p.add_argument("--to", dest="old_to", help=argparse.SUPPRESS)
    p.add_argument("--force", dest="old_force", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("history", help="list previous revisions of a file")
    p.add_argument("path")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("restore", help="roll a file back to an earlier revision")
    p.add_argument("path")
    p.add_argument("--rev", required=True, help="target rev, from history")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("upload", help="upload a local file (binaries, generated artefacts)")
    p.add_argument("path")
    p.add_argument("--from", dest="source", required=True, help="local file to upload")
    p.add_argument("--rev", help="current rev; required when overwriting")
    p.add_argument("--new", action="store_true", help="create; fails if path exists")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("download", help="download to a local file")
    p.add_argument("path")
    p.add_argument("--to", dest="dest", required=True, help="local destination")
    p.set_defaults(func=cmd_download)

    return parser


def unreachable(exc: urllib.error.URLError) -> str:
    return (
        f"Error: could not reach Dropbox ({exc.reason}). If this is the claude.ai "
        "sandbox, api.dropboxapi.com and content.dropboxapi.com must be allowed "
        "by the code-execution network egress setting."
    )


def main() -> int:
    # grep bypasses argparse entirely: its flags are GNU grep's, and argparse
    # would reject or reinterpret them before the real binary ever ran.
    if sys.argv[1:2] == ["grep"]:
        try:
            return cmd_grep(sys.argv[2:])
        except CfsError as exc:
            print(f"Error: {exc}", file=sys.stderr)
        except urllib.error.URLError as exc:
            print(unreachable(exc), file=sys.stderr)
        return 2  # grep's own code for "the search did not run"

    args = build_parser().parse_args()
    try:
        reject_retired_flags(args)
        print(args.func(args))
    except CfsError as exc:
        if exc.stdout:
            # Flushed before the message so that a caller merging the streams
            # sees the payload settled, not interleaved mid-diff.
            print(exc.stdout)
            sys.stdout.flush()
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(unreachable(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
