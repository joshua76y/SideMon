# AGENTS.md

本文件适用于整个 `SideMon` 仓库。

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
