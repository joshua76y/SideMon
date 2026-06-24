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

# Language
除非特别约定，请永远用中文回复，包括思考过程。

## 项目概览

- 项目名：`RpiZeroMon` / `SideMon`。
- 目标：Mac 端采集主机状态和服务用量，通过 TCP 推送到多个显示端，副屏实时显示系统、API 用量、代理、Codex、天气、日历、oMLX 等信息。
- Mac 入口：`mac/sidemon.py`，同时支持 CLI sender 和 PyObjC/AppKit 设置窗口。
- Pi 端入口：`pirecv/sidemon-pil.py` — 树莓派 Zero W + 3.5" ILI9486 GPIO SPI 屏。
- CYD 端入口：`cyd/src/main.cpp` — ESP32 CYD + 2.8" ILI9341 TFT。
- macOS App 打包入口：`setup.py`，输出 `dist/RpiZeroMon.app`。
- Pi 部署脚本：`deploy_pi.sh`。
- CYD 构建：`cd cyd && pio run -t upload`。


## 显示端总览

本项目支持两种副屏显示端，Mac 端 Sender 可同时向两者推送数据。

### 显示端对比

| 特性 | Raspberry Pi Zero W | ESP32 CYD (Cheap Yellow Display) |
|------|-------------------|----------------------------------|
| 屏幕 | Waveshare 3.5" GPIO SPI | ILI9341 2.8" TFT |
| 分辨率 | 480×320 (竖屏→横屏用) | 240×320 (物理竖屏，rotation=3 横屏) |
| 控制器 | ILI9486 | ILI9341 |
| 渲染方式 | Pillow → /dev/fb1 | TFT_eSPI 库直接绘图 |
| 语言 | Python | C++ (Arduino) |
| 配网 | 通过 SSH 修改 wpa_supplicant | WiFiManager Web 配网 |
| 连接方式 | TCP client → Mac sender | TCP client → Mac sender |
| 默认端口 | 9988 | 9877 |
| 中文字体 | DroidSansFallbackFull / NotoSansCJK | cjk_font.h 点阵字库 |

---

## 显示端一：Raspberry Pi Zero W

### 硬件配置
- **设备**：Raspberry Pi Zero W
- **系统**：Raspberry Pi OS Lite (Bookworm)
- **屏幕**：Waveshare 3.5寸 ILI9486 GPIO SPI 触摸屏
- **分辨率**：480×320，写入 `/dev/fb1` 前旋转 180 度
- **驱动**：[lcddiy/LCD-show](https://github.com/lcddiy/LCD-show)，安装后将屏幕映射为 `/dev/fb1`
- **SSH**：`pi@<ip>`，密码 `qwe123`（本机部署用，勿外泄）

### 服务管理
- systemd 服务名：`sidemon-pil`
- 代码路径：`/home/pi/sidemon-pil.py`
- 接收端口：9988
- 启动：`sudo systemctl start sidemon-pil`
- 停止：`sudo systemctl stop sidemon-pil`
- 状态：`sudo systemctl status sidemon-pil`
- 日志：`sudo journalctl -u sidemon-pil -f`

### Pi 端关键参数
```
W, H = 480, 320
旋转180度后写入 framebuffer
字体：piboto > dejavu > fallback
CJK字体：DroidSansFallbackFull.ttf > NotoSansCJK-Regular.ttc
页面切换间隔：从 payload._control.interval 读取
页面列表：从 payload._control.pages 读取
```

### 部署命令
```bash
# 更新代码到 Pi
bash deploy_pi.sh
# 查看日志
SSHPASS='qwe123' sshpass -e ssh pi@<pi_ip> "sudo journalctl -u sidemon-pil --no-pager -n 20"
```

---

## 显示端二：ESP32 CYD (Cheap Yellow Display)

### 硬件配置
- **设备**：ESP32-2432S028R（CYD 2USB 版本）
- **芯片**：ESP32
- **屏幕**：ILI9341 2.8" TFT，240×320 像素
- **控制器**：ILI9341 (ILI9341_2_DRIVER)
- **背光控制**：GPIO 21，背光常亮（设为 HIGH）
- **触摸**：未启用（本项目不需要）

### TFT_eSPI 关键参数

```ini
; platformio.ini [env:cyd]
-DILI9341_2_DRIVER=1      # 驱动型号
-DTFT_WIDTH=240            # 逻辑宽度
-DTFT_HEIGHT=320           # 逻辑高度
-DTFT_MOSI=13              # SPI MOSI
-DTFT_MISO=12              # SPI MISO
-DTFT_SCLK=14              # SPI 时钟
-DTFT_CS=15                # 片选
-DTFT_DC=2                 # 数据/命令
-DTFT_RST=-1               # 硬件复位（未使用）
-DTFT_BL=21                # 背光引脚
-DTFT_BACKLIGHT_ON=HIGH    # 背光高电平亮
-DSPI_FREQUENCY=55000000   # SPI 频率
-DTFT_RGB_ORDER=0          # ⚠️ RGB 顺序：ILI9341 默认为 BGR，已确认正确
-DSMOOTH_FONT=1            # 抗锯齿字体
-DLOAD_FONT2,4,6,7,8=1     # 加载的字体号
```

### 显示初始化（main.cpp setup）
```cpp
tft.init();
tft.setRotation(3);    // 横屏模式（240宽×320高 → 320宽×240高）
tft.invertDisplay(true);  // ⚠️ ILI9341 必须反转显示
tft.fillScreen(TFT_BLACK);
// ⚠️ 不要调换 RGB 顺序（TFT_RGB_ORDER=0 已正确）
```

### 构建和烧录

```bash
# 编译并烧录（自动检测串口）
cd cyd && pio run -t upload

# 仅编译
cd cyd && pio run

# 查看串口输出
cd cyd && pio device monitor
```

### 配网（WiFiManager）

1. CYD 上电后若未连接 WiFi，自动启动 WiFiManager AP
2. 手机连接 `SideMon-Setup` 热点（无密码）
3. 浏览器打开 `192.168.4.1`
4. 扫描并选择 WiFi，输入密码
5. 提交后 CYD 自动重启并连接

⚠️ 配网凭据保存在 ESP32 的 EEPROM / LittleFS 中，重新烧录固件不会清除 WiFi 凭据。

### CYD 中文字库

中文字符通过 `cyd/src/cjk_font.h` 点阵字库渲染，使用 drawMixed() 混合绘制 CJK+ASCII。

当前已收录字符（约 113 个）：
```
上下中主代仪传体内出到前副动启器在处外天存完小屏已度式当待态感成据排接数
日时期机模气活流温湿点状理用盘磁离等系紫线统置节落行表负跃载运连速重量间
队风未知请稍候就绪端口客户收打开选配网设一二三四五六星晴朗多云雨阴雪雷广
州雾大周
```

添加新字符步骤：
1. 生成对应 Unicode 的 16×16 点阵位图
2. 在 `cjk_font.h` 的 `switch(c)` 中新增 case
3. 更新文件头部的字符数量

### CYD 颜色常量
```cpp
C_BLACK   0x0000  // 背景黑
C_WHITE   0xFFFF  // 文字白
C_DIM     0x8410  // 暗灰（标签用）
C_CARD    0x1082  // 卡片底色（深蓝灰）
HDR_BG    0x2104  // 标题栏背景
AC_SYS    0x07E0  // 系统页强调色（绿）
AC_API    0x07FF  // API页强调色（青）
AC_CLASH  0xF81F  // Clash页强调色（品红）
AC_CODEX  0xFDA0  // Codex页强调色（橙）
AC_WTHR   0x067F  // 天气页强调色（蓝青）
AC_DATE   0xFFE0  // 日期页强调色（黄）
AC_OMLX   0x07E0  // oMLX页强调色（绿）
AC_CODE   0x041F  // Codex环颜色
```

### CYD 接收端参数
- 接收端口：9877
- 使用 ArduinoJson 解析 JSON payload
- 从 payload._control.pages 读取页面列表
- 从 payload._control.interval 读取切换间隔（秒）
- 页面顺序：system, cswitch, clash, codex, weather, datetime, omlx

---

## Mac 端多屏配置

配置文件中 `hosts` 数组支持多个显示端：
```json
{
  "hosts": [
    {"ip": "192.168.1.37", "port": 9988},
    {"ip": "192.168.1.63", "port": 9877}
  ],
  "pages": ["system","ccswitch","clash","codex","weather","datetime","omlx"],
  "interval": 5.0,
  ...
}
```

UDP 自动查找功能可同时发现局域网内的 Pi Zero W 和 CYD。


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
