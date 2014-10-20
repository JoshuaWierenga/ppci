import struct
import time
import logging

import usb.core
import usb.util
from .devices import Interface
from . import adi

"""
   More or less copied from:
     https://github.com/texane/stlink

   Tracing from:
     https://github.com/obe1line/stlink-trace
"""


class STLinkException(Exception):
    """ Exception used for interfaces and devices """
    pass


JTAG_WRITEDEBUG_32BIT = 0x35
JTAG_READDEBUG_32BIT = 0x36
TRACE_GET_BYTE_COUNT = 0x42

# cortex M3
CM3_REG_CPUID = 0xE000ED00


class STLink2(Interface):
    """ STlink2 interface implementation. """
    ST_VID = 0x0483
    STLINK2_PID = 0x3748

    DFU_MODE, MASS_MODE, DEBUG_MODE = 0, 1, 2

    CORE_RUNNING = 0x80
    CORE_HALTED = 0x81

    # Commands:
    GET_VERSION = 0xf1
    DEBUG_COMMAND = 0xf2
    DFU_COMMAND = 0xf3
    GET_CURRENT_MODE = 0xf5

    # dfu commands:
    DFU_EXIT = 0x7

    # debug commands:
    DEBUG_ENTER = 0x20
    DEBUG_EXIT = 0x21
    DEBUG_ENTER_SWD = 0xa3
    DEBUG_GETSTATUS = 0x01
    DEBUG_FORCEDEBUG = 0x02
    DEBUG_RESETSYS = 0x03
    DEBUG_READALLREGS = 0x04
    DEBUG_READREG = 0x5
    DEBUG_WRITEREG = 0x6
    DEBUG_READMEM_32BIT = 0x7
    DEBUG_WRITEMEM_32BIT = 0x8
    DEBUG_RUNCORE = 0x9
    DEBUG_STEPCORE = 0xa

    def __init__(self, stlink2=None):
        self.logger = logging.getLogger('stlink2')
        self._isOpen = False
        if not stlink2:
            stlink2 = usb.core.find(idVendor=self.ST_VID,
                                    idProduct=self.STLINK2_PID)
            if not stlink2:
                raise STLinkException('Could not find an ST link 2 interface')
        assert isinstance(stlink2, usb.core.Device)
        self._dev = stlink2

    def __str__(self):
        if self.IsOpen:
            return 'STlink2 device version {0}'.format(self.Version)
        else:
            return 'STlink2 device'

    def open(self):
        if self.IsOpen:
            return
        self.logger.debug('Opening device')
        self._dev.set_configuration()
        for cfg in self._dev:
            self.logger.debug('Cfg: {}'.format(cfg.bConfigurationValue))
            for intf in cfg:
                self.logger.debug('Intf: {}'.format(intf.bInterfaceNumber))
                for ep in intf:
                    self.logger.debug('EP: {}'.format(ep.bEndpointAddress))
        cfg = self._dev.get_active_configuration()

        self._isOpen = True

        # First initialization:
        if self.CurrentMode == self.DFU_MODE:
            self.exitDfuMode()
        if self.CurrentMode != self.DEBUG_MODE:
            self.enterSwdMode()

        self.logger.debug('Opening device succes!')

    def close(self):
        if self.IsOpen:
            self.logger.debug('Closing device')
            # TODO: reset is required here?
            self.exit_debug_mode()
            self._dev.reset()
            self._isOpen = False

    @property
    def IsOpen(self):
        return self._isOpen

    # modes:
    def getCurrentMode(self):
        """ Get mode of stlink """
        self.logger.debug('Get mode')
        cmd = bytearray(16)
        cmd[0] = self.GET_CURRENT_MODE
        reply = self.send_recv(cmd, 2)  # Expect 2 bytes back
        mode = reply[0]
        self.logger.debug('Mode is {}'.format(mode))
        return mode
    CurrentMode = property(getCurrentMode)

    @property
    def CurrentModeString(self):
        modes = {self.DFU_MODE: 'dfu', self.MASS_MODE: 'massmode',
                 self.DEBUG_MODE: 'debug'}
        mode = modes[self.CurrentMode]
        self.logger.debug('Mode is {}'.format(mode))
        return mode

    def exitDfuMode(self):
        self.logger.info('Exit dfu mode')
        cmd = bytearray(16)
        cmd[0:2] = self.DFU_COMMAND, self.DFU_EXIT
        self.send_recv(cmd)

    def enterSwdMode(self):
        self.logger.debug('Enter swd mode')
        cmd = bytearray(16)
        cmd[0:3] = self.DEBUG_COMMAND, self.DEBUG_ENTER, self.DEBUG_ENTER_SWD
        self.send_recv(cmd)

    def exit_debug_mode(self):
        self.logger.debug('Exit debug mode')
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_EXIT
        self.send_recv(cmd)

    def get_version(self):
        """ Get stlink version of hardware and firmware """
        if hasattr(self, '_version'):
            return self._version
        self.logger.debug('Get stlink version')
        cmd = bytearray(16)
        cmd[0] = self.GET_VERSION
        data = self.send_recv(cmd, 6)  # Expect 6 bytes back
        # Parse 6 bytes into various versions:
        b0, b1, b2, b3, b4, b5 = data
        stlink_v = b0 >> 4
        jtag_v = ((b0 & 0xf) << 2) | (b1 >> 6)
        swim_v = b1 & 0x3f
        vid = (b3 << 8) | b2
        pid = (b5 << 8) | b4
        self._version = 'stlink={} jtag={} swim={} vid:pid={:04X}:{:04X}'\
                        .format(stlink_v, jtag_v, swim_v, vid, pid)
        return self._version

    Version = property(get_version)

    @property
    def ChipId(self):
        return self.read_debug32(0xE0042000)

    @property
    def CpuId(self):
        u32 = self.read_debug32(CM3_REG_CPUID)
        implementer_id = (u32 >> 24) & 0x7f
        variant = (u32 >> 20) & 0xf
        part = (u32 >> 4) & 0xfff
        revision = u32 & 0xf
        return implementer_id, variant, part, revision

    def get_status(self):
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_GETSTATUS
        reply = self.send_recv(cmd, 2)
        return reply[0]

    Status = property(get_status)

    @property
    def StatusString(self):
        s = self.Status
        statii = {self.CORE_RUNNING: 'CORE RUNNING',
                  self.CORE_HALTED: 'CORE HALTED'}
        if s in statii:
            return statii[s]
        return 'Unknown status'

    def reset(self):
        """ Resets the core """
        self.logger.info('Reset core')
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_RESETSYS
        self.send_recv(cmd, 2)

    # debug commands:
    def step(self):
        self.logger.info('Single step')
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_STEPCORE
        self.send_recv(cmd, 2)

    def run(self):
        self.logger.info('Run core')
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_RUNCORE
        self.send_recv(cmd, 2)

    def halt(self):
        self.logger.info('Halt core')
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_FORCEDEBUG
        self.send_recv(cmd, 2)

    # Tracing:
    def traceEnable(self):
        """ Configure stlink to send trace data """
        self.logger.info('Enable tracing')

        # Set DHCSR to C_HALT and C_DEBUGEN
        self.write_debug32(0xE000EDF0, 0xA05F0003)

        # Enable TRCENA:
        self.write_debug32(0xE000EDFC, 0x01000000)

        # ?? Enable write?? PF_CTRL
        self.write_debug32(0xE0002000, 0x2)

        # TODO: send other commands

        # DBGMCU_CR enable asynchronous transmission:
        self.write_debug32(0xE0042004, 0x27)  # Enable trace in async mode

        # TPIU config:
        uc_freq = 16  # uc frequency
        stlink_freq = 2  # TODO: parameterize the stlink frequency
        divisor = int(uc_freq / stlink_freq) - 1
        self.logger.debug('uController frequency: {} MHz'.format(uc_freq))
        self.logger.debug('stlink frequency: {} MHz'.format(stlink_freq))

        # current port size register --> 1 == port size = 1
        self.write_debug32(0xE0040004, 0x00000001)

        # random clock divider??
        self.write_debug32(0xE0040010, divisor)

        # self.magicCommand41()
        self.magicCommand40()

        self.write_debug32(0xE00400F0, 0x2)  # selected pin protocol (2 == NRZ)

        # continuous formatting:
        self.write_debug32(0xE0040304, 0x100)  # or 0x100?

        # ITM config:
        # Unlock write access to ITM:
        self.write_debug32(0xE0000FB0, 0xC5ACCE55)

        # ITM Enable, sync enable, ATB=1:
        self.write_debug32(0xE0000E80, 0x00010005)

        # Enable all trace ports in ITM:
        self.write_debug32(0xE0000E00, 0xFFFFFFFF)

        # Set privilege mask for all 32 ports:
        self.write_debug32(0xE0000E40, 0x0000000F)

    def magicCommand40(self):
        """ Magic command detected with wireshark, no idea what it is!
            Apparently this enables tracing?
        """
        cmd = bytearray(16)
        cmd[0:7] = self.DEBUG_COMMAND, 0x40, 0x00, 0x10, 0x80, 0x84, 0x1e
        self.send_recv(cmd, 2)

    def writePort0(self, v32):
        self.write_debug32(0xE0000000, v32)

    def getTraceByteCount(self):
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, 0x42
        reply = self.send_recv(cmd, 2)
        data_size = struct.unpack('<H', reply[0:2])[0]
        self.logger.debug('{} pending trace data bytes'.format(data_size))
        return data_size

    def readTraceData(self):
        bsize = self.getTraceByteCount()
        if bsize > 0:
            td = self.recv_ep3(bsize)
            td = bytes(td)
            return td
        return bytes()

    # Helper 1 functions:
    def write_debug32(self, address, value):
        self.logger.debug('write 0x{:08X} to 0x{:08X}'.format(value, address))
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, JTAG_WRITEDEBUG_32BIT
        cmd[2:10] = struct.pack('<II', address, value)
        self.send_recv(cmd, 2)

    def read_debug32(self, address):
        self.logger.debug('read u32 from 0x{:08X}'.format(address))
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, JTAG_READDEBUG_32BIT
        cmd[2:6] = struct.pack('<I', address)  # pack into u32 little endian
        reply = self.send_recv(cmd, 8)
        return struct.unpack('<I', reply[4:8])[0]

    def write_reg(self, reg, value):
        """ Set a register to a value """
        assert self.Status == self.CORE_HALTED
        self.logger.debug('reg {} <- 0x{:08X}'.format(reg, value))
        cmd = bytearray(16)
        cmd[0:3] = self.DEBUG_COMMAND, self.DEBUG_WRITEREG, reg
        cmd[3:7] = struct.pack('<I', value)
        ret = self.send_recv(cmd, 2)
        print(ret)

    def read_reg(self, reg):
        """ Read a register value """
        assert self.Status == self.CORE_HALTED
        cmd = bytearray(16)
        cmd[0:3] = self.DEBUG_COMMAND, self.DEBUG_READREG, reg
        reply = self.send_recv(cmd, 4)
        val = struct.unpack('<I', reply)[0]
        self.logger.debug('Read reg {} ==> {:0X}'.format(reg, val))
        return val

    def read_all_regs(self):
        """ Read all register values """
        self.logger.debug('Reading all registers')
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_READALLREGS
        reply = self.send_recv(cmd, 84)
        fmt = '<' + 'I' * 21  # unpack 21 register values
        return list(struct.unpack(fmt, reply))

    def write_mem32(self, address, content):
        """ Write arbitrary memory address """
        assert len(content) % 4 == 0
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_WRITEMEM_32BIT
        cmd[2:8] = struct.pack('<IH', address, len(content))
        self.send_recv(cmd)
        self.send_recv(content)

    def read_mem32(self, address, length):
        self.logger.debug('read {} bytes at 0x{:08X}'.format(length, address))
        assert length % 4 == 0
        cmd = bytearray(16)
        cmd[0:2] = self.DEBUG_COMMAND, self.DEBUG_READMEM_32BIT
        cmd[2:8] = struct.pack('<IH', address, length)
        reply = self.send_recv(cmd, length)  # expect memory back!
        return reply

    # Helper 2 functions:
    def send_recv(self, tx, rxsize=0):
        """ Helper function that transmits and receives data in bulk mode. """
        tx = bytes(tx)
        # assert len(tx) == 16
        self._dev.write(0x2, tx)  # write to endpoint 2
        if rxsize > 0:
            return self._dev.read(0x81, rxsize)   # read from EP 1 | 0x80

    def recv_ep3(self, rxsize):
        return self._dev.read(0x83, rxsize)


if __name__ == '__main__':
   # Test program
   sl = STLink2()
   sl.open()
   sl.reset()
   print('version:', sl.Version)
   print('mode before doing anything:', sl.CurrentModeString)
   if sl.CurrentMode == DFU_MODE:
      sl.exitDfuMode()
   sl.enterSwdMode()
   print('mode after entering swd mode:', sl.CurrentModeString)

   i = sl.ChipId
   print('chip id: 0x{0:X}'.format(i))
   print('cpu: {0}'.format(sl.CpuId))

   print('status: {0}'.format(sl.StatusString))

   print('tracing')
   sl.traceEnable()
   sl.run()
   sl.writePort0(0x1337) # For test
   time.sleep(0.1)
   td = sl.readTraceData()
   print('trace data:', td)

   # Test CoreSight registers:
   idr4 = sl.read_debug32(0xE0041fd0)
   print('idr4 =', idr4)

   print('== ADI ==')
   a = adi.Adi(sl)
   a.parseRomTable(0xE00FF000) # why is rom table at 0xE00FF000?
   print('== ADI ==')

   # Detect ROM table:
   id4 = sl.read_debug32(0xE00FFFD0)
   id5 = sl.read_debug32(0xE00FFFD4)
   id6 = sl.read_debug32(0xE00FFFD8)
   id7 = sl.read_debug32(0xE00FFFDC)
   id0 = sl.read_debug32(0xE00FFFE0)
   id1 = sl.read_debug32(0xE00FFFE4)
   id2 = sl.read_debug32(0xE00FFFE8)
   id3 = sl.read_debug32(0xE00FFFEC)
   pIDs = [id0, id1, id2, id3, id4, id5, id6, id7]
   print(pIDs)

   print('reading from 0xE00FF000')
   scs = sl.read_debug32(0xE00FF000)
   print('scs {0:08X}'.format(scs))
   dwt = sl.read_debug32(0xE00FF004)
   print('dwt {0:08X}'.format(dwt))
   fpb = sl.read_debug32(0xE00FF008)
   print('fpb {0:08X}'.format(fpb))
   itm = sl.read_debug32(0xE00FF00C)
   print('itm {0:08X}'.format(itm))
   tpiu = sl.read_debug32(0xE00FF010)
   print('tpiu {0:08X}'.format(tpiu))
   etm = sl.read_debug32(0xE00FF014)
   print('etm {0:08X}'.format(etm))
   assert sl.read_debug32(0xE00FF018) == 0x0 # end marker

   devid = sl.read_debug32(0xE0040FC8)
   print('TPIU_DEVID: {0:X}'.format(devid))
   devtype = sl.read_debug32(0xE0040FCC)
   print('TPIU_TYPEID: {0:X}'.format(devtype))

   sl.close()
   print('Test succes!')

