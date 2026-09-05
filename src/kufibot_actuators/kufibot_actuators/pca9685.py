import math
import time
from smbus2 import SMBus


class PCA9685:
    MODE1 = 0x00
    PRESCALE = 0xFE
    LED0_ON_L = 0x06
    LED0_ON_H = 0x07
    LED0_OFF_L = 0x08
    LED0_OFF_H = 0x09

    def __init__(self, address, busnum=1, debug=False):
        self.bus = SMBus(busnum)
        self.address = address
        self.debug = debug
        self.write(self.MODE1, 0x00)

    def write(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def read(self, reg):
        return self.bus.read_byte_data(self.address, reg)

    def set_pwm_freq(self, freq_hz):
        prescale_val = 25000000.0 / 4096.0 / float(freq_hz) - 1.0
        prescale = int(math.floor(prescale_val + 0.5))
        old_mode = self.read(self.MODE1)
        new_mode = (old_mode & 0x7F) | 0x10
        self.write(self.MODE1, new_mode)
        self.write(self.PRESCALE, prescale)
        self.write(self.MODE1, old_mode)
        time.sleep(0.005)
        self.write(self.MODE1, old_mode | 0x80)

    def set_pwm(self, channel, on, off):
        self.write(self.LED0_ON_L + 4 * channel, on & 0xFF)
        self.write(self.LED0_ON_H + 4 * channel, (on >> 8) & 0xFF)
        self.write(self.LED0_OFF_L + 4 * channel, off & 0xFF)
        self.write(self.LED0_OFF_H + 4 * channel, (off >> 8) & 0xFF)

    def set_duty_cycle(self, channel, percent):
        percent = max(0.0, min(100.0, percent))
        if percent == 100.0:
            # Bit 4 of LEDn_ON_H selects the PCA9685 full-on mode.
            self.set_pwm(channel, 4096, 0)
            return
        off = int(percent * 4096 / 100)
        self.set_pwm(channel, 0, off)

    def set_level(self, channel, value):
        self.set_pwm(channel, 0, 4095 if value else 0)

    def set_pulse_us(self, channel, pulse_us, freq_hz=50):
        period_us = 1_000_000.0 / freq_hz
        off = int(pulse_us * 4096 / period_us)
        self.set_pwm(channel, 0, off)

    def close(self):
        self.bus.close()
