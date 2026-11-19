"""Visible-control adapters for the site's Mobiscroll 3.2 date/country pickers.

Only DOM reads run in JavaScript. Changes use visible controls, arrow keys and
the picker's own Set button; no hidden value or Mobiscroll API is written.
"""
from __future__ import annotations

import re
import time
from datetime import date

from playwright.sync_api import Error as BrowserError

TIMEOUT_MS = 3000


def _text(value: str) -> str:
    return ' '.join(value.split())


def _usable(control) -> bool:
    if control.count() != 1 or not control.is_visible() or control.is_disabled():
        return False
    classes = (control.get_attribute('class') or '').split()
    return (control.get_attribute('aria-disabled') != 'true'
            and not set(classes) & {'disabled', 'mbsc-fr-btn-d', 'mbsc-btn-d'})


def _row_ready(row) -> bool:
    return (_usable(row) and row.get_attribute('aria-disabled') != 'true'
            and 'disabled' not in (row.get_attribute('class') or '').split())


def _wait(page, predicate, timeout_ms: int = TIMEOUT_MS) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(30)


def _open(page, opener):
    frames = page.locator('.mbsc-fr:visible')
    if frames.count():
        return None
    opener.click(timeout=TIMEOUT_MS)
    frames.first.wait_for(state='visible', timeout=TIMEOUT_MS)
    if frames.count() != 1:
        return None
    frame = frames.first
    # Visible markup precedes the real entry animation and its final dialog
    # focus(). Starting search before that focus can send keyup to the dialog
    # instead of the search input, so this plugin never runs its filter.
    def ready():
        return frames.count() == 1 and frame.evaluate('''el =>
          !el.matches('.mbsc-anim-in,.mbsc-anim-out,.mbsc-anim-trans') &&
          el.contains(document.activeElement)''')
    if not _wait(page, ready):
        _cancel(frame)
        return None
    return frame


def _cancel(frame) -> None:
    if frame is None:
        return
    try:
        cancel = frame.locator('.mbsc-fr-btn-c .mbsc-fr-btn[role="button"]')
        if _usable(cancel):
            cancel.click(timeout=TIMEOUT_MS)
            frame.wait_for(state='hidden', timeout=TIMEOUT_MS)
    except BrowserError:
        pass


def _accept(frame) -> bool:
    button = frame.locator('.mbsc-fr-btn-s .mbsc-fr-btn[role="button"]')
    if not _usable(button):
        return False
    button.click(timeout=TIMEOUT_MS)
    frame.wait_for(state='hidden', timeout=TIMEOUT_MS)
    return True


def _wheel_value(wheel) -> int | None:
    # Circular wheels repeat the same selected value in several DOM batches.
    values = wheel.locator('.mbsc-sc-itm-sel').evaluate_all(
        'els => [...new Set(els.map(el => el.getAttribute("data-val")))]')
    if len(values) != 1 or not isinstance(values[0], str) or not values[0].isdigit():
        return None
    return int(values[0])


def _move_wheel(page, wheel, target: int, max_steps: int) -> str:
    if not _usable(wheel):
        return 'manual_control'
    for _ in range(max_steps + 1):
        # The selected class changes before the virtual wheel's current index.
        # Sending another key during its animation can repeat the old index.
        if not _wait(page, lambda: 'mbsc-sc-whl-anim' not in
                     (wheel.get_attribute('class') or '').split()):
            return 'manual_control'
        current = _wheel_value(wheel)
        if current is None:
            return 'manual_control'
        if current == target:
            return 'filled'
        if _ == max_steps:
            return 'manual_control'
        wheel.press('ArrowUp' if current > target else 'ArrowDown', timeout=TIMEOUT_MS)
        if not _wait(page, lambda: _wheel_value(wheel) != current, timeout_ms=1500):
            return 'option_missing'
    return 'manual_control'


def select_birthday(page, row, value: str) -> str:
    """Select an ISO birthday through the visible year/month/day listboxes."""
    frame = None
    try:
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            return 'option_missing'
        try:
            wanted = date.fromisoformat(value)
        except ValueError:
            return 'option_missing'
        control = row.locator('input[name="birthday"]')
        if not _row_ready(row) or not _usable(control):
            return 'manual_control'
        if control.input_value() == value:
            return 'already_set'
        if 'mbsc-comp' not in (control.get_attribute('class') or '').split():
            return 'manual_control'
        frame = _open(page, control)
        if frame is None or 'mbsc-dt' not in (frame.get_attribute('class') or '').split():
            return 'manual_control'
        wheels = [frame.locator(f'.mbsc-dt-whl-{part} .mbsc-sc-whl[role="listbox"]')
                  for part in ('y', 'm', 'd')]
        targets = (wanted.year, wanted.month - 1, wanted.day)
        # Year wheels are virtualized; ArrowUp/Down works outside the rendered
        # viewport. The cap also bounds unexpected/unsupported picker ranges.
        for wheel, target, cap in zip(wheels, targets, (250, 12, 31)):
            status = _move_wheel(page, wheel, target, cap)
            if status != 'filled':
                return status
        if tuple(_wheel_value(wheel) for wheel in wheels) != targets:
            return 'not_confirmed'
        if not _accept(frame):
            return 'manual_control'
        return ('filled' if _wait(page, lambda: control.input_value() == value)
                else 'not_confirmed')
    except BrowserError:
        # Call logs may include the supplied value. Do not print or retain them.
        return 'manual_control'
    finally:
        _cancel(frame)


def _country_items(wheel) -> list[dict]:
    return wheel.locator('.mbsc-sc-itm[role="option"]').evaluate_all('''els =>
      els.map((el, index) => ({index, value:el.getAttribute('data-val'),
        text:el.textContent, disabled:el.classList.contains('mbsc-btn-d') ||
          el.classList.contains('mbsc-sc-itm-inv') ||
          el.getAttribute('aria-disabled') === 'true',
        placeholder:el.classList.contains('mbsc-sc-itm-ph'),
        selected:el.classList.contains('mbsc-sc-itm-sel')}))''')


def select_country(page, row, value: str) -> str:
    """Select an exact native option using Mobiscroll's visible search and Set."""
    frame = None
    try:
        native = row.locator('select[name="country"]')
        if (not isinstance(value, str) or not value or not _row_ready(row)
                or native.count() != 1 or native.is_disabled()
                or native.get_attribute('aria-disabled') == 'true'
                or native.get_attribute('multiple') is not None):
            return 'manual_control'
        options = native.locator('option').evaluate_all('''els => els.map(el => ({
          value:el.value, text:el.textContent,
          disabled:el.disabled || !!el.closest('optgroup[disabled]')}))''')
        matches = [item for item in options if item['value'] == value]
        if len(matches) != 1 or matches[0]['disabled'] or not _text(matches[0]['text']):
            return 'option_missing'
        label = _text(matches[0]['text'])
        native_classes = (native.get_attribute('class') or '').split()
        # Mobiscroll clips its select to 1px rather than display:none. Playwright
        # still reports that element visible; the plugin marker takes priority.
        if 'mbsc-sel-hdn' not in native_classes and native.is_visible():
            if native.input_value() == value:
                return 'already_set'
            native.select_option(value=value, timeout=TIMEOUT_MS)
            return 'filled' if native.input_value() == value else 'not_confirmed'
        opener = row.locator('input.mbsc-control')
        native_id = native.get_attribute('id')
        if (not _usable(opener) or not native_id
                or opener.get_attribute('id') != native_id + '_dummy'
                or 'mbsc-sel-hdn' not in (native.get_attribute('class') or '').split()):
            return 'manual_control'
        readback = lambda: (native.input_value() == value
                            and _text(opener.input_value()) == label)
        if readback():
            return 'already_set'
        frame = _open(page, opener)
        if frame is None or 'mbsc-sel' not in (frame.get_attribute('class') or '').split():
            return 'manual_control'
        search = frame.locator('input.mbsc-sel-filter-input')
        wheel = frame.locator('.mbsc-sc-whl[role="listbox"]')
        if not _usable(search) or not search.is_editable() or not _usable(wheel):
            return 'manual_control'
        search.fill(label, timeout=TIMEOUT_MS)
        # This exact plugin refreshes on a 500 ms keyup debounce, not input.
        search.press('End', timeout=TIMEOUT_MS)
        def filtered():
            items = [item for item in _country_items(wheel) if not item['placeholder']]
            return bool(items) and all(label.casefold() in _text(item['text']).casefold()
                                       for item in items)
        if not _wait(page, filtered):
            return 'manual_control'
        choices = [item for item in _country_items(wheel)
                   if item['value'] == value and not item['disabled'] and not item['placeholder']]
        if len(choices) != 1 or _text(choices[0]['text']) != label:
            return 'option_missing'
        option = wheel.locator('.mbsc-sc-itm[role="option"]').nth(choices[0]['index'])
        if not _usable(option) or option.get_attribute('data-val') != value:
            return 'manual_control'
        option.click(timeout=TIMEOUT_MS)
        def selected():
            items = [item for item in _country_items(wheel) if item['selected']]
            return len(items) == 1 and items[0]['value'] == value
        if not _wait(page, selected):
            return 'not_confirmed'
        if not _accept(frame):
            return 'manual_control'
        return 'filled' if _wait(page, readback) else 'not_confirmed'
    except BrowserError:
        return 'manual_control'
    finally:
        _cancel(frame)
