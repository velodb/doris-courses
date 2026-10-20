#!/usr/bin/env python3
"""Check quiz YAML structure and answer balance.

Usage (from the repository root):
    python3 tools/check_quiz_balance.py [--strict] [PATH ...]

PATH defaults to doris-course/. For each course level the script reports how
often the correct option is the longest option and how the correct answers
are spread across positions. It also flags schema issues that block option
shuffling: questions without an id, option text that starts with a
letter prefix such as "A. ", and explanations that refer to options by letter.
With --strict, the exit status is 1 when a threshold is exceeded or a schema
issue is found.

PyYAML is used when available (it is installed in the course virtual
environments); otherwise a small parser for the course quiz layout is used.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAX_LONGEST_SHARE = 0.40
MAX_POSITION_SHARE = 0.40
LETTER_PREFIX = re.compile(r"^[A-D][.)]\s")
LETTER_REFERENCE = re.compile(r"(^|[\s'\"(])[A-D]:\s")

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - depends on the local environment
    yaml = None


def parse_fallback(text):
    """Parse the quiz layout used by the courses without PyYAML."""
    lines = text.split("\n")
    questions, current, index = [], None, 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if re.match(r"^\s*- (id|objective|prompt):", line) and current is None:
            current = {"options": []}
        option = re.match(r"^\s*- id:\s*\"?([^\"\s]+)\"?\s*$", line)
        if option and index + 1 < len(lines) and re.match(r"^\s*text:", lines[index + 1]):
            indent = len(lines[index + 1]) - len(lines[index + 1].lstrip())
            value = lines[index + 1].split("text:", 1)[1].strip()
            index += 2
            # A block scalar starts on the next line; a plain scalar may continue there.
            parts = [] if value in (">-", ">", "|", "|-") else [value]
            while index < len(lines) and lines[index].strip() and \
                    len(lines[index]) - len(lines[index].lstrip()) > indent:
                parts.append(lines[index].strip())
                index += 1
            value = " ".join(parts)
            current["options"].append({"id": option.group(1), "text": value.strip().strip("\"'")})
            continue
        if current is not None and re.match(r"^\s*- id:", line) and not current["options"]:
            current["id"] = stripped.split(":", 1)[1].strip().strip("\"'")
        if current is not None and re.match(r"^\s*id:", line) and not current["options"]:
            current["id"] = stripped.split(":", 1)[1].strip().strip("\"'")
        answer = re.match(r"^\s*answer:\s*\"?([^\"\s]+)\"?\s*$", line)
        if answer and current is not None:
            current["answer"] = answer.group(1)
        if current is not None and re.match(r"^\s*explanation:", line):
            value = stripped.split(":", 1)[1].strip()
            index += 1
            parts = [] if value in (">-", ">", "|", "|-") else [value]
            while index < len(lines) and (not lines[index].strip() or
                                          lines[index].startswith(" " * 4)) and \
                    not re.match(r"^\s*- (id|objective|prompt):", lines[index]):
                parts.append(lines[index].strip())
                index += 1
            current["explanation"] = " ".join(parts)
            questions.append(current)
            current = None
            continue
        index += 1
    return questions


def load_questions(path):
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text) or {}
        return data.get("questions", [])
    return parse_fallback(text)


def group_name(path):
    parts = path.relative_to(REPO).parts if path.is_relative_to(REPO) else path.parts
    course = next((part for part in parts if re.match(r"^0\d-", part)), "?")
    level = next((part for part in parts if part.startswith("level")), "")
    return f"{course} {level}".strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    roots = [Path(path).resolve() for path in args.paths] or [REPO / "doris-course"]

    stats = defaultdict(lambda: {"total": 0, "longest": 0, "positions": Counter()})
    issues = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("quiz*.yaml"))
        for path in files:
            name = group_name(path)
            for number, question in enumerate(load_questions(path), 1):
                where = f"{path.relative_to(REPO)} Q{number}"
                options = question.get("options") or []
                texts = {str(option.get("id")): str(option.get("text", "")) for option in options}
                answer = str(question.get("answer", ""))
                if not question.get("id"):
                    issues.append(f"{where}: question has no id")
                if answer not in texts:
                    issues.append(f"{where}: answer '{answer}' is not an option id")
                    continue
                if any(LETTER_PREFIX.match(text) for text in texts.values()):
                    issues.append(f"{where}: option text starts with a letter prefix")
                if LETTER_REFERENCE.search(str(question.get("explanation", ""))):
                    issues.append(f"{where}: explanation refers to options by letter")
                ids = [str(option.get("id")) for option in options]
                stats[name]["total"] += 1
                stats[name]["positions"][ids.index(answer)] += 1
                if len(texts[answer]) == max(len(text) for text in texts.values()):
                    stats[name]["longest"] += 1

    failed = False
    print(f"{'group':<36} {'questions':>9} {'longest':>9} {'top position':>13}  positions (A B C D)")
    for name in sorted(stats):
        item = stats[name]
        total = item["total"]
        longest = item["longest"] / total
        top = max(item["positions"].values()) / total
        counts = " ".join(str(item["positions"].get(i, 0)) for i in range(4))
        flag = ""
        if longest > MAX_LONGEST_SHARE or top > MAX_POSITION_SHARE:
            flag = "  <-- unbalanced"
            failed = True
        print(f"{name:<36} {total:>9} {longest:>8.0%} {top:>12.0%}   {counts}{flag}")
    if issues:
        print(f"\n{len(issues)} schema issue(s):")
        for issue in issues:
            print(f"  {issue}")
        failed = True
    print(f"\nThresholds: longest option <= {MAX_LONGEST_SHARE:.0%}, single position <= {MAX_POSITION_SHARE:.0%}")
    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    sys.exit(main())
