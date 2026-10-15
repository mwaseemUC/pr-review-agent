from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

FileStatus = Literal["added", "modified", "deleted", "renamed"]
LineKind = Literal["+", "-", " "]

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_GIT_HEADER_RE = re.compile(r'^diff --git (?:"?a/(.+?)"?) (?:"?b/(.+?)"?)$')
_DEV_NULL = "/dev/null"


@dataclass
class DiffLine:
    kind: LineKind
    text: str
    old_no: int | None
    new_no: int | None
    no_newline_at_eof: bool = False


@dataclass
class Hunk:
    header: str
    old_start: int
    new_start: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: str | None
    status: FileStatus
    added_lines: set[int] = field(default_factory=set)
    commentable_lines: set[int] = field(default_factory=set)
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list, repr=False, compare=False)
    raw: str = field(default="", repr=False, compare=False)


@dataclass
class _Builder:
    old_path: str | None = None
    new_path: str | None = None
    status: FileStatus = "modified"
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)

    def build(self) -> FileDiff | None:
        path = self.new_path or self.old_path
        if path is None:
            return None
        status = self.status
        if self.new_path is None:
            status = "deleted"
        elif self.old_path is None:
            status = "added"
        elif status == "modified" and self.old_path != self.new_path:
            status = "renamed"
        added: set[int] = set()
        commentable: set[int] = set()
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.new_no is None:
                    continue
                commentable.add(line.new_no)
                if line.kind == "+":
                    added.add(line.new_no)
        return FileDiff(
            path=path,
            old_path=self.old_path if status != "added" else None,
            status=status,
            added_lines=added,
            commentable_lines=commentable,
            is_binary=self.is_binary,
            hunks=self.hunks,
            raw="\n".join(self.raw_lines) + "\n" if self.raw_lines else "",
        )


def split_lines(text: str) -> list[str]:
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _strip_prefix(raw: str, prefix: str) -> str | None:
    value = raw.split("\t", 1)[0].strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    if value == _DEV_NULL:
        return None
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


def _parse_hunk_header(line: str) -> tuple[Hunk, int, int] | None:
    match = _HUNK_RE.match(line)
    if not match:
        return None
    old_start, old_len, new_start, new_len, _ = match.groups()
    hunk = Hunk(header=line, old_start=int(old_start), new_start=int(new_start))
    old_count = int(old_len) if old_len is not None else 1
    new_count = int(new_len) if new_len is not None else 1
    return hunk, old_count, new_count


def _starts_plain_unified(lines: list[str], index: int) -> bool:
    return (
        lines[index].startswith("--- ")
        and index + 1 < len(lines)
        and lines[index + 1].startswith("+++ ")
    )


def _parse_files(diff_text: str) -> list[_Builder]:
    lines = split_lines(diff_text)
    builders: list[_Builder] = []
    current: _Builder | None = None
    hunk: Hunk | None = None
    old_left = new_left = 0
    old_no = new_no = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        in_hunk = hunk is not None and (old_left > 0 or new_left > 0)

        if in_hunk and hunk is not None and current is not None:
            current.raw_lines.append(line)
            marker = line[:1]
            text = line[1:]
            if marker == "+":
                hunk.lines.append(DiffLine("+", text, None, new_no))
                new_no += 1
                new_left -= 1
            elif marker == "-":
                hunk.lines.append(DiffLine("-", text, old_no, None))
                old_no += 1
                old_left -= 1
            elif marker == " " or line == "":
                hunk.lines.append(DiffLine(" ", text, old_no, new_no))
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
            elif marker == "\\":
                if hunk.lines:
                    hunk.lines[-1].no_newline_at_eof = True
            else:
                hunk = None
                continue
            i += 1
            continue

        if line.startswith("\\") and current is not None:
            current.raw_lines.append(line)
            if hunk is not None and hunk.lines:
                hunk.lines[-1].no_newline_at_eof = True
            i += 1
            continue

        git_header = _GIT_HEADER_RE.match(line)
        if git_header or (line.startswith("diff --git ")):
            current = _Builder(raw_lines=[line])
            if git_header:
                current.old_path, current.new_path = git_header.group(1), git_header.group(2)
            builders.append(current)
            hunk = None
            i += 1
            continue

        if current is None or (hunk is not None and _starts_plain_unified(lines, i)):
            if _starts_plain_unified(lines, i):
                current = _Builder()
                builders.append(current)
                hunk = None
            else:
                i += 1
                continue

        current.raw_lines.append(line)
        parsed = _parse_hunk_header(line)
        if parsed is not None:
            hunk, old_left, new_left = parsed
            old_no, new_no = hunk.old_start, hunk.new_start
            current.hunks.append(hunk)
        elif line.startswith("--- "):
            current.old_path = _strip_prefix(line[4:], "a/")
        elif line.startswith("+++ "):
            current.new_path = _strip_prefix(line[4:], "b/")
        elif line.startswith("new file mode"):
            current.status = "added"
            current.old_path = None
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
            current.new_path = None
        elif line.startswith("rename from "):
            current.status = "renamed"
            current.old_path = line[len("rename from ") :]
        elif line.startswith("rename to "):
            current.status = "renamed"
            current.new_path = line[len("rename to ") :]
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.is_binary = True
        i += 1
    return builders


def parse_diff(diff_text: str) -> dict[str, FileDiff]:
    result: dict[str, FileDiff] = {}
    for builder in _parse_files(diff_text):
        file_diff = builder.build()
        if file_diff is not None:
            result[file_diff.path] = file_diff
    return result


def split_diff(diff_text: str) -> dict[str, str]:
    return {path: fd.raw for path, fd in parse_diff(diff_text).items()}
