# SideMon

Mac system monitor → Raspberry Pi Zero W + 3.5" TFT secondary display.

![SideMon](preview.jpg)

## Architecture

```
┌─────────┐  TCP/JSON   ┌──────────────┐   fbcp    ┌──────────┐
│   Mac   │ ──────────→ │ Pi Zero W    │ ────────→ │ 3.5" TFT │
│ (sender)│  every 2s   │ (receiver +  │  DMA/SPI  │ ILI9486  │
└─────────┘             │  PIL render) │           │ 480×320  │
                        └──────────────┘           └──────────┘
```

- **Mac** (`mac/sidemon.py`): Collects system stats (CPU, memory, disk, network, temperature), proxy state (CC Switch, Clash Verge), Codex usage, and weather. Sends JSON over TCP every 2 seconds.
- **Pi Zero W** (`pirecv/sidemon-pil.py`): Receives JSON, renders 5 pages with Pillow, writes to `/dev/fb0`. `fbcp-ili9341` copies framebuffer to the ILI9486 SPI display via DMA. Pages cycle every 15 seconds.

## Pages

| # | Page | Data Source |
|---|------|------------|
| 0 | **System** | CPU %, MEM %, DISK %, load, network, temp, uptime |
| 1 | **CC Switch** | Provider, balance, current node, request stats |
| 2 | **Clash Verge** | Node, upload/download/total, expiry, update time |
| 3 | **Codex** | 5H/week token usage %, reset time, model, token counts |
| 4 | **Weather** | Temperature, description, feels-like, humidity, wind |

## Hardware

- **Raspberry Pi Zero W** with Raspberry Pi OS (Bookworm) Lite
- **WaveShare 3.5" TFT** (ILI9486 controller, 480×320, SPI via GPIO)
- GPIO pins: DC=BCM24, RST=BCM25, BL=BCM18, SPI0 (MOSI/SCLK/CE0)
- **fbcp-ili9341** (compiled from [fbcp-ili9341](https://github.com/juj/fbcp-ili9341)) with `-DWAVESHARE35B_ILI9486=ON`

## Setup

### Prerequisites

```bash
# Mac
pip3 install psutil requests

# Pi
sudo apt install python3-pil python3-numpy python3-rpi.gpio python3-spidev
```

### Pi — screen driver (fbcp-ili9341)

```bash
# Clone and build
git clone https://github.com/juj/fbcp-ili9341.git
cd fbcp-ili9341
mkdir build && cd build
cmake -DWAVESHARE35B_ILI9486=ON -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)
sudo install fbcp-ili9341 /usr/local/bin/

# Install systemd service
sudo cp /path/to/SideMon/pirecv/fbcp-ili9341.service /etc/systemd/system/
sudo systemctl enable fbcp-ili9341
sudo systemctl start fbcp-ili9341
```

### Pi — display receiver

```bash
# Copy files to Pi
scp pirecv/sidemon-pil.py pi@192.168.1.24:/home/pi/

# Install and start service
ssh pi@192.168.1.24
sudo cp /home/pi/sidemon-pil.py /home/pi/
sudo cp /path/to/SideMon/pirecv/sidemon-pil.service /etc/systemd/system/
sudo systemctl enable sidemon-pil
sudo systemctl start sidemon-pil
```

### Pi — /boot/config.txt additions

```
dtparam=spi=on
hdmi_group=2
hdmi_mode=87
hdmi_cvt=480 320 60
hdmi_force_hotplug=1
```

And in `/boot/cmdline.txt` add:
```
bcm2708_fb.fbwidth=480 bcm2708_fb.fbheight=320 bcm2708_fb.fbswap=1
```

### Mac — sender

```bash
cd mac
python3 sidemon.py --host 192.168.1.24 --port 9877 -i 2
```

Or use the launchd plist for auto-start:
```bash
cp com.sidemon.sender.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.sidemon.sender.plist
```

## Files

```
SideMon/
├── README.md
├── mac/
│   └── sidemon.py          # Mac sender: collects & sends system data
├── pirecv/
│   ├── sidemon-pil.py       # Pi receiver: PIL renderer → /dev/fb0
│   ├── ili9486.py           # Direct SPI driver (backup, not used with fbcp)
│   ├── sidemon-pil.service  # systemd unit for receiver
│   └── fbcp-ili9341.service # systemd unit for screen driver
└── run_sender.sh            # Wrapper to keep sender alive
```

## Direct SPI mode (alternative)

If fbcp-ili9341 is unreliable, the receiver can drive the ILI9486 directly via SPI:

1. Stop and disable fbcp-ili9341
2. Use `ili9486.py` as the display driver (requires numpy, spidev, RPi.GPIO)
3. Edit `sidemon-pil.py` to call `ili9486.display_rgba()` instead of `write_fb()`

Known issue: RPi.GPIO threading safety — direct SPI mode may require all display calls on the same thread.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| White screen | Check fbcp is running: `systemctl status fbcp-ili9341` |
| "Waiting for data..." stuck | Check Mac sender is running and can reach Pi's IP |
| Screen flickers / tears | Try reducing SPI speed or enable DMA in fbcp |
| Missing data fields | Ensure Mac sender version matches Pi receiver field names |
