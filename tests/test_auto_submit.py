"""Fictional local forms: automatic preview and a single authorized submit click."""
import html
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from playwright.sync_api import ElementHandle, Error as BrowserError, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / 'helper.py' if (ROOT / 'helper.py').exists() else ROOT / 'results/helper/helper.py'
sys.path.insert(0, str(HELPER.parent))
spec = importlib.util.spec_from_file_location('automatic_helper', HELPER)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

VALUES = {'真实姓名': '虚构测试甲', '证件类型': '护照', '证件号码': 'TEST-ONLY-NOT-REAL', '性别': '男',
          '出生日期': '1990-01-02', '手机号码': '19900000000', '电子邮件': 'fixture@example.test',
          '所在国家或地区': '中国大陆', '所在城市': '上海市/上海市/徐汇区', '所在地址': '虚构测试地址一号',
          '血型': 'A', '衣服尺码': '男M', '紧急联系人姓名': '虚构联系人', '紧急联系人手机号码': '19900000001'}
REG = h.ENTRY.replace('entry.html', 'reg.html?subgroup=10%E5%85%AC%E9%87%8C%E8%B7%91')


class AutomaticSubmit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def fixture(self, *, acknowledged=True, reject=False, preview_error=False, duplicate=False, disabled=False):
        rows = []
        for label, (field, kind) in h.FIELDS.items():
            value = html.escape(VALUES[label], quote=True)
            if kind == 'city':
                saved = html.escape(json.dumps({'province': '上海市', 'city': '上海市', 'xian': '徐汇区'}, ensure_ascii=False), quote=True)
                control = f'<input name="city_text" readonly value="上海市 徐汇区"><input name="city" type="hidden" value="{saved}">'
            elif kind == 'choice':
                control = f'<input name="{field}" type="radio" checked value="{value}">'
            elif kind in ('select', 'country'):
                text = '男M (166-170)' if field == 'clothesize' else value
                control = f'<select name="{field}"><option value="{value}" selected>{text}</option></select>'
            else:
                control = f'<input name="{field}" value="{value}"'+(' readonly' if kind == 'date' else '')+'>'
            rows.append(f'<div class="bx_ui_form_row rfv bx_ui_form_tab_n_{field}">{control}</div>')
        body = '''<!doctype html><meta charset="utf-8"><style>.hide{display:none!important}</style>
          <div class="divBmMid"><div class="divBmForm">__ROWS__
          <div class="bx_ui_form_row rfv bx_ui_form_tab_n_follow"><input name="follow" type="checkbox" value="同意" __ACK__></div>
          </div><div class="divBmFormView hide"></div><div class="divBmFormBtns">
          <a href="#view" class="aBtnView">下一步</a><button type="button" class="aBtnSubmit hide" __DISABLED__>提交</button>
          </div></div><script>
          window.views=0;window.submits=0;window.ackClicks=0;
          document.querySelector('[name=follow]').onclick=()=>window.ackClicks++;
          document.querySelector('.aBtnView').onclick=e=>{
            e.preventDefault();window.views++;
            if(__REJECT__)return;
            const source=document.querySelector('.divBmForm'),preview=document.querySelector('.divBmFormView');
            for(const row of source.querySelectorAll('.bx_ui_form_row')){
              const inputs=row.querySelectorAll('input,select'),first=inputs[0],name=first.name.replace('_text','');
              let value=first.value;
              if(name==='city') { const v=JSON.parse(row.querySelector('[name=city]').value);value=[v.province,v.city,v.xian].join(' '); }
              else if(first.tagName==='SELECT')value=first.selectedOptions[0].textContent;
              if(__PREVIEW_ERROR__&&name==='name')value='与JSON不符';
              const copy=document.createElement('div');copy.className='bx_ui_form_tab_n_'+name;
              const text=document.createElement('div');text.className='bx_ui_remark';text.setAttribute('name',name);text.textContent=value;
              copy.append(text);preview.append(copy);
            }
            source.classList.add('hide');preview.classList.remove('hide');
            document.querySelector('.aBtnView').classList.add('hide');
            const submit=document.querySelector('.aBtnSubmit');submit.classList.remove('hide');
            if(__DUPLICATE__){const clone=submit.cloneNode(true);submit.after(clone);clone.onclick=()=>window.submits++;}
          };
          document.querySelector('.aBtnSubmit').onclick=()=>window.submits++;
          </script>'''.replace('__ROWS__', ''.join(rows)).replace('__ACK__', 'checked' if acknowledged else '').replace(
              '__DISABLED__', 'disabled' if disabled else '').replace('__REJECT__', json.dumps(reject)).replace(
              '__PREVIEW_ERROR__', json.dumps(preview_error)).replace('__DUPLICATE__', json.dumps(duplicate))
        self.page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body.encode('utf-8')))
        self.page.goto(REG)

    def counts(self):
        return self.page.evaluate('({views:window.views,submits:window.submits,ackClicks:window.ackClicks})')

    def test_automatic_preview_and_single_submit_without_console_prompt(self):
        self.fixture()
        with mock.patch('builtins.input', side_effect=AssertionError('No console confirmation in automatic mode')):
            self.assertEqual(h.automatic_submit(self.page, VALUES), 'clicked')
        self.assertEqual(self.counts(), {'views': 1, 'submits': 1, 'ackClicks': 0})

    def test_shanghai_both_levels_and_unique_suffix_alias(self):
        self.fixture()
        values = dict(VALUES, 所在城市='上海/上海/徐汇区')
        self.assertEqual(h.automatic_submit(self.page, values), 'clicked')
        self.assertEqual(self.counts()['submits'], 1)

    def test_missing_profile_never_opens_preview_or_submits(self):
        self.fixture()
        values = dict(VALUES)
        del values['所在城市']
        self.assertEqual(h.automatic_submit(self.page, values), 'profile_incomplete')
        self.assertEqual(self.counts()['views'], 0)
        self.assertEqual(self.counts()['submits'], 0)

    def test_json_mismatch_never_opens_preview_or_submits(self):
        self.fixture()
        self.page.locator('[name=name]').fill('已改变的值')
        self.assertEqual(h.automatic_submit(self.page, VALUES, timeout_seconds=0.01), 'waiting_timeout')
        self.assertEqual(self.counts()['views'], 0)
        self.assertEqual(self.counts()['submits'], 0)

    def test_pending_acknowledgement_waits_without_checking_it(self):
        self.fixture(acknowledged=False)
        self.assertEqual(h.automatic_submit(self.page, VALUES, timeout_seconds=0.01), 'waiting_timeout')
        self.assertEqual(self.counts(), {'views': 0, 'submits': 0, 'ackClicks': 0})
        self.assertFalse(self.page.locator('[name=follow]').is_checked())

    def test_real_tab_wrapper_required_marker_waits_before_preview(self):
        self.fixture(acknowledged=False)
        self.page.evaluate('''() => {const tab=document.querySelector('.bx_ui_form_tab_n_follow');
          tab.classList.remove('bx_ui_form_row');tab.classList.add('bx_ui_form_tab');
          const input=tab.querySelector('input');const row=document.createElement('div');
          row.className='bx_ui_form_row';tab.append(row);row.append(input);}''')
        self.assertEqual(h._required_pending(self.page), ['装备领取确认'])
        self.assertEqual(h.automatic_submit(self.page, VALUES, timeout_seconds=0.01), 'waiting_timeout')
        self.assertEqual(self.counts(), {'views': 0, 'submits': 0, 'ackClicks': 0})

    def test_continues_after_user_completes_acknowledgement(self):
        self.fixture(acknowledged=False)
        self.page.evaluate('setTimeout(()=>document.querySelector("[name=follow]").click(),120)')
        self.assertEqual(h.automatic_submit(self.page, VALUES, timeout_seconds=3), 'clicked')
        self.assertEqual(self.counts(), {'views': 1, 'submits': 1, 'ackClicks': 1})

    def test_preview_mismatch_never_submits(self):
        self.fixture(preview_error=True)
        self.assertEqual(h.automatic_submit(self.page, VALUES), 'preview_mismatch')
        self.assertEqual(self.counts()['submits'], 0)

    def test_hidden_source_cannot_exempt_missing_or_wrong_preview(self):
        for parent_hidden in (False, True):
            for missing in (False, True):
                with self.subTest(parent_hidden=parent_hidden, missing=missing):
                    self.fixture()
                    self.page.evaluate('''args => {
                      const row=document.querySelector('.divBmForm .bx_ui_form_tab_n_name');
                      if(args.parent){const parent=document.createElement('div');parent.hidden=true;
                        row.before(parent);parent.append(row);}else row.hidden=true;
                      document.querySelector('.aBtnView').addEventListener('click',()=>{
                        const value=document.querySelector('.divBmFormView .bx_ui_remark[name=name]');
                        if(args.missing)value.parentElement.remove();else value.textContent='错误预览';
                      });
                    }''', {'parent': parent_hidden, 'missing': missing})
                    self.assertEqual(h.automatic_submit(self.page, VALUES), 'preview_mismatch')
                    self.assertEqual(self.counts()['submits'], 0)
                    self.page.unroute('**/*')

    def test_every_preview_remark_and_parent_must_be_visible(self):
        for kind in ('remark', 'row', 'intermediate'):
            with self.subTest(kind=kind):
                self.fixture()
                self.page.evaluate('''kind=>document.querySelector('.aBtnView').addEventListener('click',()=>{
                  const value=document.querySelector('.divBmFormView .bx_ui_remark[name=name]');
                  if(kind==='remark')value.hidden=true;
                  else if(kind==='row')value.parentElement.hidden=true;
                  else{const box=document.createElement('div');box.style.display='none';value.before(box);box.append(value);}
                })''', kind)
                self.assertEqual(h.automatic_submit(self.page, VALUES), 'preview_mismatch')
                self.assertEqual(self.counts()['submits'], 0)
                self.page.unroute('**/*')

    def test_acknowledgement_reset_by_preview_never_submits(self):
        self.fixture()
        self.page.evaluate('''document.querySelector('.aBtnView').addEventListener('click',()=>{
          document.querySelector('[name=follow]').checked=false;
        })''')
        self.assertEqual(h.automatic_submit(self.page, VALUES), 'requirements_changed')
        self.assertEqual(self.counts()['submits'], 0)

    def test_already_visible_preview_with_missing_acknowledgement_never_submits(self):
        self.fixture(acknowledged=False)
        self.page.locator('.aBtnView').click()
        self.assertEqual(h.automatic_submit(self.page, VALUES), 'requirements_changed')
        self.assertEqual(self.counts()['submits'], 0)

    def test_acknowledgement_changes_during_final_read_never_submits(self):
        self.fixture()
        original = h._preview_snapshot
        reads = []
        def reset_on_second_read(page, values):
            result = original(page, values)
            reads.append(1)
            if len(reads) == 2:
                page.locator('input[name=follow]').evaluate('el=>el.checked=false')
            return result
        with mock.patch.object(h, '_preview_snapshot', side_effect=reset_on_second_read):
            self.assertEqual(h.automatic_submit(self.page, VALUES), 'requirements_changed')
        self.assertEqual(self.counts()['submits'], 0)

    def test_duplicate_or_disabled_submit_never_clicks(self):
        for flags in ({'duplicate': True}, {'disabled': True}):
            with self.subTest(flags=flags):
                self.fixture(**flags)
                self.assertEqual(h.automatic_submit(self.page, VALUES), 'preview_unavailable')
                self.assertEqual(self.counts()['submits'], 0)
                self.page.unroute('**/*')

    def test_wrong_group_or_forcegoing_never_clicks(self):
        for url in (REG.replace('10%E5%85%AC%E9%87%8C%E8%B7%91', 'OTHER'), REG + '&forcegoing=true'):
            with self.subTest(url=url):
                self.fixture()
                self.page.goto(url)
                self.assertEqual(h.automatic_submit(self.page, VALUES), 'left_event')
                self.assertEqual(self.counts()['submits'], 0)
                self.page.unroute('**/*')

    def test_replaced_target_between_checks_never_clicks(self):
        self.fixture()
        original = h._preview_snapshot
        calls = []
        def replace_after_second_read(page, values):
            result = original(page, values)
            calls.append(1)
            if len(calls) == 2:
                page.evaluate('''() => {const old=document.querySelector('.aBtnSubmit'),copy=old.cloneNode(true);
                  copy.onclick=()=>window.submits++;old.replaceWith(copy);}''')
            return result
        with mock.patch.object(h, '_preview_snapshot', side_effect=replace_after_second_read):
            self.assertEqual(h.automatic_submit(self.page, VALUES), 'target_changed')
        self.assertEqual(self.counts()['submits'], 0)

    def test_click_error_after_event_is_unknown_and_not_retried(self):
        self.fixture()
        original = ElementHandle.click
        def click_then_error(handle, *args, **kwargs):
            original(handle, *args, **kwargs)
            raise BrowserError('Synthetic client disconnect after event')
        with mock.patch.object(ElementHandle, 'click', new=click_then_error):
            self.assertEqual(h.automatic_submit(self.page, VALUES), 'click_outcome_unknown')
        self.assertEqual(self.counts()['submits'], 1)
        self.assertEqual(self.counts()['views'], 1)

    def test_site_rejects_preview_without_a_submit_click(self):
        self.fixture(reject=True)
        self.assertEqual(h.automatic_submit(self.page, VALUES), 'preview_rejected')
        self.assertEqual(self.counts()['views'], 1)
        self.assertEqual(self.counts()['submits'], 0)

    def test_auto_profile_check_and_smoke_conflict_never_launch_browser(self):
        folder = ROOT / '.test-temp' if HELPER.parent == ROOT else ROOT / 'work/temp'
        folder.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, suffix='.json', delete=False) as stream:
            json.dump({'报名资料': VALUES}, stream, ensure_ascii=False)
            path = Path(stream.name)
        with mock.patch.object(h, 'sync_playwright', side_effect=AssertionError('Must not launch a browser')):
            self.assertEqual(h.run(path, check_only=True, auto=True), 0)
            with self.assertRaises(h.ProfileError):
                h.run(path, smoke=True, auto=True)
            data = dict(VALUES)
            data['所在城市'] = ''
            path.write_text(json.dumps({'报名资料': data}, ensure_ascii=False), encoding='utf-8')
            with self.assertRaises(h.ProfileError):
                h.run(path, auto=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
