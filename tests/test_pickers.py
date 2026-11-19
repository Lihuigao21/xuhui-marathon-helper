"""Offline picker tests; all dates and selections below are fictional fixtures.

Run with: python -m unittest discover -s tests -p test_pickers.py -v
Real-plugin tests need the separately frozen third-party assets, never downloaded
by this test. WESTBUND_RENDERER_ASSETS and WESTBUND_PICKER_ASSETS may point at
their local directories. A public export without them reports an explicit skip.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / 'results/helper/pickers.py'
if not MODULE.exists():
    MODULE = ROOT / 'pickers.py'  # Exported repository layout.
spec = importlib.util.spec_from_file_location('westbund_pickers', MODULE)
pickers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pickers)

RENDERER = Path(os.environ.get('WESTBUND_RENDERER_ASSETS',
                             ROOT / 'work/runtime_probe/helper-check/inputs'))
PLUGINS = Path(os.environ.get('WESTBUND_PICKER_ASSETS', ROOT / 'records/special-pickers'))
PINS = {
    'jquery.js': 'c87930fc2280d7b03ea212ee73fdc43b9389cf2b4a475ae7b228414bdc288e9d',
    'bxjslib.js': '4613d0fa2266543cbe66ea6f20b6d2e0832adce350c25abd6312ec16e0183a7b',
    '04-area.js': 'cf60418ea03be71b1ac6a3dc94ac89a8929990ad8bceee1e1d0bfd516535d790',
    '05-js.js': '5a00be71c749a65cdcc7639dc695c936cce80db48ea152101198a35f13013add',
    'form-schema.json': '00950a7e872e8c64078e03b3fdf5481ceb63a25c6fdce8c17eb3d2dffb413935',
    'mobiscroll.jquery.js': '2fd69266889f38bb55ce055047349b8a782c9ece3f46447d6167a41104a94081',
    'mobiscroll.jquery.css': '491fb3c43799b83d90066852bf8af1c5765fb1293bb1140fd3106bd4a7b9bc2f',
    'icons_mobiscroll.ttf': '6c1a0692a7570f8469cfc6440893ff6205900831026bf973248d949523abc51a',
}


def frozen_assets() -> dict[str, bytes]:
    paths = {name: (PLUGINS if name.startswith(('mobiscroll.', 'icons_mobiscroll.')) else RENDERER) / name
             for name in PINS}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise unittest.SkipTest('Frozen real-plugin assets absent: ' + ', '.join(missing))
    assets = {}
    for name, path in paths.items():
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != PINS[name]:
            raise AssertionError('Frozen asset fingerprint changed: ' + name)
        assets[name] = gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw
    return assets


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
        self.page = self.browser.new_page()
        self.requests = []
        self.page.route('**/*', lambda route: (self.requests.append(route.request.url), route.abort()))

    def tearDown(self):
        self.page.close()

    def row(self, name):
        return self.page.locator('.bx_ui_form_tab_n_' + name)


class ControlGates(OfflineBrowser):
    def test_plain_readonly_date_is_not_filled_or_forced(self):
        self.page.set_content('''<meta charset="utf-8"><div class="bx_ui_form_tab_n_birthday">
          <input name="birthday" readonly></div>''')
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '2000-02-29'),
                         'manual_control')
        self.assertEqual(self.row('birthday').locator('input').input_value(), '')
        self.assertEqual(self.requests, [])

    def test_date_validation_and_existing_correct_value(self):
        self.page.set_content('''<div class="bx_ui_form_tab_n_birthday">
          <input name="birthday" value="2000-02-29" readonly></div>''')
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '2000-02-29'),
                         'already_set')
        for bad in ('2001-02-29', '2000-02-30', '2000-2-29', '', '2000-01-01T00:00', None):
            with self.subTest(case=str(bad)):
                self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), bad),
                                 'option_missing')
        self.assertEqual(self.row('birthday').locator('input').input_value(), '2000-02-29')

    def test_disabled_hidden_and_ambiguous_date_controls(self):
        for attributes in ('disabled', 'style="display:none"'):
            self.page.set_content(f'''<div class="bx_ui_form_tab_n_birthday">
              <input name="birthday" class="mbsc-comp" {attributes}></div>''')
            self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '2000-01-01'),
                             'manual_control')
        self.page.set_content('''<div class="bx_ui_form_tab_n_birthday">
          <input name="birthday"><input name="birthday"></div>''')
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '2000-01-01'),
                         'manual_control')

    def test_standard_country_select_and_missing_disabled_duplicate_options(self):
        self.page.set_content('''<meta charset="utf-8"><div class="bx_ui_form_tab_n_country">
          <select name="country"><option value="">请选择</option>
          <option value="中国大陆">中国大陆</option><option value="英国" disabled>英国</option>
          <option value="重复">甲</option><option value="重复">乙</option></select></div>''')
        for value in ('英国', '不存在', '重复'):
            self.assertEqual(pickers.select_country(self.page, self.row('country'), value),
                             'option_missing')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '中国大陆'), 'filled')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '中国大陆'), 'already_set')
        self.assertEqual(self.requests, [])

    def test_hidden_country_requires_linked_visible_dummy_and_both_readbacks(self):
        self.page.set_content('''<meta charset="utf-8"><div class="bx_ui_form_tab_n_country">
          <input id="native_dummy" class="mbsc-control" value="错误显示" readonly>
          <select id="native" class="mbsc-sel-hdn" name="country" style="display:none">
          <option value="中国大陆" selected>中国大陆</option></select></div>''')
        # Correct hidden value alone must not be reported as already_set.
        self.page.locator('input').evaluate('el => el.id = "unlinked_dummy"')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '中国大陆'),
                         'manual_control')
        self.page.locator('input').evaluate('el => { el.id="native_dummy"; el.value="中国大陆"; }')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '中国大陆'),
                         'already_set')
        self.page.locator('select').evaluate('el => el.disabled=true')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '中国大陆'),
                         'manual_control')


class RealMobiscroll(OfflineBrowser):
    @classmethod
    def setUpClass(cls):
        cls.assets = frozen_assets()
        super().setUpClass()

    def fixture(self, *, birthday='', country='', bounds=None, broken='', hide_set=False,
                focus_race=False):
        schema = json.loads(self.assets['form-schema.json'])
        fields = [item for item in schema['form']['inputs']
                  if item.get('name') in ('birthday', 'country')]
        for item in fields:
            item['value'] = birthday if item['name'] == 'birthday' else country
            if item['name'] == 'birthday' and bounds:
                item['option'].update(min=bounds[0], max=bounds[1])
        config = json.dumps(fields, ensure_ascii=False).replace('</', '<\\/')
        extra_css = '.mbsc-fr-btn-s{display:none!important}' if hide_set else ''
        if focus_race:
            extra_css += ('.mbsc-anim-in .mbsc-fr-popup{animation-duration:1s!important;'
                          '-webkit-animation-duration:1s!important}')
        html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
          <link rel="stylesheet" href="/assets/mobiscroll.jquery.css">
          <style>body{{font:16px sans-serif;margin:24px}}input.txt{{min-width:240px;height:32px}}
          .bx_ui_form_tab{{margin-bottom:16px}}{extra_css}</style>
          <script src="/assets/jquery.js"></script><script src="/assets/bxjslib.js"></script>
          <script src="/assets/04-area.js"></script><script src="/assets/05-js.js"></script>
          <script src="/assets/mobiscroll.jquery.js"></script>
          <form onsubmit="window.submitCount++;return false"><div id="fixture"></div>
          <input type="checkbox" name="agree"><button type="submit">提交</button></form>
          <script>window.submitCount=0;window.actions=[];window.focusRaces=0;window.searchReadiness=[];
          document.addEventListener('input',event=>{{
            if(event.target.matches('.mbsc-sel-filter-input')){{
              const frame=event.target.closest('.mbsc-fr');
              window.searchReadiness.push(!frame.matches('.mbsc-anim-in,.mbsc-anim-trans'));
            }}
          }},true);
          for(const kind of ['click','keydown','change']) document.addEventListener(kind,event=>{{
            window.actions.push({{kind,trusted:event.isTrusted,key:event.key||'',
              name:event.target.name||'',role:event.target.getAttribute('role')||''}});
            // Fault injection reproduces the original plugin's dialog.focus()
            // between keydown and keyup, only while entry animation is active.
            if({str(focus_race).lower()} && kind==='keydown' && event.key==='End' &&
               event.target.matches('.mbsc-sel-filter-input')){{
              const frame=event.target.closest('.mbsc-fr');
              if(frame.matches('.mbsc-anim-in,.mbsc-anim-trans')){{
                frame.querySelector('[role="dialog"]').focus();window.focusRaces++;
              }}
            }}
          }},true);
          window.fixtureForm=ui.form.create({{container:'#fixture',inputs:{config},lang:'cn',headType:'block'}});
          </script></html>'''
        self.page.unroute('**/*')
        errors = []
        self.page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            url = urlsplit(request.request.url)
            if url.netloc != 'picker-fixture.test':
                self.requests.append(request.request.url)
                request.abort()
            elif url.path == '/fixture.html':
                request.fulfill(status=200, content_type='text/html; charset=utf-8', body=html.encode('utf-8'))
            elif url.path.startswith('/assets/') and Path(url.path).name in self.assets:
                name = Path(url.path).name
                kind = ('text/css' if name.endswith('.css') else
                        'font/ttf' if name.endswith('.ttf') else 'application/javascript')
                request.fulfill(status=200, content_type=kind + '; charset=utf-8', body=self.assets[name])
            else:
                self.requests.append(request.request.url)
                request.abort()
        self.page.route('**/*', route)
        self.page.goto('http://picker-fixture.test/fixture.html')
        self.assertEqual(errors, [])
        self.assertEqual(self.row('birthday').locator('.bx_ui_form_cell_head_text').inner_text(), '出生日期')
        self.assertEqual(self.row('country').locator('option[value="中国大陆"]').inner_text(), '中国大陆')
        self.assertEqual(self.page.evaluate('mobiscroll.version'), '3.2.0')
        if broken:
            # Deliberately broken consumer callback after the real plugin saves.
            self.page.evaluate('''name => $('[name="'+name+'"]').on('change', function() {
              const control=this; setTimeout(() => {control.value='';}, 0);
            })''', broken)

    def assert_untouched(self):
        self.assertEqual(self.page.evaluate('window.submitCount'), 0)
        self.assertFalse(self.page.locator('[name="agree"]').is_checked())
        self.assertEqual(self.requests, [])

    def test_leap_day_real_wheels_and_idempotence(self):
        self.fixture()
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '2000-02-29'), 'filled')
        self.assertEqual(self.row('birthday').locator('input').input_value(), '2000-02-29')
        self.assertEqual(self.page.locator('.mbsc-fr:visible').count(), 0)
        actions = self.page.evaluate('window.actions')
        self.assertTrue(any(item['trusted'] and item['key'] == 'ArrowUp' for item in actions))
        count = len(actions)
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '2000-02-29'), 'already_set')
        self.assertEqual(len(self.page.evaluate('window.actions')), count)
        self.assert_untouched()

    def test_date_bounds_and_virtualized_years_preserve_failed_value(self):
        self.fixture(birthday='2010-12-31', bounds=('1961-01-01', '2010-12-31'))
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '1961-01-01'), 'filled')
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '1960-12-31'), 'option_missing')
        self.assertEqual(self.row('birthday').locator('input').input_value(), '1961-01-01')
        self.assertEqual(self.page.locator('.mbsc-fr:visible').count(), 0)
        self.assert_untouched()

    def test_country_near_and_far_options_visible_search_set_and_idempotence(self):
        self.fixture()
        for country in ('中国大陆', '英国'):
            self.assertEqual(pickers.select_country(self.page, self.row('country'), country), 'filled')
            self.assertEqual(self.row('country').locator('select').input_value(), country)
            self.assertEqual(self.row('country').locator('input').input_value(), country)
        count = len(self.page.evaluate('window.actions'))
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '英国'), 'already_set')
        self.assertEqual(len(self.page.evaluate('window.actions')), count)
        actions = self.page.evaluate('window.actions')
        self.assertTrue(any(item['trusted'] and item['key'] == 'End' for item in actions))
        self.assertGreaterEqual(sum(item['trusted'] and item['role'] == 'option' for item in actions), 2)
        self.assert_untouched()

    def test_country_rejects_missing_and_disabled_native_option(self):
        self.fixture()
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '不存在的地区'), 'option_missing')
        self.row('country').locator('option[value="英国"]').evaluate('el => el.disabled=true')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '英国'), 'option_missing')
        self.assertEqual(self.row('country').locator('select').input_value(), '')
        self.assertEqual(self.page.locator('.mbsc-fr:visible').count(), 0)
        self.assert_untouched()

    def test_country_repairs_inconsistent_dummy_readback(self):
        self.fixture(country='中国大陆')
        self.row('country').locator('input').evaluate('el => el.value="不一致"')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '中国大陆'), 'filled')
        self.assertEqual(self.row('country').locator('input').input_value(), '中国大陆')
        self.assert_untouched()

    def test_bad_save_readback_is_not_reported_as_filled(self):
        self.fixture(broken='country')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '英国'), 'not_confirmed')
        self.assertEqual(self.row('country').locator('select').input_value(), '')
        self.assert_untouched()

    def test_missing_set_cancels_without_committing(self):
        self.fixture(hide_set=True)
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '英国'), 'manual_control')
        self.assertEqual(self.row('country').locator('select').input_value(), '')
        self.assertEqual(self.page.locator('.mbsc-fr:visible').count(), 0)
        self.assert_untouched()

    def test_existing_other_picker_is_left_for_user(self):
        self.fixture()
        self.row('birthday').locator('input').click()
        self.page.locator('.mbsc-fr.mbsc-dt:visible').wait_for(state='visible')
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '英国'), 'manual_control')
        self.assertEqual(self.page.locator('.mbsc-fr.mbsc-dt:visible').count(), 1)
        self.assertEqual(self.row('country').locator('select').input_value(), '')
        self.assert_untouched()

    def test_country_waits_for_entry_animation_before_search_focus(self):
        self.fixture(focus_race=True)
        self.assertEqual(pickers.select_country(self.page, self.row('country'), '中国大陆'), 'filled')
        self.assertEqual(self.page.evaluate('window.focusRaces'), 0)
        self.assertEqual(self.page.evaluate('window.searchReadiness'), [True])
        self.assertEqual(self.row('country').locator('select').input_value(), '中国大陆')
        self.assert_untouched()

    def test_date_opens_after_its_frame_animation_is_ready(self):
        self.fixture(birthday='2000-02-28', focus_race=True)
        self.page.evaluate('''() => {
          window.dateKeyReadiness=[];document.addEventListener('keydown',event=>{
            if(event.target.matches('.mbsc-dt .mbsc-sc-whl')){
              const frame=event.target.closest('.mbsc-fr');
              window.dateKeyReadiness.push(!frame.matches('.mbsc-anim-in,.mbsc-anim-trans'));
            }
          },true);
        }''')
        self.assertEqual(pickers.select_birthday(self.page, self.row('birthday'), '2000-02-29'), 'filled')
        self.assertTrue(self.page.evaluate('window.dateKeyReadiness.length > 0'))
        self.assertTrue(all(self.page.evaluate('window.dateKeyReadiness')))
        self.assert_untouched()


if __name__ == '__main__':
    unittest.main(verbosity=2)
