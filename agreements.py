"""Normal UI consent for the explicitly authorized Westbund automatic mode.

The pins identify the complete frozen public terms.  This module does not fetch
terms, set site state or submit an application.  Callers must stop automatic
actions on changed terms or an unknown click outcome.
"""
from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import json
from datetime import datetime
import re
import unicodedata
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit
from weakref import WeakKeyDictionary

from playwright.sync_api import Error as BrowserError


ORIGIN = 'https://www.xuhui-marathon.com'
PREFIX = '/b/FrAJj2/'
GROUP = '10公里跑'
ENTRY_BODY_SHA256 = 'eedd5892743f6edb01ab875b9c2f2c038235abd41807abcc665be01f6298bd3b'
ENTRY_CONTRACT_SHA256 = '7ff933d4e6bff14dd41efc2e9f03710a9180131e6e0f05a0e6720c102f53c595'
FORM_INPUT_SHA256 = {
    'jhragree': '215d2040d0a317f6b64a32611025501a1cfb46fc287e5f2bc1a2587304ffa0a7',
    'follow': '1ce84f1d65499f384912df439508cb682cd1b526e70b3ac65924b9ace468e456',
    'checkagree': '71139c297275dbfda38735ebc2beccad1a7f957b2c9a6d89593fd4dbdf213b76',
    'checkagree2': '94596852197f7c1c1dac30bbff34582d1e34cca56e350dde0e17bd4130d32dec',
}
PROJECT_IDENTITY = {'projectId': 6075, 'aliasName': 'FrAJj2', 'url': PREFIX,
                    'projectName': {'cn': '2026上海西岸10K', 'en': ''}}
ENTRY_TITLE = '参赛声明'
CONSENT_LABEL = '我已阅读并同意以上内容'
BLOCK_TAGS = frozenset(('p', 'div', 'br', 'li', 'ul', 'ol', 'section', 'article',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'td', 'blockquote'))
FORM_CONTRACTS = {
    'jhragree': {
        'title': '18周岁以下需要监护人或法定代理人签署参赛声明',
        'type': 'radiolist', 'input_type': 'checkbox', 'value': '我同意',
        'label': '我同意',
        'body_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    },
    'follow': {
        'title': '了解装备发放信息', 'type': 'agreement',
        'input_type': 'checkbox', 'value': '同意', 'label': CONSENT_LABEL,
        'body_sha256': '75c68bb369bde8932d1f57c99e174ebeb5dc2731096e2942b45c5b4f39604577',
    },
    'checkagree': {
        'title': '成绩审核', 'type': 'agreement',
        'input_type': 'checkbox', 'value': '同意', 'label': CONSENT_LABEL,
        'body_sha256': '4c08cd4ee7cc414c11f7359dd6f6d2af1772a2cacebfddc2cec1b8f1c108ec94',
    },
    'checkagree2': {
        'title': '资格审核', 'type': 'agreement',
        'input_type': 'checkbox', 'value': '同意', 'label': CONSENT_LABEL,
        'body_sha256': 'eb8403fc84923e7dde2e62539d3408028b87b197dd91ccd09cc70cf5d3063f16',
    },
}
_ATTEMPTS = WeakKeyDictionary()


def normalize_text(value: str) -> str:
    """NFC, HTML entity decoding, then Unicode whitespace to one ASCII space."""
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', html.unescape(value))).strip()


class _TermsText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS:
            self.parts.append(' ')

    def handle_data(self, data):
        self.parts.append(data)


def normalize_html(value: str) -> str:
    """html-blocks-nfc-whitespace-v1, matching the frozen source manifest."""
    parser = _TermsText()
    parser.feed(value)
    parser.close()
    return normalize_text(''.join(parser.parts))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _canonical_sha(value) -> str:
    """Canonical JSON rule from the frozen known-agreements source manifest."""
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False))


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _reject_json_constant(value):
    raise ValueError('Non-JSON constant')


def _json_with_date_data(source: str) -> str:
    """Convert the site's one observed Date literal syntax to string data.

    This is a lexer, not JavaScript evaluation: complete JSON strings are copied
    unchanged.  Outside those strings only new Date('YYYY/MM/DD HH:mm:ss') (or
    the same literal in double quotes) is recognized.  A real calendar check
    rejects invalid dates.  Every other expression remains non-JSON and is
    rejected by the normal decoder below.  No Date or site object is called.
    """
    literal = re.compile(r'''new Date\((['"])([0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\1\)''')
    decoder = json.JSONDecoder()
    parts, position = [], 0
    while position < len(source):
        if source[position] == '"':
            _, end = decoder.raw_decode(source, position)
            parts.append(source[position:end])
            position = end
            continue
        match = literal.match(source, position)
        if match:
            value = match.group(2)
            datetime.strptime(value, '%Y/%m/%d %H:%M:%S')
            parts.append(json.dumps(value))
            position = match.end()
            continue
        parts.append(source[position])
        position += 1
    return ''.join(parts)


def _parse_init_script(source: str, method: str):
    """Parse standalone init data, including the site's observed date literals.

    The frozen entry page uses bmAgree.init; the frozen registration response
    uses bm.init.  JSON is unchanged; exact calendar-validated Date literals are
    read as strings only.  Wrappers, variable arguments, other expressions,
    comments, extra statements and duplicate keys cannot supply an acceptance pin.
    """
    prefix = re.match(r'\A\s*webPages\s*\.\s*saishi\s*\.\s*' + re.escape(method)
                      + r'\s*\.\s*init\s*\(\s*', source)
    if prefix is None:
        return None
    try:
        data = _json_with_date_data(source[prefix.end():])
        decoder = json.JSONDecoder(object_pairs_hook=_unique_json_object,
                                   parse_constant=_reject_json_constant)
        config, end = decoder.raw_decode(data)
        if not isinstance(config, dict) or not re.fullmatch(r'\s*\)\s*;?\s*', data[end:]):
            return None
        # Also reject values that cannot have the declared UTF-8 canonical form.
        _canonical_sha(config)
        return config
    except (ValueError, TypeError, UnicodeError):
        return None


def _initial_config(page, method: str):
    """Read the page's initial inline literal, not private runtime closures.

    No fetching, eval, or invocation of webPages/ui/internal APIs occurs here.
    Initial configuration plus current DOM are the observable coverage; later
    mutations inside a site's JS closure cannot be inferred from this source.
    """
    scripts = page.locator('script:not([src])').evaluate_all('''els => els.map(el=>({
      type:el.type||'',text:el.textContent||''}))''')
    marker = re.compile(r'\bwebPages\s*\.\s*saishi\s*\.\s*(?:bmAgree|bm)\s*\.\s*init\b')
    candidates = [item for item in scripts if marker.search(item['text'])]
    if len(candidates) != 1 or candidates[0]['type'].lower() not in ('', 'text/javascript', 'application/javascript'):
        return None
    config = _parse_init_script(candidates[0]['text'], method)
    if config is None:
        return None
    identity = {key: config.get(key) for key in PROJECT_IDENTITY}
    if _canonical_sha(identity) != _canonical_sha(PROJECT_IDENTITY):
        return None
    return config


def _entry_config_sha(page):
    config = _initial_config(page, 'bmAgree')
    keys = ('projectId', 'aliasName', 'url', 'userAgreeType', 'userAgreeList')
    if config is None or config.get('mode') != 'agree' or not all(key in config for key in keys):
        return None
    fingerprint = _canonical_sha({key: config[key] for key in keys})
    return fingerprint if fingerprint == ENTRY_CONTRACT_SHA256 else None


def _form_config_shas(page):
    config = _initial_config(page, 'bm')
    if config is None or config.get('mode') != 'reg' or not isinstance(config.get('form'), dict):
        return None
    inputs = config['form'].get('inputs')
    if not isinstance(inputs, list) or not all(isinstance(item, dict) for item in inputs):
        return None
    names = [item.get('name') for item in inputs]
    if not all(isinstance(name, str) for name in names) or len(set(names)) != len(names):
        return None
    fingerprints = {}
    for item in inputs:
        name = item['name']
        if name in FORM_INPUT_SHA256:
            fingerprint = _canonical_sha(item)
            if fingerprint != FORM_INPUT_SHA256[name]:
                return None
            fingerprints[name] = fingerprint
        elif item.get('type') == 'agreement':
            return None
    return fingerprints if set(fingerprints) == set(FORM_INPUT_SHA256) else None


def _state(page):
    return _ATTEMPTS.setdefault(page, {'entry_checkbox': False, 'entry_navigation': False,
                                      'entry_blocked': False, 'form_clicks': set(),
                                      'form_blocked': False})


def _same_site(url: str, filename: str) -> bool:
    try:
        value = urlsplit(url)
        return (value.scheme == 'https' and value.hostname == 'www.xuhui-marathon.com'
                and value.port in (None, 443) and not value.username and not value.password
                and value.path == PREFIX + filename
                and value.fragment in (('', 'view') if filename == 'reg.html' else ('',)))
    except (TypeError, ValueError):
        return False


def _registration(url: str) -> bool:
    if not _same_site(url, 'reg.html'):
        return False
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return (query.get('subgroup') == [GROUP] and 'forcegoing' not in query)


def _return_url(url: str) -> str | None:
    if not _same_site(url, 'agree.html'):
        return None
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    targets = query.get('ReturnURL', [])
    if len(targets) != 1 or not targets[0] or '\\' in targets[0]:
        return None
    target = urljoin(url, targets[0])
    if not _registration(target) or urlsplit(target).fragment:
        return None
    value = urlsplit(target)
    # Stable identity without permitting an alternative host spelling or port.
    return urlunsplit(('https', 'www.xuhui-marathon.com', value.path, value.query, ''))


def _disabled(control) -> bool:
    return (not control.is_enabled()
            or 'disabled' in (control.get_attribute('class') or '').split()
            or control.get_attribute('aria-disabled') == 'true')


def _label_binds_to_input(label, control, *, native: bool) -> bool:
    """Verify the original native-for or renderer-sibling click destination."""
    handle = control.element_handle(timeout=1000)
    if handle is None:
        return False
    return label.evaluate('''(label,args) => {
      const [input,native]=args;
      const parent=label.parentElement;
      if(!parent||parent.querySelectorAll('input').length!==1||
         parent.querySelector('input')!==input)return false;
      if(native)return !!input.id && label.htmlFor===input.id && label.control===input &&
        [...document.querySelectorAll('[id]')].filter(el=>el.id===input.id).length===1;
      return parent.classList.contains('bx_ui_checkbox') && !label.htmlFor && !input.id;
    }''', [handle, native])


def _entry_snapshot(page):
    target = _return_url(page.url)
    if target is None:
        return None
    config_sha = _entry_config_sha(page)
    if config_sha is None:
        return None
    scope = page.locator('.divBm_agree .divUserAgree')
    if scope.count() != 1:
        return None
    content = scope.locator(':scope > .divCnt')
    confirmation = content.locator(':scope > .divCfm')
    control = confirmation.locator('input[type="checkbox"][name="agree"]')
    label = confirmation.locator('label.bx_ui_checkbox_text')
    button = scope.locator(':scope > .divBtns > a.aBtn')
    if (content.count() != 1 or scope.locator('.divCnt').count() != 1
            or confirmation.count() != 1 or scope.locator('.divCfm').count() != 1
            or page.locator('input[name="agree"]').count() != 1
            or control.count() != 1 or label.count() != 1 or button.count() != 1
            or scope.locator('input,select,textarea,button').count() != 1
            or scope.locator('a').count() != 1):
        return None
    if (normalize_html(label.inner_html()) != CONSENT_LABEL
            or normalize_text(control.input_value()) not in ('on', '')
            or control.get_attribute('type') != 'checkbox'
            or button.get_attribute('href') != 'javascript:void(0);'
            or not _label_binds_to_input(label, control, native=True)):
        return None
    # Read the original DOM without changing it, even temporarily.  The sole
    # direct .divCfm is UI, not part of the frozen full agreement body.
    body = content.evaluate('''el => [...el.childNodes].filter(node =>
      !(node.nodeType===1 && node.classList.contains('divCfm')))
      .map(node => node.nodeType===1 ? node.outerHTML : node.textContent).join('')''')
    if _sha(normalize_html(body)) != ENTRY_BODY_SHA256:
        return None
    headings = content.locator(':scope > p')
    if not headings.count() or normalize_html(headings.first.inner_html()) != ENTRY_TITLE:
        return None
    return {'return_url': target, 'body_sha256': ENTRY_BODY_SHA256,
            'initial_contract_sha256': config_sha,
            'label': CONSENT_LABEL, 'input_type': 'checkbox',
            'value': control.input_value(), 'checked': control.is_checked(),
            'enabled': not _disabled(control), 'button_text': normalize_text(button.inner_text()),
            'button_disabled': _disabled(button)}, control, label, button


def accept_entry_agreement(page) -> str:
    """Accept only the pinned entry contract, using its unmodified 5s UI gate.

    Returns not_applicable, waiting, accepted, terms_changed or
    navigation_unknown.  The last state latches: a possibly delivered click is
    never retried.  The caller may poll waiting by reading this method again.
    """
    state = _state(page)
    if state['entry_blocked']:
        return 'navigation_unknown'
    if not _same_site(page.url, 'agree.html'):
        return 'not_applicable'
    if state['entry_navigation']:
        return 'navigation_unknown'
    try:
        found = _entry_snapshot(page)
        if found is None:
            return 'terms_changed'
        snapshot, control, label, button = found
        if not snapshot['enabled'] or not label.is_visible() or not button.is_visible():
            return 'waiting'
        if re.fullmatch(r'请阅读\([0-5]\)', snapshot['button_text']):
            return 'waiting'
        if snapshot['button_text'] != '下一步':
            return 'terms_changed'
        if not snapshot['checked']:
            if state['entry_checkbox']:
                state['entry_blocked'] = True
                return 'navigation_unknown'
            state['entry_checkbox'] = True
            label.click(timeout=3000)
            renewed = _entry_snapshot(page)
            if renewed is None:
                state['entry_blocked'] = True
                return 'terms_changed'
            after = renewed[0]
            if ({k: v for k, v in after.items() if k not in ('checked', 'button_disabled')}
                    != {k: v for k, v in snapshot.items() if k not in ('checked', 'button_disabled')}
                    or not after['checked']):
                state['entry_blocked'] = True
                return 'navigation_unknown'
            snapshot, control, label, button = renewed
        if snapshot['button_disabled']:
            return 'waiting'
        # Re-read all terms and the target immediately before the sole normal
        # navigation click; no cookie, script event or private API shortcuts.
        renewed = _entry_snapshot(page)
        if renewed is None or renewed[0] != snapshot:
            state['entry_blocked'] = True
            return 'terms_changed'
        state['entry_navigation'] = True
        button.click(timeout=5000)
        page.wait_for_url(lambda url: _registration(str(url)), wait_until='domcontentloaded', timeout=5000)
        if _registration(page.url) and _return_target_matches(page.url, snapshot['return_url']):
            return 'accepted'
    except BrowserError:
        pass
    state['entry_blocked'] = True
    return 'navigation_unknown'


def _return_target_matches(actual: str, expected: str) -> bool:
    return (_registration(actual)
            and parse_qs(urlsplit(actual).query, keep_blank_values=True)
            == parse_qs(urlsplit(expected).query, keep_blank_values=True))


def _result(status: str, snapshot=None, reason='', fields=None):
    return {'status': status, 'snapshot': snapshot, 'reason': reason, 'fields': fields or []}


def _row_text(row) -> str:
    # The icon and validation message are renderer chrome.  Everything else is
    # checked, including text unexpectedly added outside the body container.
    return normalize_text(row.evaluate('''el => {
      const blocks=new Set(['P','DIV','BR','LI','UL','OL','SECTION','ARTICLE',
        'H1','H2','H3','H4','H5','H6','TR','TD','BLOCKQUOTE']);
      const text=node=>{
        if(node.nodeType===3)return node.textContent;
        if(node.nodeType!==1||node.matches('.icon,.bx_ui_form_cell_val'))return '';
        const value=[...node.childNodes].map(text).join('');
        return blocks.has(node.tagName)?' '+value+' ':value;
      };return text(el);
    }'''))


def _inspect_form(page, *, visible=False):
    if not _registration(page.url):
        return _result('not_applicable', reason='wrong_page')
    config_shas = _form_config_shas(page)
    if config_shas is None:
        return _result('terms_changed', reason='initial_config_missing_or_changed')
    source = page.locator('.divBmForm')
    if source.count() != 1:
        return _result('terms_changed', reason='source_form_count')
    if visible and not source.is_visible():
        return _result('waiting', reason='source_form_hidden')
    if source.locator('.bx_ui_form_tab_t_agreement').count() != 3 or source.locator('.bx_ui_agreement').count() != 3:
        return _result('terms_changed', reason='agreement_count')
    # Sex is the original ordinary choice control, not a consent.  Unknown
    # checkbox/radio names are left untouched and stop automatic acceptance.
    names = source.locator('input[type="checkbox"],input[type="radio"]').evaluate_all(
        'els => els.map(el=>el.name)')
    if any(name not in (*FORM_CONTRACTS, 'sex') for name in names):
        return _result('terms_changed', reason='unknown_choice_control')
    snapshots, fields = {}, []
    for name, expected in FORM_CONTRACTS.items():
        row = source.locator('.bx_ui_form_tab_n_' + name)
        if row.count() != 1:
            return _result('terms_changed', reason=name + ':row_count')
        if 'bx_ui_form_tab_t_' + expected['type'] not in (row.get_attribute('class') or '').split():
            return _result('terms_changed', reason=name + ':type')
        head = row.locator('.bx_ui_form_cell_head_text')
        control = row.locator('input[name="' + name + '"]')
        label = row.locator('label.bx_ui_radio_text' if name == 'jhragree' else 'label.bx_ui_checkbox_text')
        if (head.count() != 1 or label.count() != 1 or control.count() != 1
                or source.locator('input[name="' + name + '"]').count() != 1
                or row.locator('input,select,textarea').count() != 1
                or row.locator('a,button,script,iframe').count()):
            return _result('terms_changed', reason=name + ':structure')
        if not _label_binds_to_input(label, control, native=(name == 'jhragree')):
            return _result('terms_changed', reason=name + ':label_binding')
        body = row.locator('.bx_ui_agreement_cnt')
        if ((name == 'jhragree' and body.count() != 0)
                or (name != 'jhragree' and body.count() != 1)):
            return _result('terms_changed', reason=name + ':body_count')
        body_text = '' if name == 'jhragree' else normalize_html(body.inner_html())
        title, option = normalize_html(head.inner_html()), normalize_html(label.inner_html())
        input_type = (control.get_attribute('type') or '').lower()
        value = control.input_value()
        if (title != expected['title'] or option != expected['label']
                or _sha(body_text) != expected['body_sha256']
                or input_type != expected['input_type'] or value != expected['value']
                or _row_text(row) != normalize_text(' '.join((title, body_text, option)))):
            return _result('terms_changed', reason=name + ':text_or_value')
        # jhragree's original schema is radiolist, but its sole original native
        # input is a checkbox.  Keep those identities separate in the snapshot.
        item = dict(expected, initial_input_sha256=config_shas[name],
                    checked=control.is_checked(), enabled=not _disabled(control),
                    text_sha256=_sha(_row_text(row)))
        snapshots[name] = item
        fields.append({'name': name, 'status': 'already_accepted' if item['checked'] else 'pending'})
        if visible and (not row.is_visible() or not label.is_visible() or not item['enabled']):
            return _result('waiting', reason=name + ':hidden_or_disabled', fields=fields)
    return _result('inspected', snapshots, fields=fields)


def verify_form_agreements(page, expected_snapshot=None) -> dict:
    """Read all four pinned controls, also when preview hides their source form.

    A successful result is already_accepted and contains a stable snapshot of
    titles, full-body hashes, semantic/native types, values and checked state.
    Passing an earlier snapshot detects every change to those data.  No click
    or other browser mutation is performed by this function.
    """
    try:
        found = _inspect_form(page)
        if found['status'] != 'inspected':
            return found
        if expected_snapshot is not None and found['snapshot'] != expected_snapshot:
            return _result('terms_changed', reason='snapshot_changed', fields=found['fields'])
        if any(not item['checked'] or not item['enabled'] for item in found['snapshot'].values()):
            return _result('waiting', reason='not_all_checked', fields=found['fields'])
        found['status'] = 'already_accepted'
        return found
    except BrowserError:
        return _result('terms_changed', reason='read_failed')


def accept_form_agreements(page) -> dict:
    """Preflight every term, then click only the four visible original labels.

    Returns accepted/already_accepted on success; not_applicable, waiting,
    terms_changed or click_outcome_unknown otherwise.  Clicking never starts
    unless all four complete contracts and their structure have passed.
    """
    state = _state(page)
    if state['form_blocked']:
        return _result('click_outcome_unknown', reason='previous_click_unknown')
    try:
        found = _inspect_form(page, visible=True)
        if found['status'] != 'inspected':
            return found
        before = found['snapshot']
        clicked = False
        for name in FORM_CONTRACTS:
            # The preceding click may trigger arbitrary site UI changes: re-read
            # the full set before attempting another, with only known checked
            # transitions allowed.
            current = _inspect_form(page, visible=True)
            if current['status'] != 'inspected' or current['snapshot'] != before:
                state['form_blocked'] = clicked
                return _result('terms_changed', reason='changed_during_acceptance')
            if before[name]['checked']:
                continue
            if name in state['form_clicks']:
                state['form_blocked'] = True
                return _result('click_outcome_unknown', reason=name + ':already_attempted')
            row = page.locator('.divBmForm .bx_ui_form_tab_n_' + name)
            label = row.locator('label.bx_ui_radio_text' if name == 'jhragree' else 'label.bx_ui_checkbox_text')
            state['form_clicks'].add(name)
            clicked = True
            label.click(timeout=3000)
            expected_after = {field: dict(item) for field, item in before.items()}
            expected_after[name]['checked'] = True
            after = _inspect_form(page, visible=True)
            if after['status'] != 'inspected' or after['snapshot'] != expected_after:
                state['form_blocked'] = True
                return _result('click_outcome_unknown', reason=name + ':readback_unknown')
            before = after['snapshot']
        result = verify_form_agreements(page, before)
        if result['status'] != 'already_accepted':
            state['form_blocked'] = clicked
            return result
        result['status'] = 'accepted' if clicked else 'already_accepted'
        return result
    except BrowserError:
        state['form_blocked'] = True
        return _result('click_outcome_unknown', reason='browser_error')
