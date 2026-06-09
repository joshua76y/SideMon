# AGENTS.md

本文件适用于整个 `SideMon` 仓库。

# 行为准则

用来减少 AI 帮你干活时常犯的错误（写代码、写文档、查资料、做表格都适用）。可以和项目自己的说明合并使用。
取舍：这套准则偏向「谨慎」而不是「快」。遇到特别琐碎的小任务，自己看着办。

## 1. 想清楚再写
不要瞎猜，不要藏着困惑，把权衡讲出来。
-把你的假设明确说出来。不确定就问我。
-如果有多种理解，都摆出来，别自己悄悄选一个。
-如果有更简单的做法，直说。该反对的时候反对。
-如果哪里不清楚，停下来，说清楚卡在哪，然后问。

## 2. 简单优先
用最少的东西把问题解决，不做任何多余的事。
-不加我没要求的功能。
-不给一次性的活儿搭一套通用框架。
-不加我没要求的「灵活性」或「可配置」。
-不为不可能发生的情况提前操心。
-如果你交付的东西明显比需要的多，砍到刚好够用，重来。
-问自己一句：一个资深的人会不会觉得这过度复杂了？会的话，就简化。

## 3. 外科手术式改动
只动你必须动的，只收拾你自己制造的乱。
-不要去「改进」旁边没让你碰的内容、格式。
-不要翻新没坏的东西。
-跟着原本的风格走，哪怕你自己会用别的写法。
-看到无关的、原本就有的多余内容，提一句就行，别删。
-只收拾你这次改动产生的多余东西；原本就有的旧内容，不让你删就别删。
-一条判据：每一处改动，都要能直接追溯到我的需求。

## 4. 目标驱动执行
先定清楚「做到什么算成功」，然后对着标准跑到达标。
-「把这份资料整理好」→「按这三个维度分类（是什么、做什么、怎么做），每条梳理内容配一句出处」。
-（写代码的话）「加个校验」→「先写针对非法输入的测试，再让它们通过」。
-（写代码的话）「修复这个 bug」→「先写一个能复现 bug 的测试，再让它通过」。
-多步任务，先说一个简短计划，每一步对应一个验证点。
-成功标准给得够强，你才能自己对答案；标准太虚（比如「弄好就行」），就只能不停来问我。

这套准则起作用的标志：交付里没必要的多余动作变少了，因为过度复杂而返工的次数变少了，该问清楚的问题在动手之前就问了，而不是出错之后才问。


---

# 识图能力

你的底层模型不具备原生识图能力。遇到图片时，**不要用 Read 工具**，改用 vision.js：

```
node vision.js "<图片路径>" "用中文描述这张图片"
```

## 触发场景

- 用户分享图片路径（本地或网络 URL）
- 消息中出现 "Saved attachments:" 并列出图片
- 用户要求分析、描述、识别图片内容

## 配置好之后

用户直接发图片，自动识图，无需手动打命令。

# Language
除非特别约定，请永远用中文回复，包括思考过程。




## 项目概览

- 项目名：`RpiZeroMon` / `SideMon`。
- 目标：Mac 端采集主机状态和服务用量，通过 TCP 推送到树莓派 Zero W；Pi 端用 Pillow 渲染 480x320 GPIO SPI 屏幕。
- Mac 入口：`mac/sidemon.py`，同时支持 CLI sender 和 PyObjC/AppKit 设置窗口。
- Pi 入口：`pirecv/sidemon-pil.py`。
- macOS App 打包入口：`setup.py`，输出 `dist/RpiZeroMon.app`。
- Pi 部署脚本：`deploy_pi.sh`，当前默认 Pi 地址为 `192.168.1.37`。

## 运行与构建

- Mac 端本地运行：
  ```bash
  python3 mac/sidemon.py --host 192.168.1.37 -i 1
  ```
- Mac 端设置窗口：
  ```bash
  python3 mac/sidemon.py --ui
  ```
- 单次采集并打印 JSON：
  ```bash
  python3 mac/sidemon.py --once --host 192.168.1.37
  ```
- 打包 macOS App：
  ```bash
  rm -rf build dist
  python3 setup.py py2app
  rm -rf ~/Desktop/RpiZeroMon.app
  cp -R dist/RpiZeroMon.app ~/Desktop/
  ```
- 部署 Pi 端：
  ```bash
  bash deploy_pi.sh
  ```
- 查看 Pi 日志：
  ```bash
  SSHPASS='qwe123' sshpass -e ssh -o StrictHostKeyChecking=no pi@192.168.1.37 \
    "sudo journalctl -u sidemon-pil --no-pager -n 20"
  ```

## 代码约定

- 保持单文件脚本风格，避免引入大型框架。
- Pi 端屏幕固定为 `480x320`，最终写入 framebuffer 前会旋转 180 度。
- Pi 端字体优先使用 `/usr/share/fonts/truetype/piboto`，其次使用 DejaVu。
- 页面顺序在 `pirecv/sidemon-pil.py` 的 `page_cycler()` 中维护，目前为：
  `system`, `ccswitch`, `clash`, `codex`, `weather`, `omlx`。
- Mac 端会通过 payload 的 `_control.pages` 推送启用页面和排序；Pi 端必须忽略未知 page key，并在空列表时回退默认顺序。
- 修改页面数据字段时，需要同步更新：
  - Mac 端 payload 生成逻辑：`mac/sidemon.py`
  - Pi 端页面渲染逻辑：`pirecv/sidemon-pil.py`
- Mac 端设置保存到 `~/Library/Application Support/RpiZeroMon/config.json`；API key 按当前产品选择明文保存到该 JSON。
- 不要把运行产生的 `build/`, `dist/`, `.DS_Store` 加入提交。

## 数据源说明

- 系统状态：`psutil`。
- API Usage：DeepSeek API + CC Switch SQLite 数据库。
- Clash：`/tmp/verge/verge-mihomo.sock`。
- Codex：`~/.codex/state_5.sqlite`。
- 天气：`wttr.in`。
- oMLX：`http://127.0.0.1:9876/health` 和 `~/.omlx/stats.json`。

## 安全注意

- 不要新增硬编码 API key、密码、token。需要时优先使用配置 JSON、环境变量或命令行参数。
- 现有脚本里有本机部署用的固定 Pi 密码和 API 配置，修改时不要扩大暴露范围。
- GitHub 推送前检查 `git status --short`，只提交与当前任务相关的文件。

## 验证清单

- Python 语法检查：
  ```bash
  python3 -m py_compile mac/sidemon.py pirecv/sidemon-pil.py
  ```
- Mac sender 冒烟测试：
  ```bash
  python3 mac/sidemon.py --once --host 192.168.1.37
  ```
- Pi 端部署后确认日志没有 `Render(...)` 或 traceback。
- 修改图标后，重新生成 `assets/icon.icns`，再重新运行 `python3 setup.py py2app` 并覆盖桌面 App。
