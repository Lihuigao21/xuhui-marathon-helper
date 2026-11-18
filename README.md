# 上海西岸 10K 报名辅助

这是针对 [2026 上海西岸 10K 入口](https://www.xuhui-marathon.com/b/FrAJj2/entry.html) 的本地 Windows 辅助程序。

它可以在指定时间刷新一次入口、进入正常报名链接、预填普通资料，并在页面结构变化时停下。参赛声明、城市选择、验证码/登录/排队和资料核对由用户完成。普通模式不提交；`--assisted-submit` 模式只有在用户手动进入预览页并输入精确的 `SUBMIT` 后，才点击一次官方最终提交按钮。它不会绕过验证码、排队或声明，也不会付款。

## 安装

在仓库目录打开 PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

电脑需要安装 Microsoft Edge。程序使用独立的 `.browser-profile`，不会读取现有 Edge 的 Cookie、历史或密码。

## 使用

1. 双击 `编辑资料.cmd`，填写自己的资料并保存为 `profile.json`。这个文件已被 `.gitignore` 排除，不能提交。
2. 将 `开放时间` 写成带时区的北京时间，例如 `2026-11-19T11:00:00+08:00`。
3. 抢报名时提前约两分钟运行：

```cmd
启动助手.cmd --assisted-submit
```

4. 到达预览页后，确认页面上的资料无误，再在命令窗口输入大写 `SUBMIT` 并回车。订单或支付页面由用户继续处理。

只检查资料格式：

```cmd
启动助手.cmd --check-profile
```

只读检查入口和 Edge：

```cmd
启动助手.cmd --smoke
```

## 范围

普通字段包括姓名、证件、性别、手机号、邮箱、地址、血型、衣服尺码和紧急联系人。城市、日期/国家选择器及所有声明项可能需要手动操作。网站开放状态、排队、验证码、名额和最终报名成功不作保证。请勿把 `profile.json` 或 `.browser-profile` 上传分享。

## 测试

```powershell
.venv\Scripts\python.exe -X utf8 -B tests\test_helper.py
```

测试只使用本地 HTML fixture 和虚构资料，不创建真实订单。
