"""Pure text transforms adapted from TJAI's exact-match and section services."""

from copy import deepcopy
import difflib
import json
import re

from .schemas import State


class EditConflict(ValueError):
    pass


def positions(content, target, occurrence=None, all_matches=False):
    found, start = [], 0
    while (index := content.find(target, start)) >= 0:
        found.append(index)
        start = index + len(target)
    if not found:
        raise EditConflict("TARGET_NOT_FOUND")
    if occurrence is not None:
        if occurrence > len(found):
            raise EditConflict(f"OCCURRENCE_NOT_FOUND: {len(found)} matches")
        return [found[occurrence - 1]]
    if len(found) != 1 and not all_matches:
        raise EditConflict(f"AMBIGUOUS_TARGET: {len(found)} matches; select occurrence or all_matches")
    return found


def section_range(content, selector):
    """ATX headings outside fenced code; preserve heading and adjacent sections."""
    headings, fence, offset = [], None, 0
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker and (marker[1][0] != "`" or "`" not in marker[2]):
            fence = marker[1]
        else:
            match = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$", line.rstrip("\r\n"))
            if match:
                title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
                headings.append((offset, offset + len(line), len(match[1]), title))
        offset += len(line)
    matches = [h for h in headings if h[3] == selector.heading and (selector.level is None or h[2] == selector.level)]
    if not matches:
        raise EditConflict("SECTION_NOT_FOUND")
    if selector.occurrence is None and len(matches) != 1:
        raise EditConflict(f"AMBIGUOUS_SECTION: {len(matches)} headings")
    occurrence = selector.occurrence or 1
    if occurrence > len(matches):
        raise EditConflict("SECTION_OCCURRENCE_NOT_FOUND")
    heading = matches[occurrence - 1]
    end = next((h[0] for h in headings if h[0] > heading[0] and h[2] <= heading[2]), len(content))
    return heading[1], end


def transform(original, edits):
    state = deepcopy(original)
    for index, edit in enumerate(edits):
        text = state["content"]
        try:
            if edit.op in {"replace", "insert"}:
                target = edit.old_text if edit.op == "replace" else edit.anchor
                replacement = edit.new_text if edit.op == "replace" else (
                    edit.text + target if edit.position == "before" else target + edit.text)
                for start in reversed(positions(text, target, edit.occurrence, edit.all_matches)):
                    text = text[:start] + replacement + text[start + len(target):]
                    if len(text) > 40000:
                        raise ValueError("Content exceeds 40000 characters")
                state["content"] = text
            elif edit.op == "section":
                start, end = section_range(text, edit)
                # A following heading must stay a heading; padding is explicit.
                if end < len(text) and edit.content and not edit.content.endswith("\n"):
                    raise EditConflict("SECTION_BOUNDARY: replacement before a following heading must end in a newline")
                if start and not text[:start].endswith("\n") and edit.content:
                    raise EditConflict("SECTION_BOUNDARY: heading has no trailing newline")
                state["content"] = text[:start] + edit.content + text[end:]
            elif edit.op == "append":
                state["content"] = text + (edit.separator if text and edit.content else "") + edit.content
            elif edit.op == "set_content":
                state["content"] = edit.content
            elif edit.op == "fields":
                state.update(edit.changes)
            elif edit.op == "metadata":
                for key in edit.remove:
                    state["metadata"].pop(key, None)
                state["metadata"].update(edit.set)
            elif edit.op == "relations":
                remove = [r.model_dump(mode="json") for r in edit.remove]
                links = [r for r in state["relations"] if r not in remove]
                for relation in edit.add:
                    value = relation.model_dump(mode="json")
                    if value not in links:
                        links.append(value)
                state["relations"] = links
            state = State.model_validate(state).model_dump(mode="json")
        except ValueError as error:
            raise EditConflict(f"EDIT_{index + 1}: {error}") from error
    return state


def unified_diff(before, after, fromfile="before", tofile="after"):
    parts = []
    for line in difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                     fromfile=fromfile, tofile=tofile, n=3):
        parts.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(parts)


def state_diff(before, after):
    content = unified_diff(before["content"], after["content"], "before/content", "after/content")
    def other(state):
        return json.dumps({k: v for k, v in state.items() if k != "content"}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return content + unified_diff(other(before), other(after), "before/fields", "after/fields")
