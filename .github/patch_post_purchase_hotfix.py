#!/usr/bin/env python3
"""Apply and verify the three-second post-purchase recovery hotfix."""
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_post_purchase_hotfix.py SOURCE_ROOT")
root = Path(sys.argv[1]).resolve()


def read(rel, required=True):
    p = root / rel
    if not p.is_file():
        if required:
            raise RuntimeError(f"Required source file missing: {rel}")
        return None, None
    return p, p.read_text(encoding="utf-8")


def write(p, data):
    p.write_text(data, encoding="utf-8", newline="\n")


def set_numeric(rel, name, value, anchor=None, optional=False):
    p, data = read(rel)
    pattern = re.compile(rf"(?m)^([ \t]*){re.escape(name)}\s*=\s*([0-9]+(?:\.[0-9]+)?)(?:\s+#.*)?$")
    matches = list(pattern.finditer(data))
    if len(matches) == 1:
        m = matches[0]
        if float(m.group(2)) == float(value):
            print(f"ALREADY SET {name}={value}")
            return True
        write(p, data[:m.start()] + f"{m.group(1)}{name} = {value:.2f}" + data[m.end():])
        print(f"SET {name}={value}")
        return True
    if len(matches) > 1:
        raise RuntimeError(f"Duplicate constant {name} in {rel}: {len(matches)} matches")
    if not anchor:
        if optional:
            print(f"INLINE FALLBACK: no named constant {name}")
            return False
        raise RuntimeError(f"Required constant not found: {name}")
    anchor_re = re.compile(rf"(?m)^([ \t]*){re.escape(anchor)}\s*=\s*[0-9]+(?:\.[0-9]+)?(?:\s+#.*)?$")
    found = list(anchor_re.finditer(data))
    if len(found) != 1:
        if optional:
            print(f"INLINE FALLBACK: no unique anchor {anchor} for {name}")
            return False
        raise RuntimeError(f"Cannot insert {name}; anchor {anchor} matches {len(found)} times")
    m = found[0]
    write(p, data[:m.end()] + f"\n{m.group(1)}{name} = {value:.2f}" + data[m.end():])
    print(f"INSERTED {name}={value}")
    return True


def replace_guard_literal(text, variable, target):
    pat = re.compile(rf"(?m)^([ \t]*and\s+{re.escape(variable)}\s*>=\s*)[0-9]+(?:\.[0-9]+)?(\s*)$")
    ms = list(pat.finditer(text))
    if len(ms) == 1:
        m = ms[0]
        text = text[:m.start()] + m.group(1) + target + m.group(2) + text[m.end():]
        print(f"SET inline guard: {variable} >= {target}")
        return text, True
    if len(ms) == 0 and re.search(rf"(?m)^\s*and\s+{re.escape(variable)}\s*>=\s*{re.escape(target)}\s*$", text):
        print(f"ALREADY SET inline guard: {variable} >= {target}")
        return text, True
    if len(ms) == 0:
        return text, False
    raise RuntimeError(f"Expected one elapsed-time guard for {variable}, got {len(ms)}")


def set_elapsed_assignment(text, variable, constant):
    """Change the delay expression on cancel/back cooldown assignments, including multiline forms."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if re.match(rf"^[ \t]*{re.escape(variable)}\s*=", line)]
    if len(starts) == 0:
        print(f"Optional cooldown assignment absent: {variable}")
        return text, False
    if len(starts) != 1:
        raise RuntimeError(f"Duplicate assignment for {variable}")
    start = starts[0]
    end = start
    depth = 0
    while end < len(lines):
        depth += lines[end].count("(") - lines[end].count(")")
        if end > start and depth <= 0:
            break
        end += 1
    block = "".join(lines[start:end+1])
    if constant in block:
        print(f"ALREADY SET cooldown: {variable}")
        return text, True
    named_cooldowns = re.findall(r"POST_PURCHASE_CANCEL_[A-Z0-9_]*SECONDS", block)
    if named_cooldowns:
        # These named timers are normalized to 3.00 by set_numeric() immediately before
        # validating this assignment; their presence means the assignment uses that timer.
        normalized_names = {
            "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS",
            "POST_PURCHASE_CANCEL_RETRY_SECONDS",
            "POST_PURCHASE_CANCEL_BACK_GRACE_SECONDS",
        }
        if any(name in normalized_names for name in named_cooldowns):
            print(f"ALREADY SAFE named cooldown: {variable} uses {named_cooldowns[0]}")
            return text, True
    pat = re.compile(r"(>=\s*)[0-9]+(?:\.[0-9]+)?")
    block2, n = pat.subn(rf"\g<1>{constant}", block, count=1)
    if n != 1:
        raise RuntimeError(f"No numeric elapsed-time bound found for {variable}:\n{block}")
    lines[start:end+1] = [block2]
    print(f"SET cooldown: {variable} uses {constant}")
    return "".join(lines), True

main = "main.py"
p, data = read(main)
for name in (
    "POST_PURCHASE_CONFIRM_GRACE_SECONDS",
    "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS",
    "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS",
):
    set_numeric(main, name, 3.0)
p, data = read(main)

# The uploaded archive can contain either the newer "unknown" guard or the older "nonmodal" guard.
nonmodal_variant = ("POST_PURCHASE_NONMODAL_AFTER_CONFIRM_MIN_SECONDS" in data
                    or "nonmodal_for" in data
                    or "_resume_market_nonmodal_after_confirm_last_frame_token" in data)
if nonmodal_variant:
    set_numeric(main, "POST_PURCHASE_NONMODAL_AFTER_CONFIRM_MIN_FRESH_FRAMES", 3, optional=True)
    nonmodal_seconds_set = set_numeric(main, "POST_PURCHASE_NONMODAL_AFTER_CONFIRM_MIN_SECONDS", 3.0, optional=True)
    p, data = read(main)
    if not nonmodal_seconds_set:
        data, ok = replace_guard_literal(data, "nonmodal_for", "3.00")
        if not ok:
            raise RuntimeError("Could not set nonmodal elapsed-time guard to 3 seconds")
        write(p, data)
    if "and confirm_seen_for >= POST_PURCHASE_CONFIRM_GRACE_SECONDS" not in data:
        data, ok = replace_guard_literal(data, "confirm_seen_for", "3.00")
        if not ok:
            raise RuntimeError("Could not verify/set modal-seen grace in nonmodal variant")
        write(p, data)
    guard_marker = "nonmodal"
else:
    set_numeric(main, "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_FRESH_FRAMES", 3, optional=True)
    min_named = set_numeric(main, "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS", 3.0, optional=True)
    back_named = set_numeric(main, "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS", 3.0, optional=True)
    p, data = read(main)
    if not min_named:
        data, ok = replace_guard_literal(data, "unknown_for", "3.00")
        if not ok:
            raise RuntimeError("Could not set unknown-state elapsed-time guard to 3 seconds")
    if not back_named:
        if re.search(r"(?m)^\s*and\s+confirm_seen_for\s*>=\s*POST_PURCHASE_CONFIRM_GRACE_SECONDS\s*$", data):
            print("Modal-seen grace uses POST_PURCHASE_CONFIRM_GRACE_SECONDS=3")
        else:
            data, ok = replace_guard_literal(data, "confirm_seen_for", "3.00")
            if not ok:
                raise RuntimeError("Could not set modal-seen elapsed-time guard to 3 seconds")
    write(p, data)
    guard_marker = "unknown"

# A missing capture timestamp is not fresh evidence. Fix any unsafe resume frame-token gates.
p, data = read(main)
fresh_pattern = re.compile(
    r"(?m)^([ \t]*[A-Za-z_]\w*\s*=\s*\(?)(token is None)\s+or\s+(token\s*!=\s*self\._resume_market_[A-Za-z0-9_]*_last_frame_token)(\)?)$"
)
data, n_fresh = fresh_pattern.subn(r"\g<1>token is not None and \g<3>\g<4>", data)
if n_fresh:
    write(p, data)
    print(f"Fixed {n_fresh} stale-token freshness checks")
p, data = read(main)
if re.search(r"(?m)^.*token is None\s+or\s+token\s*!=\s*self\._resume_market_.*_last_frame_token.*$", data):
    raise RuntimeError("A stale-token freshness expression remains in post-purchase recovery")

# Add a shared 3-second post-cancel cooldown.
p, data = read(main)
if "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00" not in data:
    if not set_numeric(main, "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS", 3.0,
                       anchor="POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS", optional=True):
        p, data = read(main)
        anchor_re = re.compile(r"(?m)^([ \t]*)POST_PURCHASE_CONFIRM_GRACE_SECONDS\s*=\s*3\.00$")
        ms = list(anchor_re.finditer(data))
        if len(ms) != 1:
            raise RuntimeError("Cannot safely add post-cancel cooldown constant")
        m = ms[0]
        write(p, data[:m.end()] + f"\n{m.group(1)}POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00" + data[m.end():])
        print("INSERTED post-cancel cooldown after confirm grace")
else:
    print("ALREADY SET POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS=3")

# Older archive variants reuse these named timers for retry and return-to-market grace.
set_numeric(main, "POST_PURCHASE_CANCEL_RETRY_SECONDS", 3.0, optional=True)
set_numeric(main, "POST_PURCHASE_CANCEL_BACK_GRACE_SECONDS", 3.0, optional=True)

# Retry Cancel and Back are gated by the 3-second post-cancel timer.
p, data = read(main)
retry_pattern = re.compile(
    r"(?ms)^([ \t]*retry_due\s*=\s*\(self\._resume_market_cancel_attempts\s*==\s*0\s+or\s+\(self\._resume_market_cancel_attempts\s*<\s*2\s+and\s+now\s*-\s*last_attempt\s*>=\s*)[0-9]+(?:\.[0-9]+)?(\)\))$"
)
ms = list(retry_pattern.finditer(data))
if len(ms) == 1:
    m = ms[0]
    data = data[:m.start()] + m.group(1) + "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS" + m.group(2) + data[m.end():]
    write(p, data)
    print("SET retry_due 3-second cooldown")
elif len(ms) == 0 and "retry_due" in data and ("POST_PURCHASE_CANCEL_RETRY_SECONDS" in data or "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS" in data):
    print("ALREADY SAFE: retry_due uses a named 3-second cancel cooldown")
elif len(ms) == 0 and "retry_due" in data and "now-last_attempt >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS" not in data:
    lines = data.splitlines(keepends=True)
    idx = next((i for i, x in enumerate(lines) if re.match(r"^[ \t]*retry_due\s*=", x)), None)
    if idx is not None:
        end = idx
        while end + 1 < len(lines) and (lines[end].rstrip().endswith("(") or lines[end].count("(") > lines[end].count(")")):
            end += 1
        block = "".join(lines[idx:end+1])
        block2, n = re.subn(r"(>=\s*)[0-9]+(?:\.[0-9]+)?", r"\g<1>POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS", block, count=1)
        if n != 1:
            raise RuntimeError(f"Could not patch retry_due cooldown:\n{block}")
        lines[idx:end+1] = [block2]
        write(p, "".join(lines))
        print("SET multiline retry_due cooldown")

p, data = read(main)
updated = False
for variable in ("cancel_back_grace_done", "cancel_cooldown_done"):
    data, ok = set_elapsed_assignment(data, variable, "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS")
    updated = updated or ok
if not updated:
    relevant = "\n".join(f"{i+1}: {line}" for i, line in enumerate(data.splitlines()) if "last_attempt" in line or "cancel_back_grace_done" in line or "cancel_cooldown_done" in line)
    raise RuntimeError("No supported cancel-to-back cooldown assignment found; refusing unpatched build:\n" + relevant)
write(p, data)

# Update regression-test timer expectations if this source version includes those tests.
for rel, replacements in {
    "tests/test_v1281_post_purchase_navigation.py": {
        "POST_PURCHASE_CONFIRM_GRACE_SECONDS": 3.0,
        "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS": 3.0,
        "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS": 3.0,
    },
    "tests/test_v1282_post_purchase_unknown_guard.py": {
        "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS": 3.0,
        "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS": 3.0,
    },
}.items():
    p, testdata = read(rel, required=False)
    if p is None:
        continue
    for name, value in replacements.items():
        rx = re.compile(rf"{re.escape(name)}\s*=\s*[0-9]+(?:\.[0-9]+)?")
        ms = list(rx.finditer(testdata))
        if len(ms) == 1:
            m = ms[0]
            testdata = testdata[:m.start()] + f"{name} = {value:.2f}" + testdata[m.end():]
        elif len(ms) > 1:
            raise RuntimeError(f"Duplicate test value for {name} in {rel}")
    write(p, testdata)
    print(f"Updated timer test expectations in {rel}")

# Optional release note update.
rel = "docs/release/POST_PURCHASE_NAVIGATION_v1.2.81_UA.txt"
p, doc = read(rel, required=False)
if p is not None:
    doc, _ = re.subn(r"модальне вікно має залишатися видимим щонайменше [0-9,.]+ с",
                     "модальне вікно має залишатися видимим щонайменше 3 с", doc, count=1)
    write(p, doc)

# Final assertions before this patched source is allowed to compile.
_, data = read(main)
for name in (
    "POST_PURCHASE_CONFIRM_GRACE_SECONDS",
    "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS",
    "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS",
    "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS",
):
    if not any(f"{name} = {value}" in data for value in ("3.00", "3.0", "3")):
        raise RuntimeError(f"Final validation failed: {name} is not set to 3 seconds")
if guard_marker == "nonmodal":
    if not any(s in data for s in (
        "POST_PURCHASE_NONMODAL_AFTER_CONFIRM_MIN_SECONDS = 3.00",
        "POST_PURCHASE_NONMODAL_AFTER_CONFIRM_MIN_SECONDS = 3.0",
        "and nonmodal_for >= 3.00",
        "and nonmodal_for >= 3.0",
    )):
        raise RuntimeError("Final validation failed: nonmodal 3-second guard missing")
else:
    if not any(s in data for s in (
        "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS = 3.00",
        "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS = 3.0",
        "and unknown_for >= 3.00",
        "and unknown_for >= 3.0",
    )):
        raise RuntimeError("Final validation failed: unknown 3-second guard missing")
print("Post-purchase 3-second hotfix applied and verified for", guard_marker, "source variant.")
