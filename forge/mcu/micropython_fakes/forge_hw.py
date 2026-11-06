# forge_hw - the simulated hardware behind forge's fake `machine` module
# (MicroPython unix port, also CPython). Tests set it up and read it back:
#
#   import forge_hw
#   forge_hw.i2c_device(0x76, {0xD0: b"\x60"})    # a sensor's registers
#   forge_hw.set_adc(26, [1200, 1300])             # successive read_u16() values
#   forge_hw.set_input(15, 0)                       # an input pin's level
#   forge_hw.edge(15, 1)                            # change it and fire its irq
#   forge_hw.uart_feed(0, b"OK\r\n")               # bytes a UART will read
#   forge_hw.tick(1000)                             # run timers due in 1000 ms
#   forge_hw.pin(25)                                # an output's level
#   forge_hw.events                                 # everything the code did
#
# Pure Python that MicroPython and CPython both run.

events = []
pins = {}          # id -> level
modes = {}         # id -> mode
irqs = {}          # id -> (handler, trigger)
adc = {}           # id -> list of values (last one repeats)
i2c = {}           # addr -> {reg: bytes} (bytes written to regs are stored)
i2c_raw = {}       # addr -> list of bytes objects for readfrom()
uart_in = {}       # id -> bytearray
uart_out = {}      # id -> bytearray
pwm = {}           # id -> {"freq": n, "duty_u16": n}
timers = []        # [period_ms, next_due, callback, periodic, timer]
now_ms = [0]


def reset():
    del events[:]
    for d in (pins, modes, irqs, adc, i2c, i2c_raw, uart_in, uart_out, pwm):
        d.clear()
    del timers[:]
    now_ms[0] = 0


def log(*ev):
    events.append(ev)


def pin(pid):
    return pins.get(pid, 0)


def set_input(pid, level):
    pins[pid] = 1 if level else 0


def edge(pid, level):
    """Set an input and call its irq handler when the edge matches."""
    old = pins.get(pid, 0)
    new = 1 if level else 0
    pins[pid] = new
    h = irqs.get(pid)
    if h and old != new:
        handler, trigger, obj = h
        rising, falling = 1, 2
        if (new and trigger & rising) or (not new and trigger & falling):
            handler(obj)


def set_adc(pid, values):
    adc[pid] = list(values) if isinstance(values, (list, tuple)) else [values]


def i2c_device(addr, regs=None, raw=None):
    i2c[addr] = dict(regs or {})
    if raw is not None:
        i2c_raw[addr] = list(raw)


def uart_feed(uid, data):
    uart_in.setdefault(uid, bytearray()).extend(data)


def uart_sent(uid):
    return bytes(uart_out.get(uid, b""))


def tick(ms):
    """Advance the fake clock by ms and run every timer callback that falls
    due, in time order (periodic timers repeat)."""
    end = now_ms[0] + ms
    while True:
        due = [t for t in timers if t[1] <= end]
        if not due:
            break
        due.sort(key=lambda t: t[1])
        t = due[0]
        now_ms[0] = t[1]
        if t[3]:
            t[1] += t[0]
        else:
            timers.remove(t)
        t[2](t[4])
    now_ms[0] = end
