# A fake `machine` module for testing MicroPython code without a board: the
# common classes of the RP2040 / ESP32 ports with the same names and
# methods, backed by forge_hw (see there). Not every port-specific argument
# is modelled; unknown keyword arguments are accepted and ignored.
import forge_hw as _hw


class Pin:
    IN = 0
    OUT = 1
    OPEN_DRAIN = 2
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2

    def __init__(self, id, mode=-1, pull=-1, value=None, **kw):
        self.id = id
        self.init(mode, pull, value)

    def init(self, mode=-1, pull=-1, value=None, **kw):
        if mode != -1:
            _hw.modes[self.id] = mode
        if pull == Pin.PULL_UP and self.id not in _hw.pins:
            _hw.pins[self.id] = 1
        if value is not None:
            self.value(value)
        _hw.log("pin.init", self.id, mode, pull)

    def value(self, v=None):
        if v is None:
            return _hw.pins.get(self.id, 0)
        _hw.pins[self.id] = 1 if v else 0
        _hw.log("pin", self.id, _hw.pins[self.id])

    def __call__(self, v=None):
        return self.value(v)

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)

    def high(self):
        self.value(1)

    def low(self):
        self.value(0)

    def toggle(self):
        self.value(0 if self.value() else 1)

    def irq(self, handler=None, trigger=3, **kw):
        _hw.irqs[self.id] = (handler, trigger, self)
        _hw.log("pin.irq", self.id, trigger)

    def __repr__(self):
        return "Pin(%s)" % (self.id,)


class Signal:
    def __init__(self, pin, invert=False, *a, **kw):
        self.pin = pin if isinstance(pin, Pin) else Pin(pin, *a, **kw)
        self.invert = invert

    def value(self, v=None):
        if v is None:
            return self.pin.value() ^ (1 if self.invert else 0)
        self.pin.value((1 if v else 0) ^ (1 if self.invert else 0))

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)


def _pid(p):
    return p.id if isinstance(p, Pin) else p


class ADC:
    def __init__(self, pin, **kw):
        self.id = _pid(pin)

    def read_u16(self):
        vals = _hw.adc.get(self.id) or [0]
        v = vals.pop(0) if len(vals) > 1 else vals[0]
        _hw.log("adc", self.id, v)
        return v

    def read(self):
        return self.read_u16() >> 4


class PWM:
    def __init__(self, pin, freq=None, duty_u16=None, **kw):
        self.id = _pid(pin)
        _hw.pwm[self.id] = {"freq": 0, "duty_u16": 0}
        if freq is not None:
            self.freq(freq)
        if duty_u16 is not None:
            self.duty_u16(duty_u16)

    def freq(self, f=None):
        if f is None:
            return _hw.pwm[self.id]["freq"]
        _hw.pwm[self.id]["freq"] = f
        _hw.log("pwm.freq", self.id, f)

    def duty_u16(self, d=None):
        if d is None:
            return _hw.pwm[self.id]["duty_u16"]
        _hw.pwm[self.id]["duty_u16"] = d
        _hw.log("pwm.duty", self.id, d)

    def deinit(self):
        _hw.log("pwm.deinit", self.id)


class I2C:
    def __init__(self, id=0, scl=None, sda=None, freq=400000, **kw):
        self.id = id

    def scan(self):
        return sorted(_hw.i2c)

    def _dev(self, addr):
        if addr not in _hw.i2c:
            raise OSError(19)        # ENODEV, as the real ports raise for no ACK
        return _hw.i2c[addr]

    def readfrom_mem(self, addr, reg, n, **kw):
        regs = self._dev(addr)
        out = bytearray()
        r = reg
        while len(out) < n:
            out.extend(regs.get(r, b"\x00"))
            r += 1
        _hw.log("i2c.read", addr, reg, n)
        return bytes(out[:n])

    def readfrom_mem_into(self, addr, reg, buf, **kw):
        data = self.readfrom_mem(addr, reg, len(buf))
        for i in range(len(buf)):
            buf[i] = data[i]

    def writeto_mem(self, addr, reg, buf, **kw):
        regs = self._dev(addr)
        for i in range(len(buf)):
            regs[reg + i] = bytes([buf[i]])
        _hw.log("i2c.write", addr, reg, bytes(buf))

    def readfrom(self, addr, n, stop=True):
        self._dev(addr)
        q = _hw.i2c_raw.get(addr) or []
        data = q.pop(0) if q else b"\x00" * n
        _hw.log("i2c.readfrom", addr, n)
        return bytes(data[:n])

    def writeto(self, addr, buf, stop=True):
        self._dev(addr)
        _hw.log("i2c.writeto", addr, bytes(buf))
        return 1


class SoftI2C(I2C):
    pass


class SPI:
    def __init__(self, id=0, *a, **kw):
        self.id = id
        self.reply = bytearray()

    def write(self, buf):
        _hw.log("spi.write", self.id, bytes(buf))

    def read(self, n, write=0):
        _hw.log("spi.read", self.id, n)
        return bytes(n)

    def write_readinto(self, out, into):
        _hw.log("spi.xfer", self.id, bytes(out))
        for i in range(len(into)):
            into[i] = 0


class UART:
    def __init__(self, id=0, baudrate=9600, **kw):
        self.id = id
        _hw.uart_in.setdefault(id, bytearray())
        _hw.uart_out.setdefault(id, bytearray())

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        _hw.uart_out[self.id].extend(data)
        return len(data)

    def any(self):
        return len(_hw.uart_in[self.id])

    def read(self, n=-1):
        buf = _hw.uart_in[self.id]
        if not buf:
            return None
        n = len(buf) if n is None or n < 0 else n
        out = bytes(buf[:n])
        del buf[:n]
        return out

    def readline(self):
        buf = _hw.uart_in[self.id]
        if not buf:
            return None
        i = buf.find(b"\n")
        n = len(buf) if i < 0 else i + 1
        return self.read(n)


class Timer:
    ONE_SHOT = 0
    PERIODIC = 1

    def __init__(self, id=-1, **kw):
        self.entry = None
        if kw:
            self.init(**kw)

    def init(self, mode=1, period=1000, callback=None, freq=None, **kw):
        self.deinit()
        if freq:
            period = int(1000 / freq)
        self.entry = [period, _hw.now_ms[0] + period, callback, mode == Timer.PERIODIC, self]
        _hw.timers.append(self.entry)

    def deinit(self):
        if self.entry in _hw.timers:
            _hw.timers.remove(self.entry)
        self.entry = None


class WDT:
    def __init__(self, id=0, timeout=5000):
        _hw.log("wdt", timeout)

    def feed(self):
        _hw.log("wdt.feed")


def freq(f=None):
    return 125000000 if f is None else None


def reset():
    _hw.log("reset")
    raise SystemExit("machine.reset()")


def unique_id():
    return b"\xe6\x61\x38\x00\x00\x00\x00\x00"


def idle():
    pass


def lightsleep(ms=None):
    _hw.log("lightsleep", ms)


def deepsleep(ms=None):
    _hw.log("deepsleep", ms)
