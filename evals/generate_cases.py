"""Write evals/cases/<id>/{diff.patch, files/, label.json} from evals/cases_spec.py.

Deterministic: same spec -> byte-identical output. Validates that every planted bug line exists in
the after file and is an added ('+') line of the diff, and that Python sources parse.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.cases_spec import CASES, Case, FileChange  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def file_diff(path: str, change: FileChange) -> str:
    before = change.before.splitlines(keepends=True)
    after = change.after.splitlines(keepends=True)
    is_new = not change.before
    header = [f"diff --git a/{path} b/{path}\n"]
    if is_new:
        header.append("new file mode 100644\n")
    body = list(
        difflib.unified_diff(
            before,
            after,
            fromfile="/dev/null" if is_new else f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if not body:
        return ""
    return "".join(header + body)


def case_diff(case: Case) -> str:
    return "".join(file_diff(path, case.files[path]) for path in sorted(case.files))


def added_lines(diff_text: str) -> dict[str, set[int]]:
    """New-side line numbers of '+' lines, per file."""
    result: dict[str, set[int]] = {}
    current: set[int] | None = None
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            current = result.setdefault(path, set())
            continue
        if line.startswith("--- ") or line.startswith("diff --git"):
            continue
        match = _HUNK_RE.match(line)
        if match:
            new_line = int(match.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            current.add(new_line)
            new_line += 1
        elif line.startswith(" "):
            new_line += 1
    return result


def validate_case(case: Case, diff_text: str) -> list[str]:
    errors: list[str] = []
    if not diff_text:
        errors.append("empty diff")
    for path, change in case.files.items():
        if not change.after.endswith("\n"):
            errors.append(f"{path}: after source must end with a newline")
        if path.endswith(".py"):
            try:
                ast.parse(change.after, filename=path)
            except SyntaxError as exc:
                errors.append(f"{path}: does not parse: {exc}")
    added = added_lines(diff_text)
    for bug in case.bugs:
        after_lines = case.files[bug.file].after.splitlines()
        if not 1 <= bug.line <= len(after_lines):
            errors.append(f"{bug.file}:{bug.line} outside file ({len(after_lines)} lines)")
        elif bug.line not in added.get(bug.file, set()):
            errors.append(f"{bug.file}:{bug.line} is not an added line in the diff")
    return errors


def label_for(case: Case, diff_text: str) -> dict[str, object]:
    added = added_lines(diff_text)
    return {
        "id": case.id,
        "description": case.description,
        "kind": case.kind,
        "language": case.language,
        "files": sorted(case.files),
        "added_lines": sum(len(lines) for lines in added.values()),
        "bugs": [
            {
                "file": bug.file,
                "line": bug.line,
                "category": bug.category,
                "explanation": bug.explanation,
            }
            for bug in case.bugs
        ],
    }


def write_case(case: Case, root: Path) -> dict[str, object]:
    diff_text = case_diff(case)
    errors = validate_case(case, diff_text)
    if errors:
        raise ValueError(f"case {case.id} is invalid: " + "; ".join(errors))
    case_dir = root / case.id
    files_dir = case_dir / "files"
    for path, change in case.files.items():
        target = files_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(change.after.encode("utf-8"))
    (case_dir / "diff.patch").write_bytes(diff_text.encode("utf-8"))
    label = label_for(case, diff_text)
    (case_dir / "label.json").write_bytes(
        (json.dumps(label, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    return label


def generate(root: Path = CASES_DIR) -> list[dict[str, object]]:
    ids = [case.id for case in CASES]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    root.mkdir(parents=True, exist_ok=True)
    labels = [write_case(case, root) for case in CASES]
    _prune(root, _expected_paths(root))
    return labels


def _expected_paths(root: Path) -> set[Path]:
    expected: set[Path] = set()
    for case in CASES:
        case_dir = root / case.id
        expected |= {case_dir / "diff.patch", case_dir / "label.json"}
        expected |= {case_dir / "files" / path for path in case.files}
    return expected


def _prune(root: Path, expected: set[Path]) -> None:
    """Remove stale files and empty dirs in place (rmtree of the root is flaky under OneDrive)."""
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file() and path not in expected:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def main() -> int:
    labels = generate()
    for label in labels:
        bugs = label["bugs"]
        where = ", ".join(f"{b['file']}:{b['line']} [{b['category']}]" for b in bugs) or "clean"
        print(f"ok  {label['id']:<30} +{label['added_lines']:<3} {where}")
    n_bug = sum(1 for label in labels if label["kind"] == "bug")
    print(
        f"\n{len(labels)} cases written to {CASES_DIR} ({n_bug} bug, {len(labels) - n_bug} clean)"
    )
    print("all labeled lines exist in the after file and are added lines in the diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
