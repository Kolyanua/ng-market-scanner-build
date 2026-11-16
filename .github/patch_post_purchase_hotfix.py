#!/usr/bin/env python3
"""Apply the post-purchase 3-second safety hotfix to an extracted NGMS source tree."""
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_post_purchase_hotfix.py SOURCE_ROOT")
root = Path(sys.argv[1]).resolve()


def read(rel):
    p = root / rel
    if not p.is_file():
        raise RuntimeError(f"Required source file missing: {rel}")
    return p, p.read_text(encoding="utf-8")


def write(p, data):
    p.write_text(data, encoding="utf-8", newline="\n")


def set_numeric_constant(rel, name, value):
    p, data = read(rel)
    pattern = re.compile(rf"(?m)^([ \t]*){re.escape(name)}\s*=\s*([0-9]+(?:\.[0-9]+)?)(?:\s+#.*)?$")
    matches = list(pattern.finditer(data))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one numeric constant {name} in {rel}, found {len(matches)}")
    m = matches[0]
    if float(m.group(2)) == float(value):
        print(f"ALREADY PATCHED {rel}: {name}={value:.2f}")
        return
    replacement = f"{m.group(1)}{name} = {value:.2f}"
    data = data[:m.start()] + replacement + data[m.end():]
    write(p, data)
    print(f"PATCHED {rel}: {name} -> {value:.2f}")


def replace_line(rel, pattern, replacement, label):
    p, data = read(rel)
    regex = re.compile(pattern, re.MULTILINE)
    matches = list(regex.finditer(data))
    if len(matches) == 0:
        already_patterns = {
            "market fresh-frame gate": r"(?m)^\s*fresh = token is not None and token != self\._resume_market_last_frame_token$",
            "confirm fresh-frame gate": r"(?m)^\s*fresh_confirm_frame = \(token is not None and token != self\._resume_market_confirm_last_frame_token\)$",
            "unknown fresh-frame gate": r"(?m)^\s*fresh_unknown_frame = \(token is not None and token != self\._resume_market_unknown_after_confirm_last_frame_token\)$",
            "cancel retry cooldown": r"(?m)^\s*retry_due = \(self\._resume_market_cancel_attempts == 0$",
            "cancel-to-back cooldown": r"(?m)^\s*cancel_cooldown_done = not last_attempt or \(now - last_attempt\) >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS$",
        }
        already = already_patterns.get(label)
        if already and re.search(already, data):
            print(f"ALREADY PATCHED {rel}: {label}")
            return
        raise RuntimeError(f"Missing expected line in {rel}: {label}")
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
    "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS",
    "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS",
):
    set_numeric_constant(main, name, 3.0)

# Add a dedicated 3-second cooldown after clicking Cancel before Back is allowed.
p, data = read(main)
if re.search(r"(?m)^POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS\s*=", data):
    set_numeric_constant(main, "POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS", 3.0)
else:
    pattern = re.compile(r"(?m)^([ \t]*)POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS\s*=\s*[0-9]+(?:\.[0-9]+)?(?:\s+#.*)?$")
    matches = list(pattern.finditer(data))
    if len(matches) != 1:
        raise RuntimeError("Cannot place POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS safely")
    m = matches[0]
    line = m.group(0)
    replacement = line + f"\n{m.group(1)}POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00"
    write(p, data[:m.start()] + replacement + data[m.end():])
    print("PATCHED main.py: added POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS=3.00")

# A missing capture timestamp is not fresh evidence and must never advance a safety gate.
frame_assignments = [
    (r"^([ \t]*)fresh\s*=\s*.*self\._resume_market_last_frame_token.*$",
     r"\1fresh = token is not None and token != self._resume_market_last_frame_token",
     "market fresh-frame gate"),
    (r"^([ \t]*)fresh_confirm_frame\s*=\s*.*self\._resume_market_confirm_last_frame_token.*$",
     r"\1fresh_confirm_frame = (token is not None and token != self._resume_market_confirm_last_frame_token)",
     "confirm fresh-frame gate"),
    (r"^([ \t]*)fresh_unknown_frame\s*=\s*.*self\._resume_market_unknown_after_confirm_last_frame_token.*$",
     r"\1fresh_unknown_frame = (token is not None and token != self._resume_market_unknown_after_confirm_last_frame_token)",
     "unknown fresh-frame gate"),
]
for pattern, replacement, label in frame_assignments:
    replace_line(main, pattern, replacement, label)

# Retry Cancel no sooner than 3 seconds after the preceding attempt.
replace_line(
    main,
    r"^([ \t]*)retry_due\s*=\s*\(self\._resume_market_cancel_attempts\s*==\s*0\s+or\s+\(self\._resume_market_cancel_attempts\s*<\s*2\s+and\s+now\s*-\s*last_attempt\s*>=\s*[0-9.]+\)\)",
    r"\1retry_due = (self._resume_market_cancel_attempts == 0\n\1             or (self._resume_market_cancel_attempts < 2 and now-last_attempt >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS))",
    "cancel retry cooldown",
)
replace_line(
    main,
    r"^([ \t]*)cancel_cooldown_done\s*=\s*not last_attempt or \(now\s*-\s*last_attempt\)\s*>=\s*[0-9.]+$",
    r"\1cancel_cooldown_done = not last_attempt or (now - last_attempt) >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS",
    "cancel-to-back cooldown",
)

# Update regression-test expectations to match the release behavior.
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
    p, data = read(rel)
    for name, value in replacements.items():
        regex = re.compile(rf"{re.escape(name)} = [0-9]+(?:\.[0-9]+)?")
        matches = list(regex.finditer(data))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one test expectation for {name} in {rel}, found {len(matches)}")
        m = matches[0]
        updated = f"{name} = {value:.2f}"
        data = data[:m.start()] + updated + data[m.end():]
    write(p, data)
    print(f"PATCHED regression test expectations: {rel}")

p, data = read("tests/test_v1282_post_purchase_unknown_guard.py")
needle = "        self.assertIn('or bool(self._resume_market_modal_absence_confirmed)', resume)"
assertions = (
    "        self.assertIn('fresh = token is not None and token != self._resume_market_last_frame_token', resume)\n"
    "        self.assertIn('fresh_confirm_frame = (token is not None and token != self._resume_market_confirm_last_frame_token)', resume)\n"
    "        self.assertIn('fresh_unknown_frame = (token is not None and token != self._resume_market_unknown_after_confirm_last_frame_token)', resume)\n"
    "        self.assertIn('POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00', self.main)"
)
if assertions not in data:
    if needle not in data:
        raise RuntimeError("Cannot add fresh-frame assertions to post-purchase regression test")
    data = data.replace(needle, needle + "\n" + assertions, 1)
    write(p, data)
    print("PATCHED regression tests: missing frame tokens are not fresh")
else:
    print("ALREADY PATCHED regression tests: fresh-frame safety assertions")

# Keep the shipped release notes consistent with the actual binary.
rel = "docs/release/POST_PURCHASE_NAVIGATION_v1.2.81_UA.txt"
p, data = read(rel)
data, n1 = re.subn(r"модальне вікно має залишатися видимим щонайменше [0-9,.]+ с", "модальне вікно має залишатися видимим щонайменше 3 с", data, count=1)
old_bullet = "- Усі навігаційні дії запускаються після завершення потоку покупки; для «Назад» діють окремі мінімальні паузи, а сканування поновлюється лише після двох свіжих кадрів каталогу."
new_bullet = "- Після «Скасувати» витримується ще щонайменше 3 с перед «Назад»; повторна спроба «Скасувати» також не раніше ніж через 3 с. Усі кліки навігації виконуються після завершення потоку покупки.\n- Кадр без timestamp не зараховується як свіжий доказ. Сканування поновлюється лише після двох кадрів каталогу з різними свіжими timestamp."
if old_bullet in data:
    data = data.replace(old_bullet, new_bullet, 1)
elif new_bullet in data:
    pass
else:
    raise RuntimeError("Could not update post-purchase release notes safely")
if data != p.read_text(encoding="utf-8"):
    write(p, data)
print("Post-purchase 3-second hotfix applied and source markers validated.")
