# SideMon

Mac 系统状态副屏监控 —— 在树莓派 Zero W 配 3.5 寸 TFT 屏幕上实时显示主机信息。

## 效果预览

480×320 分辨率，深色主题，6 个页面每 15 秒循环切换：

| 页面 | 内容 |
|------|------|
| **System** | CPU / 内存 / 磁盘环形图，系统负载，网络速率，温度，运行时长 |
| **CC Switch** | DeepSeek 余额，当前节点，请求数量与成功率 |
| **Clash** | 流量使用量，上传/下载总量，到期日，模式与版本 |
| **Codex** | 5 小时 / 7 天 Token 用量百分比，预算上限，模型，重置时间 |
| **Weather** | 当前温度、天气描述、体感温度、湿度、风力、最高/最低温 |
| **omLX** | 本地模型推理统计：请求数、Token、缓存命中率、推理速度、显存 |

## 架构

```
┌──────────┐   TCP/JSON    ┌──────────────┐    fbcp     ┌───────────┐
│   Mac    │ ────────────→ │  Pi Zero W   │ ─────────→ │ 3.5" TFT  │
│ (发送端)  │   每 2 秒     │ (接收+渲染)   │  DMA/SPI   │ ILI9486   │
└──────────┘               └──────────────┘            │ 480×320   │
                                                       └───────────┘
```

- **Mac 端** (`mac/sidemon.py`)：采集 CPU、内存、磁盘、网络、温度等系统信息，以及 CC Switch 余额、Clash 代理状态、Codex Token 用量、天气数据，通过 TCP 每 2 秒发送 JSON 到树莓派。
- **树莓派 Zero W** (`pirecv/sidemon-pil.py`)：接收 JSON，用 Pillow 渲染 6 个页面到 `/dev/fb0`，`fbcp-ili9341` 通过 DMA 将帧缓冲推送到 ILI9486 SPI 屏幕。

## 硬件

| 组件 | 型号 |
|------|------|
| 主控 | Raspberry Pi Zero W |
| 屏幕 | WaveShare 3.5" TFT (ILI9486, 480×320, SPI) |
| 系统 | Raspberry Pi OS (Bookworm) Lite |
| 连接 | GPIO：DC=BCM24, RST=BCM25, BL=BCM18, SPI0 |

### /boot/config.txt

```
dtparam=spi=on
hdmi_group=2
hdmi_mode=87
hdmi_cvt=480 320 60
hdmi_force_hotplug=1
```

### /boot/cmdline.txt 追加

```
bcm2708_fb.fbwidth=480 bcm2708_fb.fbheight=320 bcm2708_fb.fbswap=1
```

## 安装

### 树莓派

```bash
# 安装依赖
sudo apt install python3-pil python3-numpy python3-rpi.gpio

# 编译 fbcp-ili9341（帧缓冲到 SPI 屏幕的 DMA 驱动）
git clone https://github.com/juj/fbcp-ili9341.git
cd fbcp-ili9341 && mkdir build && cd build
cmake -DWAVESHARE35B_ILI9486=ON -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)
sudo install fbcp-ili9341 /usr/local/bin/

# 复制接收端脚本
scp pirecv/sidemon-pil.py pi@192.168.1.24:/home/pi/

# 安装 systemd 服务（fbcp + sidemon-pil 均设为开机自启）
# 服务文件见 pirecv/ 目录
sudo systemctl enable fbcp-ili9341 sidemon-pil
sudo systemctl start fbcp-ili9341 sidemon-pil
```

### Mac

```bash
pip3 install psutil requests

# 运行发送端（需根据实际 IP 调整）
cd mac
python3 sidemon.py --host 192.168.1.24 --port 9877 -i 2

# 或使用 launchd 开机自启
cp com.sidemon.sender.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.sidemon.sender.plist
```

## 目录结构

```
SideMon/
├── README.md
├── mac/
│   ├── sidemon.py            # Mac 发送端
│   └── requirements.txt
├── pirecv/
│   ├── sidemon-pil.py        # Pi 接收端（Pillow 渲染 → /dev/fb0）
│   └── ili9486.py            # ILI9486 直驱模块（备用）
└── run_sender.sh             # 发送端保活脚本
```

## 故障排查

| 现象 | 解决方法 |
|------|----------|
| 白屏 | 检查 `fbcp-ili9341` 是否运行：`systemctl status fbcp-ili9341` |
| 一直显示 "Waiting for data..." | Mac 发送端未运行或 IP 不通；检查 Mac IP 和防火墙 |
| 屏幕闪烁 | 调整 fbcp 编译参数或降低 SPI 频率 |
| 某些页面数据不全 | Mac 发送端与实际软件版本不匹配，检查字段名是否对应 |
| 中文显示为方块 | DroidSansFallbackFull.ttf 字体会导致白屏，已移除中文 |

## 设计说明

- 所有文字使用 Piboto 英文字体（Raspberry Pi OS 自带），渲染清晰
- 深色主题配色，环形进度条直观展示 CPU/内存/磁盘使用率
- 15 秒自动翻页，底部圆点指示当前页面位置
- 资源占用极低：Pi Zero W 上 CPU 占用 < 5%，内存 < 30 MB
