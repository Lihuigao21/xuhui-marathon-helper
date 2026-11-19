"""Westbund 10K: one browser, optional one timed refresh, ordinary-field autofill."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
TASK = HERE
ENTRY = 'https://www.xuhui-marathon.com/b/FrAJj2/entry.html'
PREFIX = '/b/FrAJj2/'
GROUP = '10公里跑'
# An explicit allowlist. Agreements, OTPs, uploads and payment have no fill action.
FIELDS = {
    '真实姓名': ('name', 'text'),
    '证件类型': ('idcardtype', 'select'),
    '证件号码': ('idcardnumber', 'text'),
    '性别': ('sex', 'choice'),
    '出生日期': ('birthday', 'text'),
    '手机号码': ('mobile', 'text'),
    '电子邮件': ('email', 'text'),
    '所在国家或地区': ('country', 'select'),
    '所在城市': ('city', 'city'),
    '所在地址': ('address', 'text'),
    '血型': ('blood', 'select'),
    '衣服尺码': ('clothesize', 'select'),
    '紧急联系人姓名': ('contactname', 'text'),
    '紧急联系人手机号码': ('contactmobile', 'text'),
}
MANUAL = ['参赛声明', '监护人声明（如适用）', '装备信息/成绩/资格确认', '验证码（如出现）', '最终核对、提交与付款']


class ProfileError(ValueError):
    pass


def parse_city(value: str) -> tuple[str, str, str]:
    parts = tuple(part.strip() for part in value.split('/'))
    if len(parts) != 3 or not all(parts):
        raise ProfileError('所在城市请填写“省/市/区县”，例如“上海市/上海市/徐汇区”。')
    return parts


def parse_target(value: str, now: datetime | None = None) -> datetime | None:
    if not value:
        return None
    try:
        target = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise ProfileError('开放时间格式错误，请用带时区的 ISO 时间，例如 2026-11-19T11:00:00+08:00。') from None
    if target.tzinfo is None:
        raise ProfileError('开放时间必须注明时区；北京时间使用 +08:00。')
    current = now or datetime.now(timezone.utc)
    if (target - current).total_seconds() > 7 * 86400:
        raise ProfileError('开放时间超过七天，请临近报名再启动。')
    return target


def read_profile(path: Path) -> tuple[dict[str, str], datetime | None]:
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProfileError('无法读取资料文件，请检查文件存在且为合法 UTF-8 JSON。') from None
    if not isinstance(data, dict) or set(data) - {'开放时间', '报名资料', '说明'}:
        raise ProfileError('资料文件顶层只允许：开放时间、报名资料、说明。')
    if not isinstance(data.get('开放时间', ''), str):
        raise ProfileError('开放时间必须是字符串。')
    values = data.get('报名资料')
    if not isinstance(values, dict) or set(values) - set(FIELDS):
        raise ProfileError('报名资料包含未知字段，请按模板填写；声明和验证码必须在页面手动操作。')
    if any(not isinstance(v, str) or len(v) > 500 for v in values.values()):
        raise ProfileError('每项资料必须是长度不超过500的字符串。')
    values = {k: v.strip() for k, v in values.items() if v.strip()}
    if '所在城市' in values:
        parse_city(values['所在城市'])
    for label in ('手机号码', '紧急联系人手机号码'):
        value = values.get(label)
        if value and (len(value) != 11 or not value.isascii() or not value.isdigit() or value[0] != '1'):
            raise ProfileError(f'{label}应为11位中国大陆手机号码。')
    value = values.get('出生日期')
    if value:
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            raise ProfileError('出生日期请使用 YYYY-MM-DD。') from None
    return values, parse_target(data.get('开放时间', ''))


def event_page(url: str, name: str | None = None) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname == 'www.xuhui-marathon.com'
            and parsed.port in (None, 443)
            and parsed.path == PREFIX + name) if name else (
                parsed.scheme == 'https' and parsed.hostname == 'www.xuhui-marathon.com'
                and parsed.port in (None, 443) and parsed.path in
                [PREFIX + x for x in ('entry.html', 'agree.html', 'reg.html')])


def registration_link(href: str) -> bool:
    url = urljoin(ENTRY, href)
    return event_page(url, 'reg.html') and parse_qs(urlsplit(url).query).get('subgroup') == [GROUP]


def wait_until(page, target: datetime, now=None, pause=None) -> None:
    """Local clock waiting only; never polls the website or starts a model."""
    now = now or (lambda: datetime.now(timezone.utc))
    pause = pause or page.wait_for_timeout
    while True:
        remaining = (target - now()).total_seconds()
        if remaining <= 0:
            return
        pause(min(remaining * 1000, 1000))


def enter_available_group(page) -> str:
    if not event_page(page.url, 'entry.html'):
        return 'manual'
    try:
        page.locator('.divEntryList').wait_for(state='visible', timeout=20000)
        page.wait_for_function("""() => {
          const box = document.querySelector('.divEntryList');
          return box && !box.querySelector('.loading') && box.innerText.trim().length;
        }""", timeout=20000)
    except BrowserTimeout:
        return 'not_ready'
    links = page.locator('a.aEntryItem')
    for i in range(links.count()):
        link = links.nth(i)
        if link.get_attribute('sub') != GROUP:
            continue
        classes = (link.get_attribute('class') or '').split()
        text = link.inner_text()
        if ('disabled' in classes or link.get_attribute('aria-disabled') == 'true'
                or any(word in text for word in ('名额已满', '未开始', '未开放', '已结束', '暂停'))):
            return 'unavailable'
        href = link.get_attribute('href') or ''
        if not registration_link(href) or not link.is_visible():
            return 'manual'
        # The only navigation click made by the helper. No consent/next/submit button.
        link.click(timeout=10000)
        return 'entered'
    return 'not_ready'


def select_value(page, row, field: str, value: str) -> str:
    select = row.locator(f'select[name="{field}"]')
    if select.count() != 1 or select.is_disabled():
        return 'manual_control'
    options = select.locator('option').evaluate_all(
        'els => els.map(x => ({value:x.value, text:x.textContent, disabled:x.disabled || !!x.closest("optgroup[disabled]")}))')
    found = [o for o in options if o['value'] == value and not o['disabled']]
    if len(found) != 1:
        return 'option_missing'
    if select.input_value() == value:
        return 'already_set'
    if select.is_visible():
        select.select_option(value=value, timeout=3000)
    else:
        # Official dropdownlist widget hides the native select and exposes this textbox.
        # Use its visible menu so native value AND displayed text follow its own handler.
        widget = row.locator('.bx_ui_select_type_dropdownlist')
        opener = widget.locator('.bx_ui_select_type_ddl_word')
        if widget.count() != 1 or opener.count() != 1 or not opener.is_visible():
            return 'manual_control'  # Country/mobile pickers stay manual.
        opener.click(timeout=3000)
        # The official widget inserts the body menu from a 10 ms timer after click.
        # Bound the wait; do not enumerate the whole page before the menu exists.
        menu = page.locator('.bx_ui_select_type_ddl_list:visible')
        try:
            menu.first.wait_for(state='visible', timeout=3000)
            choices = menu.first.locator('.bx_ui_select_type_ddl_list_item')
            exact = [choices.nth(i) for i in range(choices.count())
                     if choices.nth(i).inner_text().strip() == found[0]['text'].strip()
                     and 'disabled' not in (choices.nth(i).get_attribute('class') or '').split()]
        except BrowserTimeout:
            exact = []
        if len(exact) != 1:
            page.keyboard.press('Escape')
            return 'manual_control'
        exact[0].click(timeout=3000)
    return 'filled' if select.input_value() == value else 'not_confirmed'


def _city_name(value: str, column: str) -> str:
    # Only accept an unambiguous province/city suffix alias; districts stay exact.
    return value.removesuffix('省').removesuffix('市') if column != 'xian' else value


def _city_data(raw: str) -> dict[str, str] | None:
    # bxPub.serialize leaves these property names unquoted. Quote only known keys
    # and parse as JSON; never evaluate JavaScript from a hidden field.
    normalized = re.sub(r'([{,]\s*)(province|city|xian)(\s*:)', r'\1"\2"\3', raw)
    try:
        saved = json.loads(normalized)
    except (json.JSONDecodeError, TypeError):
        return None
    if (not isinstance(saved, dict) or set(saved) != {'province', 'city', 'xian'}
            or any(not isinstance(part, str) for part in saved.values())):
        return None
    return saved


def _city_readback(row, expected: dict[str, str], labels: list[str]) -> bool:
    saved = _city_data(row.locator('input[name="city"]').input_value())
    shown = ' '.join(row.locator('input[name="city_text"]').input_value().split())
    # The official widget omits repeated display labels, including municipalities.
    display = ' '.join(' '.join(dict.fromkeys(labels)).split())
    return saved == expected and shown == display


def select_city(page, row, value: str) -> str:
    try:
        parts = parse_city(value)
    except ProfileError:
        return 'invalid_city'
    opener = row.locator('input[name="city_text"]')
    hidden = row.locator('input[name="city"][type="hidden"]')
    if (opener.count() != 1 or hidden.count() != 1 or not opener.is_visible()
            or opener.is_disabled() or hidden.is_disabled()
            or 'disabled' in (row.get_attribute('class') or '').split()
            or page.locator('.bx_ui_area').count()):
        return 'manual_control'
    picker = page.locator('.bx_ui_area')
    try:
        opener.click(timeout=3000)
        picker.first.wait_for(state='visible', timeout=3000)
        if picker.count() != 1:
            return 'manual_control'
        # With a cached selection the official widget arms its save handler at 100ms.
        page.wait_for_timeout(150)
        expected, labels = {}, []
        for column, requested in zip(('province', 'city', 'xian'), parts):
            tab = picker.locator(f'.bx_ui_area_navi_item[n="{column}"]')
            if tab.count() != 1 or not tab.is_visible():
                return 'manual_control'
            tab.click(timeout=3000)
            picker.locator(f'.bx_ui_area_navi_item_selected[n="{column}"]').wait_for(
                state='visible', timeout=3000)
            options = picker.locator('.bx_ui_area_list_ul .bx_ui_area_list_li')
            options.first.wait_for(state='visible', timeout=3000)
            items = options.evaluate_all('els => els.map((el, index) => ({index, value: el.getAttribute("v")}))')
            matches = [item for item in items if item['value'] == requested]
            if not matches:
                matches = [item for item in items if isinstance(item['value'], str)
                           and _city_name(item['value'], column) == _city_name(requested, column)]
            if len(matches) != 1:
                return 'option_missing'
            selected = matches[0]
            option = options.nth(selected['index'])
            text = option.locator('.bx_ui_area_list_li_text')
            if (not option.is_visible() or option.get_attribute('v') != selected['value']
                    or option.is_disabled() or option.get_attribute('aria-disabled') == 'true'
                    or 'disabled' in (option.get_attribute('class') or '').split()
                    or text.count() != 1):
                return 'manual_control'
            expected[column] = selected['value']
            labels.append(text.inner_text().strip())
            option.click(timeout=3000)
        picker.wait_for(state='detached', timeout=3000)
        return 'filled' if _city_readback(row, expected, labels) else 'not_confirmed'
    finally:
        # Cancel only the picker opened above; never write hidden JSON ourselves.
        if picker.count() == 1:
            close = picker.locator('.bx_ui_area_close')
            if close.count() == 1 and close.is_visible():
                close.click(timeout=3000)
                picker.wait_for(state='detached', timeout=3000)


def fill_fields(page, values: dict[str, str], *, offline_test=False) -> list[dict[str, str]]:
    if not offline_test and not event_page(page.url, 'reg.html'):
        return [{'label': '报名表', 'status': 'wrong_page'}]
    results = []
    ordered_labels = [k for k in FIELDS if k in values] + [k for k in values if k not in FIELDS]
    for label in ordered_labels:
        value = values[label]
        if label not in FIELDS:
            results.append({'label': '未知字段', 'status': 'not_allowed'})
            continue
        field, kind = FIELDS[label]
        row = page.locator('.bx_ui_form_tab_n_' + field)
        status = 'missing_or_hidden'
        try:
            if row.count() == 1 and row.is_visible():
                if kind == 'city':
                    status = select_city(page, row, value)
                elif kind == 'select':
                    if field == 'clothesize':
                        # Event 6075's public extend.js rebuilds these options 500ms after sex changes.
                        page.wait_for_timeout(750)
                    status = select_value(page, row, field, value)
                elif kind == 'choice':
                    choices = row.locator(f'input[name="{field}"]')
                    matches = [choices.nth(i) for i in range(choices.count())
                               if choices.nth(i).input_value() == value]
                    if len(matches) == 1 and not matches[0].is_disabled():
                        item = matches[0]
                        if item.is_checked():
                            status = 'already_set'
                        elif item.is_visible():
                            item.check(timeout=3000)
                            status = 'filled' if item.is_checked() else 'not_confirmed'
                        else:
                            label_control = item.locator('xpath=..').locator('span.icon')
                            if label_control.count() == 1 and label_control.is_visible():
                                label_control.click(timeout=3000)
                                status = 'filled' if item.is_checked() else 'not_confirmed'
                            else:
                                status = 'manual_control'
                    else:
                        status = 'option_missing'
                else:
                    control = row.locator(f'input[name="{field}"]')
                    if (control.count() == 1 and control.is_visible() and control.is_editable()
                            and control.get_attribute('type') in (None, 'text', 'tel', 'email', 'date')):
                        control.fill(value, timeout=3000)
                        control.dispatch_event('change')
                        control.blur()
                        status = 'filled' if control.input_value() == value else 'not_confirmed'
                    else:
                        status = 'manual_control'
        except BrowserError:
            # Playwright exception call logs can contain input values: do not print/save them.
            status = 'manual_control'
        results.append({'label': label, 'status': status})
    return results


def await_form(page, timeout_seconds=1800) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not event_page(page.url):
            return False
        if event_page(page.url, 'reg.html') and page.locator('.bx_ui_form_tab_n_name').is_visible():
            return True
        # Observe local DOM/URL only; no refresh, endpoint calls or consent actions.
        page.wait_for_timeout(500)
    return False


def _submit_target(page):
    """Return one stable, visible submit target only while the official preview is visible."""
    if not event_page(page.url, 'reg.html'):
        return None
    preview = page.locator('.divBmFormView:visible')
    if preview.count() != 1:
        return None
    candidates = page.locator('a.aBtnSubmit, button.aBtnSubmit')
    if candidates.count() != 1:
        return None
    button = candidates.first
    if not button.is_visible():
        return None
    classes = (button.get_attribute('class') or '').split()
    if 'disabled' in classes or button.get_attribute('aria-disabled') == 'true':
        return None
    try:
        handle = button.element_handle(timeout=1000)
        if handle is None:
            return None
        signature = page.evaluate("el => ({tag:el.tagName, id:el.id, className:el.className, text:el.textContent})", handle)
        return handle, signature
    except BrowserError:
        return None


def confirm_and_click_submit(page, answer_reader=input) -> str:
    """Click only after exact confirmation and a second identity/preview check."""
    target = _submit_target(page)
    if target is None:
        return 'preview_not_ready'
    handle, signature = target
    try:
        answer = answer_reader('已到达预览页。最终提交会创建报名结果；必须输入 SUBMIT 并回车，其他内容取消：')
    except (EOFError, KeyboardInterrupt):
        return 'cancelled'
    if answer != 'SUBMIT':
        return 'cancelled'
    try:
        renewed = _submit_target(page)
        if renewed is None:
            return 'target_changed'
        renewed_handle, renewed_signature = renewed
        if renewed_signature != signature:
            return 'target_changed'
        same_element = page.evaluate('(els) => els[0] === els[1]', [handle, renewed_handle])
        if not same_element:
            return 'target_changed'
        handle.click(timeout=5000)
        page.wait_for_timeout(1000)
        return 'clicked'
    except BrowserError:
        return 'click_failed'


def assisted_submit(page, timeout_seconds=1800, answer_reader=input) -> str:
    print('请核对自动填写结果，手动补齐未填成功的字段并完成声明、验证码/排队，再在网页上点击“下一步/查看”进入预览。助手不会点击这些步骤。', flush=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if page.is_closed() or not event_page(page.url):
            return 'left_event'
        if event_page(page.url, 'reg.html'):
            button = page.locator('a.aBtnSubmit:visible, button.aBtnSubmit:visible').first
            if button.count() == 1 and button.is_visible():
                result = confirm_and_click_submit(page, answer_reader)
                print({'clicked': '已点击官方最终提交按钮', 'cancelled': '已取消最终提交',
                       'preview_not_ready': '预览页尚未出现最终提交按钮',
                       'submit_disabled': '最终提交按钮仍不可用', 'click_failed': '点击未完成',
                       'wrong_page': '页面已离开报名表', 'target_changed': '确认后预览或提交按钮发生变化，已取消点击'}.get(result, result), flush=True)
                return result
        page.wait_for_timeout(500)
    return 'preview_timeout'


def run(profile_path: Path, check_only=False, smoke=False, assisted=False) -> int:
    values, target = read_profile(profile_path)
    print(f'资料格式有效：{len(values)}项非空字段。程序不输出资料值。', flush=True)
    if check_only:
        return 0
    if smoke and values:
        raise ProfileError('只读检查只允许空资料模板。')
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(TASK / ('.smoke-profile' if smoke else '.browser-profile')),
            channel='msedge', headless=smoke, locale='zh-CN',
            viewport={'width': 1300, 'height': 900})
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(ENTRY, wait_until='domcontentloaded', timeout=45000)
            if smoke:
                page.locator('a.aEntryItem').first.wait_for(state='attached', timeout=25000)
                print('只读入口检查：' + page.locator('.divEntryList').inner_text().strip(), flush=True)
                return 0
            if target and target > datetime.now(timezone.utc):
                print('等待开放时间：' + target.isoformat() + '；到点刷新一次。等待期间请保持本页，勿手填。', flush=True)
                wait_until(page, target)
                if not event_page(page.url, 'entry.html'):
                    print('页面已变化，取消自动刷新；请自行继续。', flush=True)
                    return keep_open(context, page)
                page.reload(wait_until='domcontentloaded', timeout=45000)
            elif target:
                print('指定时间已到/已过，使用当前这次加载，不追加刷新。', flush=True)
            state = enter_available_group(page)
            if state != 'entered':
                messages = {'unavailable': '当前入口未开放或名额已满。没有自动刷新；你可手动继续查看。',
                            'not_ready': '入口未就绪。请检查页面，稍后重新启动。',
                            'manual': '页面结构或入口已变化，请在官方页面手动继续。'}
                print(messages[state], flush=True)
                return keep_open(context, page)
            print('如出现声明/验证码/登录/排队，请在页面自行完成；助手只等待表单。', flush=True)
            if await_form(page):
                results = fill_fields(page, values)
                filled = sum(r['status'] in ('filled', 'already_set') for r in results)
                print(f'已填/已有相同值：{filled}/{len(values)} 项。', flush=True)
                for result in results:
                    if result['status'] not in ('filled', 'already_set'):
                        print('需手动：' + result['label'], flush=True)
                print('请在页面核对资料。还需手动：' + '；'.join(MANUAL), flush=True)
                if assisted:
                    state = assisted_submit(page)
                    if state == 'clicked':
                        print('已停止自动操作；如进入支付/订单页，请由你手动处理。', flush=True)
            else:
                print('未在30分钟内到达可识别表单，或离开指定页面。请手动继续。', flush=True)
            return keep_open(context, page)
        except BrowserError:
            if not page.is_closed():
                print('页面操作未完成。请在已打开的官方页面手动继续，关闭窗口后退出助手。', flush=True)
                if not smoke:
                    return keep_open(context, page)
            return 2
        finally:
            context.close()


def keep_open(context, page) -> int:
    print('助手已停止自动操作。关闭这个 Edge 窗口即可退出。', flush=True)
    try:
        while context.pages:
            context.pages[0].wait_for_timeout(500)
    except BrowserError:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=HERE / 'profile.json')
    parser.add_argument('--check-profile', action='store_true')
    parser.add_argument('--smoke', action='store_true', help='empty-template read-only live entry check, then close')
    parser.add_argument('--assisted-submit', action='store_true',
                        help='after you manually reach preview, require exact SUBMIT confirmation before one official click')
    args = parser.parse_args()
    try:
        return run(args.profile, args.check_profile, args.smoke, args.assisted_submit)
    except ProfileError as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('已退出。')
        return 130
    except Exception:
        print('助手未能启动，请检查本机 Edge、任务局部 Python 环境和资料文件。', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
