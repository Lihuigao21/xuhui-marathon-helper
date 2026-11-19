"""Offline consent controls; public fixtures contain only fictional terms.

The optional integration cases read separately frozen public site originals.
They never fetch missing files.  An exported repository without those originals
reports explicit skips; no personal profile or browser cache is read.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest import mock
from urllib.parse import quote, urlsplit

from playwright.sync_api import Error as BrowserError, sync_playwright


ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / 'results/helper/agreements.py'
if not MODULE.exists():
    MODULE = ROOT / 'agreements.py'
spec = importlib.util.spec_from_file_location('westbund_agreements', MODULE)
agreements = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agreements)

REG = agreements.ORIGIN + agreements.PREFIX + 'reg.html?subgroup=' + quote(agreements.GROUP)
AGREE = agreements.ORIGIN + agreements.PREFIX + 'agree.html?ReturnURL=' + quote(REG, safe='')
ENTRY_BODY = '<p>参赛声明</p><p>虚构测试正文甲，不用于真实报名。</p>'
FICTIONAL_BODIES = {name: ('' if name == 'jhragree' else '虚构测试条款' + str(index))
                    for index, name in enumerate(agreements.FORM_CONTRACTS)}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fictional_contracts():
    contracts = copy.deepcopy(agreements.FORM_CONTRACTS)
    for name, contract in contracts.items():
        contract['body_sha256'] = sha(FICTIONAL_BODIES[name].encode('utf-8'))
    return contracts


def fictional_entry_config():
    return dict(copy.deepcopy(agreements.PROJECT_IDENTITY), mode='agree', userAgreeType=4,
                userAgreeList=[{'button': {'text': {'cn': '虚构同意', 'en': ''}},
                                'content': {'cn': ENTRY_BODY, 'en': ''}, 'delay': '5',
                                'scrollRegion': 'page', 'sign': False,
                                'text': {'cn': '参赛声明', 'en': ''}}])


def entry_contract_pin(config):
    return agreements._canonical_sha({key: config[key] for key in
        ('projectId', 'aliasName', 'url', 'userAgreeType', 'userAgreeList')})


def fictional_form_config(contracts):
    inputs = []
    for name, contract in contracts.items():
        inputs.append({'name': name, 'type': contract['type'],
                       'text': {'cn': contract['title'], 'en': ''},
                       'desc': {'cn': '', 'en': ''},
                       'value': {'cn': FICTIONAL_BODIES[name], 'en': ''},
                       'list': ([{'text': {'cn': '我同意', 'en': ''}, 'value': '我同意'}]
                                if name == 'jhragree' else []),
                       'option': {'showBy': '', 'enableIcode': False, 'enableVcode': False},
                       'val': {'mode': 'rfv', 'exp': '', 'target': '', 'empty': 'disabled'}})
    return dict(copy.deepcopy(agreements.PROJECT_IDENTITY), mode='reg', form={'inputs': inputs})


def init_call(method, config):
    payload = json.dumps(config, ensure_ascii=False).replace('</', '<\\/')
    return '<script>webPages.saishi.' + method + '.init(' + payload + ')</script>'


class OfflineBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='msedge', headless=True)
        except Exception:
            cls.runtime.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context(offline=True, service_workers='block')
        self.page = self.context.new_page()
        self.blocked_requests, self.actions = [], []
        self.page.on('console', self.console)
        self.page.route('**/*', lambda route: (self.blocked_requests.append(route.request.url), route.abort()))

    def console(self, message):
        if message.text.startswith('TEST_ACTION:'):
            self.actions.append(json.loads(message.text[len('TEST_ACTION:'):]))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.blocked_requests, [])

    def serve(self, document: str, *, url=REG, assets=None):
        self.page.unroute('**/*')
        def route(request):
            target = urlsplit(request.request.url)
            if target.scheme != 'https' or target.netloc != 'www.xuhui-marathon.com':
                self.blocked_requests.append(request.request.url)
                return request.abort()
            if target.path in (agreements.PREFIX + 'agree.html', agreements.PREFIX + 'reg.html'):
                return request.fulfill(status=200, content_type='text/html; charset=utf-8',
                                       body=document.encode('utf-8'))
            name = Path(target.path).name
            if target.path.startswith('/fixture-assets/') and assets and name in assets:
                return request.fulfill(status=200, content_type='application/javascript; charset=utf-8',
                                       body=assets[name])
            self.blocked_requests.append(request.request.url)
            request.abort()
        self.page.route('**/*', route)
        self.page.goto(url, wait_until='domcontentloaded')

    def counts(self):
        counts = {}
        for action in self.actions:
            counts[action['name']] = counts.get(action['name'], 0) + 1
        return counts


RECORDER = r'''
document.addEventListener('click',event=>{
  const el=event.target;
  if(el.matches('input[name],a.aBtn'))console.log('TEST_ACTION:'+JSON.stringify({
    name:el.name||'next',trusted:event.isTrusted}));
});'''


class EntryAgreements(OfflineBrowser):
    def setUp(self):
        super().setUp()
        self.pin = mock.patch.object(agreements, 'ENTRY_BODY_SHA256',
                                     sha(agreements.normalize_html(ENTRY_BODY).encode('utf-8')))
        self.pin.start()
        self.config_pin = mock.patch.object(agreements, 'ENTRY_CONTRACT_SHA256',
                                             entry_contract_pin(fictional_entry_config()))
        self.config_pin.start()

    def tearDown(self):
        self.pin.stop()
        self.config_pin.stop()
        super().tearDown()

    def fixture(self, *, checked=False, seconds=0, navigate=True, url=AGREE, body=ENTRY_BODY,
                config=None, include_config=True):
        config = copy.deepcopy(config if config is not None else fictional_entry_config())
        ready = 'disabled' if seconds or not checked else ''
        caption = '请阅读(' + str(seconds) + ')' if seconds else '下一步'
        document = f'''<!doctype html><meta charset="utf-8"><style>
          label,a{{display:inline-block;padding:8px}}.divCfm{{margin:16px 0}}
          </style><div class="divBm_agree"><div class="divUserAgree"><div class="divCnt">{body}
          <div class="divCfm"><span class="bx_ui_checkbox"><label class="checkbox">
          <input type="checkbox" name="agree" id="entry-consent" {'checked' if checked else ''}>
          </label><label class="bx_ui_checkbox_text" for="entry-consent">{agreements.CONSENT_LABEL}</label>
          </span></div></div><div class="divBtns"><a class="aBtn {ready}" href="javascript:void(0);">{caption}</a>
          </div></div></div><script>{RECORDER}
          window.webPages={{saishi:{{bmAgree:{{init(config){{
          let remaining=Math.min({seconds},Number(config.userAgreeList[0].delay));
          const input=document.querySelector('input[name=agree]');
          const next=document.querySelector('.aBtn');
          function update(){{next.textContent=remaining?'请阅读('+remaining+')':'下一步';
            next.classList.toggle('disabled',!!remaining||!input.checked);}}
          input.addEventListener('change',update);
          if(remaining){{const timer=setInterval(()=>{{remaining--;update();if(!remaining)clearInterval(timer);}},1000);}}
          next.onclick=event=>{{event.preventDefault();if(!next.classList.contains('disabled')&&{str(navigate).lower()})
            location.href={json.dumps(REG)};}};
          }}}}}}}};</script>'''
        if include_config:
            document += init_call('bmAgree', config)
        self.serve(document, url=url)

    def test_original_five_second_gate_is_observed(self):
        self.fixture(seconds=5)
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'waiting')
        self.assertEqual(self.actions, [])
        self.page.wait_for_function("document.querySelector('.aBtn').textContent==='下一步'", timeout=6500)
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'accepted')
        self.assertEqual(self.counts(), {'agree': 1, 'next': 1})
        self.assertTrue(all(action['trusted'] for action in self.actions))

    def test_initial_checked_is_not_toggled(self):
        self.fixture(checked=True)
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'accepted')
        self.assertEqual(self.counts(), {'next': 1})
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'not_applicable')

    def test_full_body_change_is_rejected_before_click(self):
        self.fixture(body=ENTRY_BODY + '<p>新增虚构条款</p>')
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'terms_changed')
        self.assertEqual(self.actions, [])

    def test_duplicate_control_is_rejected_before_click(self):
        self.fixture()
        self.page.locator('.divCfm').evaluate("el=>el.insertAdjacentHTML('beforeend','<input type=checkbox name=agree>')")
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'terms_changed')
        self.assertEqual(self.actions, [])

    def test_label_redirected_to_another_input_is_zero_click_failure(self):
        self.fixture()
        self.page.locator('.bx_ui_checkbox_text').evaluate("el=>el.htmlFor='other-input'")
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'terms_changed')
        self.assertEqual(self.actions, [])

    def test_foreign_or_wrong_group_return_url_is_rejected_before_click(self):
        bad_targets = ('https://example.test/b/FrAJj2/reg.html?subgroup=' + quote(agreements.GROUP),
                       'http://www.xuhui-marathon.com/b/FrAJj2/reg.html?subgroup=' + quote(agreements.GROUP),
                       REG + '&forcegoing=true', REG + '&subgroup=other')
        for target in bad_targets:
            with self.subTest(target=target):
                url = agreements.ORIGIN + agreements.PREFIX + 'agree.html?ReturnURL=' + quote(target, safe='')
                self.fixture(url=url)
                self.assertEqual(agreements.accept_entry_agreement(self.page), 'terms_changed')
                self.assertEqual(self.actions, [])

    def test_navigation_unknown_is_not_retried(self):
        self.fixture(navigate=False)
        with mock.patch.object(self.page, 'wait_for_url', side_effect=BrowserError('fixture timeout')):
            self.assertEqual(agreements.accept_entry_agreement(self.page), 'navigation_unknown')
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'navigation_unknown')
        self.assertEqual(self.counts(), {'agree': 1, 'next': 1})

    def test_other_event_page_is_not_accepted(self):
        self.fixture(url=REG)
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'not_applicable')
        self.assertEqual(self.actions, [])

    def test_same_dom_changed_complete_entry_configuration_never_accepts(self):
        changes = (
            ('count', lambda c: c['userAgreeList'].append(copy.deepcopy(c['userAgreeList'][0]))),
            ('sign', lambda c: c['userAgreeList'][0].update(sign=True)),
            ('delay', lambda c: c['userAgreeList'][0].update(delay='0')),
            ('unrendered_option', lambda c: c['userAgreeList'][0]['button']['text'].update(en='new terms')),
        )
        baseline_dom = None
        for name, change in changes:
            with self.subTest(change=name):
                config = fictional_entry_config()
                change(config)
                self.fixture(config=config)
                current = self.page.locator('.divUserAgree').inner_text()
                if baseline_dom is None:
                    baseline_dom = current
                self.assertEqual(current, baseline_dom)
                self.assertEqual(agreements.accept_entry_agreement(self.page), 'terms_changed')
                self.assertEqual(self.actions, [])

    def test_missing_initial_entry_configuration_never_accepts(self):
        self.fixture(include_config=False)
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'terms_changed')
        self.assertEqual(self.actions, [])


class FormAgreements(OfflineBrowser):
    def setUp(self):
        super().setUp()
        self.contracts = fictional_contracts()
        self.pin = mock.patch.object(agreements, 'FORM_CONTRACTS', self.contracts)
        self.pin.start()
        self.config = fictional_form_config(self.contracts)
        self.input_pins = {item['name']: agreements._canonical_sha(item) for item in self.config['form']['inputs']}
        self.config_pin = mock.patch.object(agreements, 'FORM_INPUT_SHA256', self.input_pins)
        self.config_pin.start()

    def tearDown(self):
        self.pin.stop()
        self.config_pin.stop()
        super().tearDown()

    def fixture(self, *, checked=(), changed='', duplicate='', unknown=False,
                config=None, include_config=True):
        config = copy.deepcopy(config if config is not None else self.config)
        rows = []
        for name, contract in self.contracts.items():
            title = html.escape(contract['title'])
            label = html.escape(contract['label'])
            value = html.escape(contract['value'], quote=True)
            kind = contract['type']
            input_id = f' id="fixture-{name}"' if name == 'jhragree' else ''
            label_for = f' for="fixture-{name}"' if name == 'jhragree' else ''
            control = f'<input type="checkbox" name="{name}" value="{value}"{input_id}'+(
                ' checked' if name in checked else '')+'>'
            if duplicate == name:
                control += f'<input type="checkbox" name="{name}" value="{value}">'
            option = f'<span class="bx_ui_{"radio" if name == "jhragree" else "checkbox"}"><label>{control}</label>'
            option += f'<label class="bx_ui_{"radio" if name == "jhragree" else "checkbox"}_text"{label_for}>{label}</label></span>'
            body = html.escape(FICTIONAL_BODIES[name] + ('已改变' if name == changed else ''))
            if name != 'jhragree':
                option = f'<div class="bx_ui_agreement"><div class="bx_ui_agreement_cnt">{body}</div><div>{option}</div></div>'
            rows.append(f'<div class="bx_ui_form_tab bx_ui_form_tab_n_{name} bx_ui_form_tab_t_{kind}">'
                        f'<div class="bx_ui_form_cell_head_text">{title}</div><div>{option}</div></div>')
        extra = '<input type="checkbox" name="new_agreement">新协议' if unknown else ''
        document = f'''<!doctype html><meta charset="utf-8"><style>
          label{{display:inline-block;padding:5px}}.hide{{display:none!important}}
          </style><div class="divBmForm">{''.join(rows)}{extra}</div>
          <input type="checkbox" name="unrelated_outside"><script>{RECORDER}
          window.webPages={{saishi:{{bm:{{init(config){{
          for(const field of config.form.inputs){{
            if(field.type!=='agreement')continue;
            const row=document.querySelector('.bx_ui_form_tab_n_'+field.name);
            if(row)row.querySelector('.bx_ui_checkbox_text').addEventListener('click',event=>
              event.currentTarget.parentElement.querySelector('input').click());
          }}
          }}}}}}}};</script>'''
        if include_config:
            document += init_call('bm', config)
        self.serve(document)

    def test_all_four_then_readonly_hidden_source_preview(self):
        self.fixture()
        result = agreements.accept_form_agreements(self.page)
        self.assertEqual(result['status'], 'accepted', result['reason'])
        self.assertEqual(self.counts(), dict.fromkeys(self.contracts, 1))
        self.assertFalse(self.page.locator('input[name=unrelated_outside]').is_checked())
        self.page.locator('.divBmForm').evaluate("el=>el.classList.add('hide')")
        self.page.locator('.divBmForm').evaluate("el=>el.insertAdjacentHTML('afterend','<div class=divBmFormView><div name=follow>预览同名元素</div></div>')")
        checked = agreements.verify_form_agreements(self.page, result['snapshot'])
        self.assertEqual(checked['status'], 'already_accepted', checked['reason'])
        self.assertEqual(checked['snapshot'], result['snapshot'])
        self.assertEqual(self.counts(), dict.fromkeys(self.contracts, 1))
        self.page.evaluate("location.hash='view'")
        self.assertEqual(agreements.verify_form_agreements(self.page, result['snapshot'])['status'], 'already_accepted')
        self.page.evaluate("location.hash='unknown'")
        self.assertEqual(agreements.verify_form_agreements(self.page, result['snapshot'])['status'], 'not_applicable')

    def test_already_selected_controls_are_not_toggled(self):
        self.fixture(checked=self.contracts)
        result = agreements.accept_form_agreements(self.page)
        self.assertEqual(result['status'], 'already_accepted', result['reason'])
        self.assertEqual(self.actions, [])
        self.assertEqual(agreements.accept_form_agreements(self.page)['snapshot'], result['snapshot'])
        self.assertEqual(self.actions, [])

    def test_fourth_body_change_prevents_every_click(self):
        self.fixture(changed='checkagree2')
        result = agreements.accept_form_agreements(self.page)
        self.assertEqual(result['status'], 'terms_changed')
        self.assertEqual(self.actions, [])

    def test_duplicate_control_prevents_every_click(self):
        self.fixture(duplicate='checkagree2')
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'terms_changed')
        self.assertEqual(self.actions, [])

    def test_unknown_control_is_not_touched(self):
        self.fixture(unknown=True)
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'terms_changed')
        self.assertEqual(self.actions, [])
        self.assertFalse(self.page.locator('input[name=new_agreement]').is_checked())

    def test_redirected_label_binding_prevents_every_click(self):
        for selector in ('.bx_ui_form_tab_n_jhragree .bx_ui_radio_text',
                         '.bx_ui_form_tab_n_follow .bx_ui_checkbox_text'):
            with self.subTest(selector=selector):
                self.fixture()
                self.page.locator(selector).evaluate("el=>el.htmlFor='unrelated-input'")
                self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'terms_changed')
                self.assertEqual(self.actions, [])

    def test_changed_title_type_value_and_option_are_zero_click_failures(self):
        changes = (
            ('.bx_ui_form_tab_n_follow .bx_ui_form_cell_head_text', "el=>el.textContent+='新增'"),
            ('.bx_ui_form_tab_n_follow', "el=>el.classList.remove('bx_ui_form_tab_t_agreement')"),
            ('input[name=follow]', "el=>el.value='新增同意选项'"),
            ('.bx_ui_form_tab_n_jhragree .bx_ui_radio_text', "el=>el.textContent='我拒绝'"),
        )
        for selector, change in changes:
            with self.subTest(selector=selector):
                self.fixture()
                self.page.locator(selector).evaluate(change)
                self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'terms_changed')
                self.assertEqual(self.actions, [])

    def test_readonly_verify_detects_uncheck_and_text_change(self):
        self.fixture(checked=self.contracts)
        snapshot = agreements.verify_form_agreements(self.page)['snapshot']
        self.page.locator('input[name=follow]').evaluate('el=>el.checked=false')
        self.assertEqual(agreements.verify_form_agreements(self.page, snapshot)['status'], 'terms_changed')
        self.assertEqual(self.actions, [])
        self.page.locator('input[name=follow]').evaluate('el=>el.checked=true')
        self.page.locator('.bx_ui_form_tab_n_checkagree2 .bx_ui_agreement_cnt').evaluate("el=>el.textContent+='新增'")
        self.assertEqual(agreements.verify_form_agreements(self.page, snapshot)['status'], 'terms_changed')
        self.assertEqual(self.actions, [])

    def test_changed_terms_during_first_click_stop_the_rest(self):
        self.fixture()
        self.page.locator('input[name=jhragree]').evaluate('''el=>el.addEventListener('click',()=>{
          document.querySelector('.bx_ui_form_tab_n_checkagree2 .bx_ui_agreement_cnt').textContent+='新增';})''')
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'click_outcome_unknown')
        self.assertEqual(self.counts(), {'jhragree': 1})
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'click_outcome_unknown')
        self.assertEqual(self.counts(), {'jhragree': 1})

    def test_checkbox_click_without_checked_readback_is_not_retried(self):
        self.fixture()
        self.page.locator('input[name=jhragree]').evaluate("el=>el.addEventListener('click',event=>event.preventDefault())")
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'click_outcome_unknown')
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'click_outcome_unknown')
        self.assertEqual(self.counts(), {'jhragree': 1})

    def test_same_dom_changed_complete_input_configuration_never_accepts(self):
        changes = (
            ('val.mode', lambda item: item['val'].update(mode='other')),
            ('val.exp', lambda item: item['val'].update(exp='changed_expression')),
            ('val.target', lambda item: item['val'].update(target='other_field')),
            ('unrendered_list', lambda item: item['list'].append(
                {'text': {'cn': '未渲染的新选项', 'en': ''}, 'value': '额外同意'})),
            ('option', lambda item: item['option'].update(enableIcode=True)),
        )
        baseline_dom = None
        for name, change in changes:
            with self.subTest(change=name):
                config = copy.deepcopy(self.config)
                change(config['form']['inputs'][-1])
                self.fixture(config=config)
                current = self.page.locator('.divBmForm').inner_text()
                if baseline_dom is None:
                    baseline_dom = current
                self.assertEqual(current, baseline_dom)
                result = agreements.accept_form_agreements(self.page)
                self.assertEqual(result['status'], 'terms_changed')
                self.assertEqual(result['reason'], 'initial_config_missing_or_changed')
                self.assertEqual(self.actions, [])

    def test_missing_or_ambiguous_initial_form_configuration_never_accepts(self):
        self.fixture(include_config=False)
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'terms_changed')
        self.assertEqual(self.actions, [])
        self.fixture()
        self.page.evaluate('''() => {
          const source=[...document.scripts].find(el=>el.textContent.startsWith('webPages.saishi.bm.init('));
          const extra=document.createElement('script');extra.textContent=source.textContent;
          extra.type='application/json';document.body.append(extra);
        }''')
        self.assertEqual(agreements.accept_form_agreements(self.page)['status'], 'terms_changed')
        self.assertEqual(self.actions, [])


RENDERER = Path(os.environ.get('WESTBUND_RENDERER_ASSETS', ROOT / 'work/runtime_probe/helper-check/inputs'))
RESEARCH = Path(os.environ.get('WESTBUND_AGREEMENT_ASSETS', ROOT / 'records/agreements-autofill/inputs/entry-agreement-research'))
REG_SOURCE = Path(os.environ.get('WESTBUND_REG_INIT_SOURCE', ROOT / 'records/linked-reg-readonly/response.html'))
PINS = {
    'jquery.js': 'c87930fc2280d7b03ea212ee73fdc43b9389cf2b4a475ae7b228414bdc288e9d',
    'bxjslib.js': '4613d0fa2266543cbe66ea6f20b6d2e0832adce350c25abd6312ec16e0183a7b',
    '04-area.js': 'cf60418ea03be71b1ac6a3dc94ac89a8929990ad8bceee1e1d0bfd516535d790',
    '05-js.js': '5a00be71c749a65cdcc7639dc695c936cce80db48ea152101198a35f13013add',
    'form-schema.json': '00950a7e872e8c64078e03b3fdf5481ceb63a25c6fdce8c17eb3d2dffb413935',
    'entry-dom.html': 'd67ce881cbe7797741d653424cd00851b4af44fb80f710932370478f825d5c6a',
    'entry-agreement-renderer-config.json': 'e5a41427da5b3e18a947d4a370c2448cc6741b14f967b59c3b2b66d813cdbf22',
    'reg-init-source.html': 'fd1015156c02f79cafd3276df915637449dd8ab4b2919092706776e2e488124b',
}


def real_assets():
    paths = {name: (REG_SOURCE if name == 'reg-init-source.html' else
                   (RESEARCH if name in ('entry-dom.html', 'entry-agreement-renderer-config.json') else RENDERER) / name)
             for name in PINS}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise unittest.SkipTest('Frozen original agreement assets absent: ' + ', '.join(missing))
    assets = {}
    for name, path in paths.items():
        raw = path.read_bytes()
        if sha(raw) != PINS[name]:
            raise AssertionError('Frozen original fingerprint changed: ' + name)
        assets[name] = gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw
    return assets


class FrozenOriginalIntegration(OfflineBrowser):
    @classmethod
    def setUpClass(cls):
        cls.assets = real_assets()
        super().setUpClass()

    def test_actual_original_checkbox_renderer_and_four_complete_bodies(self):
        original_scripts = re.findall(r'<script\b[^>]*>(.*?)</script\s*>',
                                      self.assets['reg-init-source.html'].decode('utf-8-sig'), re.DOTALL)
        configs = [agreements._parse_init_script(source, 'bm') for source in original_scripts]
        configs = [config for config in configs if config is not None]
        self.assertEqual(len(configs), 1)
        config = configs[0]
        schema = json.loads(self.assets['form-schema.json'])
        for item in config['form']['inputs']:
            if item.get('name') in agreements.FORM_CONTRACTS:
                self.assertEqual(item, next(field for field in schema['form']['inputs'] if field.get('name') == item['name']))
        document = f'''<!doctype html><meta charset="utf-8"><style>
          body{{font:16px sans-serif}}label{{display:inline-block;padding:5px}}
          .bx_ui_form_tab{{margin:12px}}.hide{{display:none!important}}
          </style><script src="/fixture-assets/jquery.js"></script>
          <script src="/fixture-assets/bxjslib.js"></script>
          <script src="/fixture-assets/04-area.js"></script>
          <script src="/fixture-assets/05-js.js"></script>
          <div class="divBmForm" id="fixture"></div><script>{RECORDER}
          window.webPages={{saishi:{{bm:{{init(config){{
            const inputs=config.form.inputs.filter(item=>
              ['jhragree','follow','checkagree','checkagree2'].includes(item.name));
            ui.form.create({{container:'#fixture',inputs:inputs,lang:'cn',headType:'block'}});
          }}}}}}}};</script>''' + init_call('bm', config)
        errors = []
        self.page.on('pageerror', lambda error: errors.append(str(error)))
        self.serve(document, assets=self.assets)
        self.assertEqual(errors, [])
        self.assertEqual(self.page.locator('input[name=jhragree]').get_attribute('type'), 'checkbox')
        result = agreements.accept_form_agreements(self.page)
        self.assertEqual(result['status'], 'accepted', result['reason'])
        self.assertEqual(self.counts(), dict.fromkeys(agreements.FORM_CONTRACTS, 1))
        self.assertEqual(result['snapshot']['jhragree']['type'], 'radiolist')
        self.page.locator('.divBmForm').evaluate("el=>el.classList.add('hide')")
        self.assertEqual(agreements.verify_form_agreements(self.page, result['snapshot'])['status'], 'already_accepted')

    def test_frozen_complete_entry_dom_passes_pin_and_respects_captured_countdown(self):
        dom = self.assets['entry-dom.html'].decode('utf-8')
        config = json.loads(self.assets['entry-agreement-renderer-config.json'])
        document = ('<!doctype html><meta charset="utf-8"><div class="divBm_agree">' + dom
                    + '</div><script>window.webPages={saishi:{bmAgree:{init(config){}}}};</script>'
                    + init_call('bmAgree', config))
        self.serve(document, url=AGREE)
        self.assertEqual(agreements.accept_entry_agreement(self.page), 'waiting')
        self.assertFalse(self.page.locator('input[name=agree]').is_checked())


class HelperEntryReadiness(OfflineBrowser):
    """The entry adapter must not run before asynchronous terms are rendered."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        helper_path = MODULE.parent / 'helper.py'
        sys.path.insert(0, str(helper_path.parent))
        helper_spec = importlib.util.spec_from_file_location('readiness_helper', helper_path)
        cls.helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(cls.helper)

    def test_delayed_terms_are_ready_before_adapter_call(self):
        self.serve('''<!doctype html><meta charset="utf-8">
          <div class="divBm_agree"><div class="divUserAgree"></div></div>
          <script>setTimeout(()=>document.querySelector('.divUserAgree').innerHTML=
            '<div class="divCnt">虚构延迟渲染正文</div>',180);</script>''', url=AGREE)
        def simulated_adapter(page):
            self.assertTrue(page.locator('.divUserAgree > .divCnt').is_visible())
            # A test-only stand-in for the separately tested real agreement UI.
            page.set_content('<div class="bx_ui_form_tab_n_name"><input name="name"></div>')
            page.evaluate('url=>history.replaceState(null,"",url)', REG)
            return 'accepted'
        with mock.patch.object(self.helper, 'accept_entry_agreement', side_effect=simulated_adapter) as adapter:
            self.assertTrue(self.helper.await_form(self.page, timeout_seconds=3, accept_agreements=True))
            adapter.assert_called_once()

    def test_unrendered_terms_timeout_without_calling_adapter(self):
        self.serve('<div class="divBm_agree"><div class="divUserAgree"></div></div>', url=AGREE)
        with mock.patch.object(self.helper, 'accept_entry_agreement', side_effect=AssertionError('Not rendered')) as adapter:
            self.assertFalse(self.helper.await_form(self.page, timeout_seconds=0.05, accept_agreements=True))
            adapter.assert_not_called()


class TextNormalization(unittest.TestCase):
    def test_block_entity_nfc_and_whitespace_normalization(self):
        self.assertEqual(agreements.normalize_html('<p>甲&nbsp;乙</p><div>e\u0301<br>丙</div>'), '甲 乙 é 丙')

    def test_init_json_parser_rejects_nonliteral_or_ambiguous_javascript(self):
        self.assertEqual(agreements._parse_init_script('webPages.saishi.bm.init({"x":1})', 'bm'), {'x': 1})
        for source in ('webPages.saishi.bm.init(config)',
                       'webPages.saishi.bm.init({"x":1,"x":2})',
                       'webPages.saishi.bm.init({"x":NaN})',
                       '// webPages.saishi.bm.init({"x":1})',
                       'webPages.saishi.bm.init({"x":1}); runOtherCode()',
                       'webPages.saishi.bm.init({"x":1}, other)',
                       'webPages.saishi.bmReg.init({"x":1})'):
            with self.subTest(source=source):
                self.assertIsNone(agreements._parse_init_script(source, 'bm'))

    def test_exact_valid_date_literals_are_read_as_string_data(self):
        for quote_mark in ("'", '"'):
            source = ('webPages.saishi.bm.init({"nowTime":new Date(' + quote_mark
                      + '2024/02/29 23:59:59' + quote_mark + '),"flag":true})')
            self.assertEqual(agreements._parse_init_script(source, 'bm'),
                             {'nowTime': '2024/02/29 23:59:59', 'flag': True})

    def test_json_strings_containing_date_or_code_text_are_unchanged(self):
        data = {'first': "new Date('2026/11/19 11:23:29')",
                'second': 'new Date("invalid date"); new Function("do not execute")',
                'escaped': '引号"与反斜杠\\及 new Date(\'2026/02/30 00:00:00\')'}
        original = json.dumps(data, ensure_ascii=False, indent=2)
        self.assertEqual(agreements._json_with_date_data(original), original)
        self.assertEqual(agreements._parse_init_script('webPages.saishi.bm.init(' + original + ')', 'bm'), data)

    def test_invalid_dates_and_other_constructor_expressions_are_rejected(self):
        expressions = (
            "new Date('2026/02/30 00:00:00')",
            "new Date('2025/02/29 00:00:00')",
            "new Date('2026/01/01 24:00:00')",
            "new Date('0000/01/01 00:00:00')",
            "new Date('2026/11/19 11:23:29',other())",
            "new Date(Date.now())",
            "Date('2026/11/19 11:23:29')",
            "new Function('malicious code')",
            "new Date('2026/11/19 11:23:29').getTime()",
            "new Date('2026/11/19 11:23:29')+1",
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertIsNone(agreements._parse_init_script(
                    'webPages.saishi.bm.init({"nowTime":' + expression + '})', 'bm'))
        self.assertIsNone(agreements._parse_init_script(
            'webPages.saishi.bm.init({"nowTime":new Date("2026/11/19 11:23:29"),"nowTime":1})', 'bm'))
        self.assertIsNone(agreements._parse_init_script(
            'webPages.saishi.bm.init({"nowTime":new Date("2026/11/19 11:23:29")});other()', 'bm'))


class CurrentArchitectureLiterals(unittest.TestCase):
    """Read the separately cached current public HTML; never execute or fetch it."""
    PINS = {
        'reg.html.decoded': '0a9d4f3c29a0695bb89815b84d74f5499225a9302eefa11e878c60f9df619876',
        'agree.html.decoded': 'b8a90c35aa6653ea445bd4ada680fc2323bfe235a8e9d80f3ca61b1c6e1f0871',
    }

    @classmethod
    def setUpClass(cls):
        cls.source_root = Path(os.environ.get('WESTBUND_ARCHITECTURE_ASSETS',
                                              ROOT / 'records/architecture-check-20261119/attempt-01'))
        missing = [name for name in cls.PINS if not (cls.source_root / name).is_file()]
        if missing:
            raise unittest.SkipTest('Current frozen public init assets absent: ' + ', '.join(missing))

    def test_current_real_initial_scripts_parse_with_unchanged_contract_pins(self):
        for filename, expected_sha in self.PINS.items():
            with self.subTest(filename=filename):
                raw = (self.source_root / filename).read_bytes()
                self.assertEqual(sha(raw), expected_sha)
                scripts = re.findall(r'<script\b[^>]*>(.*?)</script\s*>',
                                     raw.decode('utf-8-sig'), re.DOTALL)
                method = 'bmAgree' if filename.startswith('agree') else 'bm'
                matching = [source for source in scripts if re.match(
                    r'\s*webPages\.saishi\.' + method + r'\.init\(', source)]
                self.assertEqual(len(matching), 1)
                self.assertEqual(len(re.findall(r'new Date\(', matching[0])), 3)
                config = agreements._parse_init_script(matching[0], method)
                self.assertIsNotNone(config)
                for key in ('nowTime', 'preTime', 'startTime'):
                    self.assertRegex(config[key], r'^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$')
                self.assertEqual({key: config[key] for key in agreements.PROJECT_IDENTITY},
                                 agreements.PROJECT_IDENTITY)
                if method == 'bmAgree':
                    self.assertEqual(entry_contract_pin(config), agreements.ENTRY_CONTRACT_SHA256)
                for field in config['form']['inputs']:
                    if field['name'] in agreements.FORM_INPUT_SHA256:
                        self.assertEqual(agreements._canonical_sha(field), agreements.FORM_INPUT_SHA256[field['name']])


if __name__ == '__main__':
    unittest.main()
