#!/usr/bin/env python3
"""Apply the post-purchase 3-second safety hotfix to an extracted NGMS source tree."""
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


def set_numeric_constant(rel, name, value, insert_after=None, optional=False):
    p, data = read(rel)
    pattern = re.compile(rf"(?m)^([ \t]*){re.escape(name)}\s*=\s*([0-9]+(?:\.[0-9]+)?)(?:\s+#.*)?$")
    matches = list(pattern.finditer(data))
    if len(matches) == 1:
        m = matches[0]
        if float(m.group(2)) == float(value):
            print(f"ALREADY PATCHED {rel}: {name}={value:.2f}")
            return True
        replacement = f"{m.group(1)}{name} = {value:.2f}"
        write(p, data[:m.start()] + replacement + data[m.end():])
        print(f"PATCHED {rel}: {name} -> {value:.2f}")
        return True
    if len(matches) > 1:
        raise RuntimeError(f"Expected at most one numeric constant {name} in {rel}, found {len(matches)}")
    if not insert_after:
        if optional:
            print(f"INLINE-GUARD FALLBACK {rel}: {name} not present; patching the comparison literal instead")
            return False
        raise RuntimeError(f"Missing required constant {name} in {rel}")
    anchor = re.compile(rf"(?m)^([ \t]*){re.escape(insert_after)}\s*=\s*[0-9]+(?:\.[0-9]+)?(?:\s+#.*)?$")
    anchors = list(anchor.finditer(data))
    if len(anchors) != 1:
        if optional:
            print(f"INLINE-GUARD FALLBACK {rel}: anchor {insert_after!r} missing; patching the comparison literal instead")
            return False
        raise RuntimeError(f"Cannot safely insert {name}; anchor {insert_after!r} found {len(anchors)} times")
    m = anchors[0]
    insertion = f"\n{m.group(1)}{name} = {value:.2f}"
    write(p, data[:m.end()] + insertion + data[m.end():])
    print(f"INSERTED {rel}: {name} = {value:.2f}")
    return True


def replace_line(rel, pattern, replacement, label, already_pattern=None, required=True):
    p, data = read(rel, required=required)
    if p is None:
        print(f"SKIPPED optional file missing: {rel}")
        return
    regex = re.compile(pattern, re.MULTILINE)
    matches = list(regex.finditer(data))
    if len(matches) == 0:
        if already_pattern and re.search(already_pattern, data, re.MULTILINE):
            print(f"ALREADY PATCHED {rel}: {label}")
            return
        if not required:
            print(f"SKIPPED optional source variation: {rel}: {label}")
            return
        # Print concise relevant context in the CI log for source variants, but fail closed.
        lines = data.splitlines()
        keywords = ("unknown", "confirm", "cancel_cooldown", "retry_due", "fresh_frame", "last_frame_token")
        context = [f"{i+1}: {line}" for i, line in enumerate(lines) if any(k in line.lower() for k in keywords)]
        raise RuntimeError(f"Missing expected line in {rel}: {label}. Relevant source lines:\n" + "\n".join(context[-70:]))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one matching line in {rel} for {label}, found {len(matches)}")
    m = matches[0]
    updated = data[:m.start()] + m.expand(replacement) + data[m.end():]
    if updated != data:
        write(p, updated)
    print(f"PATCHED {rel}: {label}")


main = "main.py"
for name in (
    "POST_PURCHASE_CONFIRM_GRACE_SECONDS",
    "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS",
    "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS",
):
    set_numeric_constant(main, name, 3.0)

# Some older v1.2.81 archives used inline literals instead of these named guards.
min_guard_named = set_numeric_constant(
    main, "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS", 3.0,
    insert_after="POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_FRESH_FRAMES", optional=True)
back_guard_anchor = ("POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS" if min_guard_named
                     else "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS")
back_guard_named = set_numeric_constant(
    main, "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS", 3.0,
    insert_after=back_guard_anchor, optional=True)
set_numeric_constant(main, "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS", 3.0,
                     insert_after="POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS")

# Ensure the guard expressions use a 3-second bound even when the archive stored inline literals.
unknown_guard_target = ("POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS" if min_guard_named else "3.00")
back_guard_target = ("POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS" if back_guard_named else "3.00")
replace_line(
    main,
    r"^([ \t]*and unknown_for\s*>=\s*)(?:[0-9]+(?:\.[0-9]+)?|POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS)(\s*)$",
    rf"\g<1>{unknown_guard_target}\g<2>",
    "unknown-state minimum guard",
    already_pattern=rf"(?m)^\s*and unknown_for\s*>=\s*{re.escape(unknown_guard_target)}\s*$",
)
replace_line(
    main,
    r"^([ \t]*and confirm_seen_for\s*>=\s*)(?:[0-9]+(?:\.[0-9]+)?|POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS)(\s*)$",
    rf"\g<1>{back_guard_target}\g<2>",
    "modal-seen minimum guard",
    already_pattern=rf"(?m)^\s*and confirm_seen_for\s*>=\s*{re.escape(back_guard_target)}\s*$",
)

# A missing capture timestamp is not fresh evidence and must never advance a safety gate.
frame_assignments = [
    (r"^([ \t]*)fresh\s*=\s*.*self\._resume_market_last_frame_token.*$",
     r"\1fresh = token is not None and token != self._resume_market_last_frame_token",
     "market fresh-frame gate",
     r"(?m)^\s*fresh = token is not None and token != self\._resume_market_last_frame_token$"),
    (r"^([ \t]*)fresh_confirm_frame\s*=\s*.*self\._resume_market_confirm_last_frame_token.*$",
     r"\1fresh_confirm_frame = (token is not None and token != self._resume_market_confirm_last_frame_token)",
     "confirm fresh-frame gate",
     r"(?m)^\s*fresh_confirm_frame = \(token is not None and token != self\._resume_market_confirm_last_frame_token\)$"),
    (r"^([ \t]*)fresh_unknown_frame\s*=\s*.*self\._resume_market_unknown_after_confirm_last_frame_token.*$",
     r"\1fresh_unknown_frame = (token is not None and token != self._resume_market_unknown_after_confirm_last_frame_token)",
     "unknown fresh-frame gate",
     r"(?m)^\s*fresh_unknown_frame = \(token is not None and token != self\._resume_market_unknown_after_confirm_last_frame_token\)$"),
]
for pattern, replacement, label, already in frame_assignments:
    replace_line(main, pattern, replacement, label, already_pattern=already)

# Retry Cancel no sooner than 3 seconds after the preceding attempt.
replace_line(
    main,
    r"^([ \t]*)retry_due\s*=\s*\(self\._resume_market_cancel_attempts\s*==\s*0\s+or\s+\(self\._resume_market_cancel_attempts\s*<\s*2\s+and\s+now\s*-\s*last_attempt\s*>=\s*[0-9.]+\)\)",
    r"\1retry_due = (self._resume_market_cancel_attempts == 0\n\1             or (self._resume_market_cancel_attempts < 2 and now-last_attempt >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS))",
    "cancel retry cooldown",
    already_pattern=r"(?m)^\s*retry_due = \(self\._resume_market_cancel_attempts == 0\n\s+or \(self\._resume_market_cancel_attempts < 2 and now-last_attempt >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS\)\)$",
)
replace_line(
    main,
    r"^([ \t]*)cancel_cooldown_done\s*=\s*not last_attempt or \(now\s*-\s*last_attempt\)\s*>=\s*[0-9.]+$",
    r"\1cancel_cooldown_done = not last_attempt or (now - last_attempt) >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS",
    "cancel-to-back cooldown",
    already_pattern=r"(?m)^\s*cancel_cooldown_done = not last_attempt or \(now - last_attempt\) >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS$",
)

# Update regression-test expectations to match the release behavior; tolerate older test inventories.
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
    p, data = read(rel, required=False)
    if p is None:
        continue
    for name, value in replacements.items():
        regex = re.compile(rf"{re.escape(name)} = [0-9]+(?:\.[0-9]+)?")
        matches = list(regex.finditer(data))
        if len(matches) == 1:
            m = matches[0]
            updated = f"{name} = {value:.2f}"
            data = data[:m.start()] + updated + data[m.end():]
        elif len(matches) > 1:
            raise RuntimeError(f"Expected at most one test expectation for {name} in {rel}, found {len(matches)}")
    needle = "        self.assertIn('or bool(self._resume_market_modal_absence_confirmed)', resume)"
    assertions = (
        "        self.assertIn('fresh = token is not None and token != self._resume_market_last_frame_token', resume)\n"
        "        self.assertIn('fresh_confirm_frame = (token is not None and token != self._resume_market_confirm_last_frame_token)', resume)\n"
        "        self.assertIn('fresh_unknown_frame = (token is not None and token != self._resume_market_unknown_after_confirm_last_frame_token)', resume)\n"
        "        self.assertIn('POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00', self.main)"
    )
    if needle in data and assertions not in data:
        data = data.replace(needle, needle + "\n" + assertions, 1)
    write(p, data)
    print(f"PATCHED optional regression test expectations: {rel}")

# Keep release notes consistent where the file exists.
rel = "docs/release/POST_PURCHASE_NAVIGATION_v1.2.81_UA.txt"
p, data = read(rel, required=False)
if p is not None:
    data, _ = re.subn(r"модальне вікно має залишатися видимим щонайменше [0-9,.]+ с", "модальне вікно має залишатися видимим щонайменше 3 с", data, count=1)
    old_bullet = "- Усі навігаційні дії запускаються після завершення потоку покупки; для «Назад» діють окремі мінімальні паузи, а сканування поновлюється лише після двох свіжих кадрів каталогу."
    new_bullet = "- Після «Скасувати» витримується ще щонайменше 3 с перед «Назад»; повторна спроба «Скасувати» також не раніше ніж через 3 с. Усі кліки навігації виконуються після завершення потоку покупки.\n- Кадр без timestamp не зараховується як свіжий доказ. Сканування поновлюється лише після двох кадрів каталогу з різними свіжими timestamp."
    if old_bullet in data:
        data = data.replace(old_bullet, new_bullet, 1)
    elif new_bullet not in data:
        print("NOTE: release note wording differs; runtime source remains authoritative.")
    write(p, data)

# Final source validation prevents silently building an unpatched executable.
_, data = read(main)
for fragment in (
    "POST_PURCHASE_CONFIRM_GRACE_SECONDS = 3.00",
    "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS = 3.00",
    "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS = 3.00",
    "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00",
    f"and unknown_for >= {unknown_guard_target}",
    f"and confirm_seen_for >= {back_guard_target}",
    "fresh = token is not None and token != self._resume_market_last_frame_token",
    "fresh_confirm_frame = (token is not None and token != self._resume_market_confirm_last_frame_token)",
    "fresh_unknown_frame = (token is not None and token != self._resume_market_unknown_after_confirm_last_frame_token)",
):
    if fragment not in data:
        raise RuntimeError(f"Final source validation failed: {fragment}")
print("Post-purchase 3-second hotfix applied and source markers validated.")
