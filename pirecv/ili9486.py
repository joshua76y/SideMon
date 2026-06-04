import spidev, time, numpy as np, threading

_DC, _RST, _BL = 24, 25, 18
_spi = None
_W, _H = 480, 320
_inited = False
_gpio_lock = threading.Lock()

def _gpio():
    import RPi.GPIO as GPIO
    return GPIO

def _ensure_gpio():
    global _spi
    with _gpio_lock:
        GPIO = _gpio()
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(_DC, GPIO.OUT)
        GPIO.setup(_RST, GPIO.OUT)
        GPIO.setup(_BL, GPIO.OUT)
        GPIO.output(_BL, GPIO.HIGH)
        _spi = spidev.SpiDev()
        _spi.open(0, 0)
        _spi.mode = 0
        _spi.max_speed_hz = 31200000

def _cmd(*b):
    with _gpio_lock:
        GPIO = _gpio()
        GPIO.output(_DC, GPIO.LOW)
        _spi.writebytes(list(b))

def _data(buf):
    with _gpio_lock:
        GPIO = _gpio()
        GPIO.output(_DC, GPIO.HIGH)
        chunk = 4096
        for i in range(0, len(buf), chunk):
            _spi.writebytes(buf[i:i+chunk])

def init():
    global _inited
    if _inited:
        return
    _ensure_gpio()
    with _gpio_lock:
        GPIO = _gpio()
        GPIO.output(_RST, GPIO.LOW)
        time.sleep(0.1)
        GPIO.output(_RST, GPIO.HIGH)
        time.sleep(0.2)

    _cmd(0x01)
    time.sleep(0.15)
    _cmd(0x11)
    time.sleep(0.15)
    _cmd(0x3A, 0x55)
    _cmd(0x36, 0x28)
    _cmd(0xB1, 0xA0, 0x11)
    _cmd(0xB4, 0x02)
    _cmd(0x29)
    _cmd(0x2A, 0x00, 0x00, 0x01, 0xDF)
    _cmd(0x2B, 0x00, 0x00, 0x01, 0x3F)
    _inited = True

def display_rgba(rgba_bytes):
    if not _inited:
        init()
    W, H = _W, _H
    expected = W * H * 4
    if len(rgba_bytes) != expected:
        raise ValueError(f'Expected {expected} bytes, got {len(rgba_bytes)}')

    arr = np.frombuffer(rgba_bytes, dtype=np.uint8).reshape((H, W, 4))
    R = arr[:, :, 0].astype(np.uint16)
    G = arr[:, :, 1].astype(np.uint16)
    B = arr[:, :, 2].astype(np.uint16)
    rgb565 = ((R & 0xF8) << 8) | ((G & 0xFC) << 3) | (B >> 3)
    hi = (rgb565 >> 8).astype(np.uint8)
    lo = (rgb565 & 0xFF).astype(np.uint8)
    buf = np.empty(W * H * 2, dtype=np.uint8)
    buf[0::2] = hi.flatten()
    buf[1::2] = lo.flatten()

    _cmd(0x2C)
    _data(buf.tobytes())

def cleanup():
    global _inited
    if not _inited:
        return
    _spi.close()
    _gpio().cleanup()
    _inited = False
