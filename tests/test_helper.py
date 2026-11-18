"""Task-local regression checks; all personal values below are fictional fixtures."""
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('westbund_helper', ROOT / 'helper.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class Contracts(unittest.TestCase):
    def test_clock_requires_timezone_and_respects_beijing(self):
        now = datetime(2026, 11, 18, 2, 59, 59, tzinfo=timezone.utc)
        target = h.parse_target('2026-11-18T11:00:00+08:00', now)
        self.assertEqual((target - now).total_seconds(), 1)
        with self.assertRaises(h.ProfileError):
            h.parse_target('2026-11-18T11:00:00', now)
        with self.assertRaises(h.ProfileError):
            h.parse_target('2026-12-18T11:00:00+08:00', now)

    def test_wait_is_local_and_ends_once(self):
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        clock, sleeps = [now], []
        def pause(ms):
            sleeps.append(ms)
            clock[0] += timedelta(milliseconds=ms)
        h.wait_until(None, now + timedelta(seconds=2.5), now=lambda: clock[0], pause=pause)
        self.assertEqual(sleeps, [1000, 1000, 500])

    def test_only_expected_registration_link(self):
        self.assertTrue(h.registration_link('reg.html?subgroup=10%E5%85%AC%E9%87%8C%E8%B7%91'))
        for bad in ['https://example.com/reg.html?subgroup=x', '/b/OTHER/reg.html?subgroup=x',
                    'javascript:alert(1)', 'https://www.xuhui-marathon.com:8443/b/FrAJj2/reg.html?subgroup=10公里跑',
                    'reg.html?subgroup=10公里跑&subgroup=其他', 'pay.html']:
            self.assertFalse(h.registration_link(bad))

    def test_profile_rejects_agreements_bool_and_unknown_data(self):
        base = {'开放时间': '', '报名资料': {}}
        folder = ROOT / '.test-temp'
        folder.mkdir(exist_ok=True)
        for values in [{'参赛声明': '我同意'}, {'真实姓名': True}, {'手机号码': '123'}, {'出生日期': '2000-02-30'}]:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, suffix='.json', delete=False) as f:
                json.dump(dict(base, 报名资料=values), f, ensure_ascii=False)
                name = Path(f.name)
            with self.assertRaises(h.ProfileError):
                h.read_profile(name)
        values, target = h.read_profile(ROOT / 'profile.example.json')
        self.assertEqual(values, {})
        self.assertIsNone(target)


class BrowserFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.requests = []
        self.page.on('request', lambda r: self.requests.append(r.url))

    def tearDown(self):
        self.page.close()

    def test_fill_readback_skip_and_no_submission(self):
        self.page.set_content('''<script>window.submits=0;window.changes=0;</script>
          <form onsubmit="window.submits++;return false">
          <div class="bx_ui_form_tab_n_name"><input name="name" onchange="window.changes++"></div>
          <div class="bx_ui_form_tab_n_idcardtype"><select name="idcardtype"><option value="">请选择</option><option value="护照">护照</option></select></div>
          <div class="bx_ui_form_tab_n_sex"><input type="checkbox" class="ipt_radio" name="sex" value="男"><input type="checkbox" class="ipt_radio" name="sex" value="女"></div>
          <div class="bx_ui_form_tab_n_mobile"><input name="mobile" disabled></div>
          <div class="bx_ui_form_tab_n_birthday"><input name="birthday" readonly></div>
          <div class="bx_ui_form_tab_n_clothesize"><select name="clothesize"><option>女M</option></select></div>
          <div class="bx_ui_form_tab_n_contactname" hidden><input name="contactname"></div>
          <div class="bx_ui_form_tab_n_jhragree"><input type="checkbox" name="jhragree" value="我同意"></div>
          <input type="checkbox" name="agree"><input type="checkbox" name="checkagree">
          <button type="submit">确认并提交</button></form>''')
        values = {'真实姓名': '虚构测试甲', '证件类型': '护照', '性别': '女', '手机号码': '19900000000',
                  '出生日期': '2000-01-01', '衣服尺码': '男M', '紧急联系人姓名': '虚构联系人'}
        result = {r['label']: r['status'] for r in h.fill_fields(self.page, values, offline_test=True)}
        self.assertEqual(result, {'真实姓名': 'filled', '证件类型': 'filled', '性别': 'filled',
                                  '手机号码': 'manual_control', '出生日期': 'manual_control',
                                  '衣服尺码': 'option_missing', '紧急联系人姓名': 'missing_or_hidden'})
        self.assertEqual(self.page.locator('[name=name]').input_value(), '虚构测试甲')
        self.assertEqual(self.page.evaluate('window.submits'), 0)
        self.assertGreaterEqual(self.page.evaluate('window.changes'), 1)
        self.assertEqual(self.page.locator('[name=agree]:checked,[name=jhragree]:checked,[name=checkagree]:checked').count(), 0)
        self.assertEqual(self.requests, [])
        self.assertTrue(all('value' not in item for item in h.fill_fields(self.page, {'真实姓名': '虚构测试乙'}, offline_test=True)))

    def test_wrong_page_and_unknown_label_do_not_fill(self):
        self.page.set_content('<div class="bx_ui_form_tab_n_name"><input name="name"></div><input type="checkbox" name="agree">')
        self.assertEqual(h.fill_fields(self.page, {'真实姓名': '虚构测试'}), [{'label': '报名表', 'status': 'wrong_page'}])
        self.assertEqual(self.page.locator('[name=name]').input_value(), '')
        result = h.fill_fields(self.page, {'参赛声明': '我同意'}, offline_test=True)
        self.assertEqual(result, [{'label': '未知字段', 'status': 'not_allowed'}])
        self.assertFalse(self.page.locator('[name=agree]').is_checked())

    def test_assisted_submit_requires_exact_confirmation(self):
        reg_url = 'https://www.xuhui-marathon.com/b/FrAJj2/reg.html?subgroup=10%E5%85%AC%E9%87%8C%E8%B7%91'
        body = '''<form onsubmit="window.submits=(window.submits||0)+1;return false">
          <div class="divBmFormView">预览</div>
          <a class="aBtnSubmit aBtn">提交</a></form>
          <script>document.querySelector('a').onclick=()=>{document.querySelector('form').requestSubmit()}</script>'''
        self.page.route('**/*', lambda r: r.fulfill(status=200, content_type='text/html; charset=utf-8', body=body.encode()))
        self.page.goto(reg_url)
        self.assertEqual(h.confirm_and_click_submit(self.page, lambda _: 'NO'), 'cancelled')
        self.assertEqual(self.page.evaluate('window.submits||0'), 0)
        self.assertEqual(h.confirm_and_click_submit(self.page, lambda _: 'SUBMIT'), 'clicked')
        self.assertEqual(self.page.evaluate('window.submits||0'), 1)

    def test_assisted_submit_rejects_whitespace_case_empty_and_changed_target(self):
        reg_url = 'https://www.xuhui-marathon.com/b/FrAJj2/reg.html?subgroup=10%E5%85%AC%E9%87%8C%E8%B7%91'
        body = '''<form onsubmit="window.submits=(window.submits||0)+1;return false"><div class="divBmFormView">预览</div><a class="aBtnSubmit aBtn" onclick="document.querySelector('form').requestSubmit()">提交</a></form>'''
        self.page.route('**/*', lambda r: r.fulfill(status=200, content_type='text/html; charset=utf-8', body=body.encode()))
        for answer in (' SUBMIT', 'SUBMIT ', 'submit', ''):
            self.page.goto(reg_url)
            self.assertEqual(h.confirm_and_click_submit(self.page, lambda _, a=answer: a), 'cancelled')
            self.assertEqual(self.page.evaluate('window.submits||0'), 0)
        self.page.goto(reg_url)
        def replace_target(_):
            self.page.evaluate("document.querySelector('a').outerHTML='<a class=\"aBtnSubmit aBtn\" onclick=\"document.querySelector(\\'form\\').requestSubmit()\">换了</a>'")
            return 'SUBMIT'
        self.assertEqual(h.confirm_and_click_submit(self.page, replace_target), 'target_changed')
        self.assertEqual(self.page.evaluate('window.submits||0'), 0)

    def test_delayed_hidden_dropdown_uses_native_and_visible_readback(self):
        self.page.set_content('''<form onsubmit="window.submits=(window.submits||0)+1;return false">
          <div class="bx_ui_form_tab_n_blood"><div class="bx_ui_select bx_ui_select_type_dropdownlist">
            <select name="blood" style="display:none"><option value="">请选择</option><option value="A">A</option><option value="B" disabled>B</option></select>
            <input class="bx_ui_select_type_ddl_word" readonly value="请选择"></div></div>
          <div class="bx_ui_form_tab_n_clothesize"><div class="bx_ui_select bx_ui_select_type_dropdownlist">
            <select name="clothesize" style="display:none"><option value="女M">女M</option></select>
            <input class="bx_ui_select_type_ddl_word" readonly value="女M"></div></div>
          <button type="submit">确认并提交</button></form>
          <script>
          for (const box of document.querySelectorAll('.bx_ui_select_type_dropdownlist')) {
            const select=box.querySelector('select'), opener=box.querySelector('.bx_ui_select_type_ddl_word');
            opener.addEventListener('click',()=>setTimeout(()=>{
              const menu=document.createElement('div'); menu.className='bx_ui_select_type_ddl_list';
              for (const opt of select.options) { const item=document.createElement('div'); item.className='bx_ui_select_type_ddl_list_item'+(opt.disabled?' disabled':''); item.textContent=opt.text; item.dataset.value=opt.value; if(!opt.disabled) item.onclick=()=>{select.value=opt.value; opener.value=opt.text; menu.remove(); select.dispatchEvent(new Event('change',{bubbles:true}));}; menu.append(item); } document.body.append(menu);
            },10));
          }</script>''')
        result = {r['label']: r['status'] for r in h.fill_fields(self.page, {'血型': 'A', '衣服尺码': '女M'}, offline_test=True)}
        self.assertEqual(result, {'血型': 'filled', '衣服尺码': 'already_set'})
        self.assertEqual(self.page.locator('[name=blood]').input_value(), 'A')
        self.assertEqual(self.page.locator('.bx_ui_form_tab_n_blood .bx_ui_select_type_ddl_word').input_value(), 'A')
        self.assertEqual(self.page.evaluate('window.submits||0'), 0)
        missing = h.fill_fields(self.page, {'血型': 'RH'}, offline_test=True)
        self.assertEqual(missing, [{'label': '血型', 'status': 'option_missing'}])

    def test_closed_entry_never_navigates_or_reloads(self):
        urls = []
        def route(r):
            urls.append(r.request.url)
            r.fulfill(status=200, content_type='text/html; charset=utf-8', body='''<div class="divEntryList"><a class="aEntryItem disabled" sub="10公里跑" href="reg.html?subgroup=10公里跑">10公里跑 (名额已满)</a></div>'''.encode())
        self.page.route('**/*', route)
        self.page.goto(h.ENTRY)
        self.assertEqual(h.enter_available_group(self.page), 'unavailable')
        self.assertEqual(urls, [h.ENTRY])

    def test_enabled_entry_only_follows_normal_registration_link(self):
        urls = []
        def route(r):
            urls.append(r.request.url)
            body = '<div>手动声明页</div>' if 'reg.html' in r.request.url else '''<div class="divEntryList"><a class="aEntryItem" sub="10公里跑" href="reg.html?subgroup=10公里跑">10公里跑</a></div>'''
            r.fulfill(status=200, content_type='text/html; charset=utf-8', body=body.encode())
        self.page.route('**/*', route)
        self.page.goto(h.ENTRY)
        self.assertEqual(h.enter_available_group(self.page), 'entered')
        self.page.wait_for_url('**/reg.html?*')
        self.assertEqual(len(urls), 2)
        self.assertTrue(h.registration_link(self.page.url))


if __name__ == '__main__':
    unittest.main(verbosity=2)
