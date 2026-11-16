#!/usr/bin/env python3
"""Apply the post-purchase 3-second safety hotfix to an extracted NGMS source tree."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_post_purchase_hotfix.py SOURCE_ROOT")
root = Path(sys.argv[1]).resolve()

def replace_once(rel, old, new):
    p = root / rel
    if not p.is_file():
        raise RuntimeError(f"Required source file missing: {rel}")
    data = p.read_text(encoding="utf-8")
    old_count = data.count(old)
    if new in data:
        print(f"ALREADY PATCHED {rel}: {new.strip()[:95]}")
    elif old_count == 1:
        data = data.replace(old, new, 1)
        p.write_text(data, encoding="utf-8", newline="\n")
        print(f"PATCHED {rel}: {old.strip()[:95]}")
    else:
        raise RuntimeError(
            f"Unexpected source shape in {rel}: old matches={old_count}; refusing unsafe patch"
        )

main = "main.py"
constants = [
    ("POST_PURCHASE_CONFIRM_GRACE_SECONDS = 1.10", "POST_PURCHASE_CONFIRM_GRACE_SECONDS = 3.00"),
    ("POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS = 0.85", "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS = 3.00"),
    ("POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS = 1.05", "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS = 3.00"),
    ("POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS = 0.45", "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS = 3.00"),
    ("POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS = 1.50", "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS = 3.00"),
]
for old, new in constants:
    replace_once(main, old, new)
replace_once(
    main,
    "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS = 3.00",
    "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS = 3.00\nPOST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00",
)

# A missing capture timestamp is not fresh evidence and must never advance a safety gate.
replace_once(main, "fresh = token is None or token != self._resume_market_last_frame_token",
             "fresh = token is not None and token != self._resume_market_last_frame_token")
replace_once(main, "fresh_confirm_frame = (token is None or token != self._resume_market_confirm_last_frame_token)",
             "fresh_confirm_frame = (token is not None and token != self._resume_market_confirm_last_frame_token)")
replace_once(main, "fresh_unknown_frame = (token is None or token != self._resume_market_unknown_after_confirm_last_frame_token)",
             "fresh_unknown_frame = (token is not None and token != self._resume_market_unknown_after_confirm_last_frame_token)")
replace_once(main, "retry_due = (self._resume_market_cancel_attempts == 0\n                         or (self._resume_market_cancel_attempts < 2 and now-last_attempt >= 0.65))",
             "retry_due = (self._resume_market_cancel_attempts == 0\n                         or (self._resume_market_cancel_attempts < 2 and now-last_attempt >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS))")
replace_once(main, "cancel_cooldown_done = not last_attempt or (now - last_attempt) >= 0.28",
             "cancel_cooldown_done = not last_attempt or (now - last_attempt) >= POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS")

# Align source-level regression tests with the release behavior.
replace_once("tests/test_v1281_post_purchase_navigation.py",
             "POST_PURCHASE_CONFIRM_GRACE_SECONDS = 1.10", "POST_PURCHASE_CONFIRM_GRACE_SECONDS = 3.00")
replace_once("tests/test_v1281_post_purchase_navigation.py",
             "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS = 0.85", "POST_PURCHASE_DETAIL_BACK_MIN_WAIT_SECONDS = 3.00")
replace_once("tests/test_v1281_post_purchase_navigation.py",
             "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS = 1.05", "POST_PURCHASE_UNKNOWN_BACK_MIN_WAIT_SECONDS = 3.00")
replace_once("tests/test_v1282_post_purchase_unknown_guard.py",
             "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS = 0.45", "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_MIN_SECONDS = 3.00")
replace_once("tests/test_v1282_post_purchase_unknown_guard.py",
             "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS = 1.50", "POST_PURCHASE_UNKNOWN_AFTER_CONFIRM_BACK_GRACE_SECONDS = 3.00")
replace_once("tests/test_v1282_post_purchase_unknown_guard.py",
             "        self.assertIn('or bool(self._resume_market_modal_absence_confirmed)', resume)",
             "        self.assertIn('or bool(self._resume_market_modal_absence_confirmed)', resume)\n"
             "        self.assertIn('fresh = token is not None and token != self._resume_market_last_frame_token', resume)\n"
             "        self.assertIn('fresh_confirm_frame = (token is not None and token != self._resume_market_confirm_last_frame_token)', resume)\n"
             "        self.assertIn('fresh_unknown_frame = (token is not None and token != self._resume_market_unknown_after_confirm_last_frame_token)', resume)\n"
             "        self.assertIn('POST_PURCHASE_CANCEL_TO_BACK_GRACE_SECONDS = 3.00', self.main)")

replace_once("docs/release/POST_PURCHASE_NAVIGATION_v1.2.81_UA.txt",
             "модальне вікно має залишатися видимим щонайменше 1,1 с",
             "модальне вікно має залишатися видимим щонайменше 3 с")
replace_once("docs/release/POST_PURCHASE_NAVIGATION_v1.2.81_UA.txt",
             "- Усі навігаційні дії запускаються після завершення потоку покупки; для «Назад» діють окремі мінімальні паузи, а сканування поновлюється лише після двох свіжих кадрів каталогу.",
             "- Після «Скасувати» витримується ще щонайменше 3 с перед «Назад»; повторна спроба «Скасувати» також не раніше ніж через 3 с. Усі кліки навігації виконуються після завершення потоку покупки.\n- Кадр без timestamp не зараховується як свіжий доказ. Сканування поновлюється лише після двох кадрів каталогу з різними свіжими timestamp.")
print("Post-purchase 3-second hotfix applied and all expected markers validated.")
