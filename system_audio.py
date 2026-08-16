"""Capture whatever the Mac is playing and feed it to a mode as jack input.

macOS 14.2 added Core Audio process taps, so system output can be read without a
loopback driver like BlackHole and without touching the output device. The tap is
created private and unmuted: nothing else sees it in Audio MIDI Setup, and you still
hear the audio while a mode draws to it.

Everything here goes through ctypes against CoreAudio and the Objective-C runtime, so
the simulator keeps pygame as its only dependency.

The chain is tap -> private aggregate device -> IOProc. The IOProc lands on a realtime
thread, so it does one memcpy into a deque and nothing else; rate conversion happens on
the consumer side in `sample()`.
"""
import ctypes
import ctypes.util
import struct
from collections import deque

from simulator import SAMPLE_RATE

_objc = ctypes.CDLL(ctypes.util.find_library('objc'))
_ca = ctypes.CDLL(ctypes.util.find_library('CoreAudio'))


def _proto(fn, restype, *argtypes):
    """Give a C function its signature once, and hand it back to bind to a name."""
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


_proto(_objc.objc_getClass, ctypes.c_void_p, ctypes.c_char_p)
_proto(_objc.sel_registerName, ctypes.c_void_p, ctypes.c_char_p)


def _msg(restype, obj, sel, *args):
    """Send an Objective-C message. argtypes must be set per call site."""
    send = _objc.objc_msgSend
    send.restype = restype
    send.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [type(a) for a in args]
    return send(
        ctypes.c_void_p(obj), ctypes.c_void_p(_objc.sel_registerName(sel)), *args)


def _nsstr(s):
    return _msg(
        ctypes.c_void_p, _objc.objc_getClass(b'NSString'), b'stringWithUTF8String:',
        ctypes.c_char_p(s.encode()))


def _nsnum(i):
    return _msg(
        ctypes.c_void_p, _objc.objc_getClass(b'NSNumber'), b'numberWithInt:',
        ctypes.c_int32(i))


def _nsdict(pairs):
    d = _msg(
        ctypes.c_void_p, _objc.objc_getClass(b'NSMutableDictionary'), b'dictionary')
    for key, value in pairs:
        _msg(
            None, d, b'setObject:forKey:', ctypes.c_void_p(value),
            ctypes.c_void_p(_nsstr(key)))
    return d


def _nsarray(items):
    a = _msg(ctypes.c_void_p, _objc.objc_getClass(b'NSMutableArray'), b'array')
    for item in items:
        _msg(None, a, b'addObject:', ctypes.c_void_p(item))
    return a


def _fourcc(s):
    return struct.unpack('>I', s.encode())[0]


_SYSTEM_OBJECT = 1      # kAudioObjectSystemObject


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ('mSelector', ctypes.c_uint32),
        ('mScope', ctypes.c_uint32),
        ('mElement', ctypes.c_uint32),
    ]


class _ASBD(ctypes.Structure):
    """AudioStreamBasicDescription."""
    _fields_ = [
        ('mSampleRate', ctypes.c_double),
        ('mFormatID', ctypes.c_uint32),
        ('mFormatFlags', ctypes.c_uint32),
        ('mBytesPerPacket', ctypes.c_uint32),
        ('mFramesPerPacket', ctypes.c_uint32),
        ('mBytesPerFrame', ctypes.c_uint32),
        ('mChannelsPerFrame', ctypes.c_uint32),
        ('mBitsPerChannel', ctypes.c_uint32),
        ('mReserved', ctypes.c_uint32),
    ]


class _AudioBuffer(ctypes.Structure):
    _fields_ = [
        ('mNumberChannels', ctypes.c_uint32),
        ('mDataByteSize', ctypes.c_uint32),
        ('mData', ctypes.c_void_p),
    ]


class _AudioBufferList(ctypes.Structure):
    _fields_ = [
        ('mNumberBuffers', ctypes.c_uint32),
        ('mBuffers', _AudioBuffer * 8),
    ]


_IOPROC = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(_AudioBufferList),
    ctypes.c_void_p, ctypes.POINTER(_AudioBufferList), ctypes.c_void_p, ctypes.c_void_p)

# The CoreAudio calls, prototyped up front. Short aliases for the argument types keep
# the table readable — they stand in for the C spellings the framework headers use.
_i32, _u32, _vp = ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p
_ptr = ctypes.POINTER

_get_property = _proto(
    _ca.AudioObjectGetPropertyData, _i32,
    _u32, _ptr(_PropertyAddress), _u32, _vp, _ptr(_u32), _vp)
_tap_create = _proto(_ca.AudioHardwareCreateProcessTap, _i32, _vp, _ptr(_u32))
_tap_destroy = _proto(_ca.AudioHardwareDestroyProcessTap, _i32, _u32)
_aggregate_create = _proto(
    _ca.AudioHardwareCreateAggregateDevice, _i32, _vp, _ptr(_u32))
_aggregate_destroy = _proto(_ca.AudioHardwareDestroyAggregateDevice, _i32, _u32)
_ioproc_create = _proto(
    _ca.AudioDeviceCreateIOProcID, _i32, _u32, _IOPROC, _vp, _ptr(_vp))
_ioproc_destroy = _proto(_ca.AudioDeviceDestroyIOProcID, _i32, _u32, _vp)
_device_start = _proto(_ca.AudioDeviceStart, _i32, _u32, _vp)
_device_stop = _proto(_ca.AudioDeviceStop, _i32, _u32, _vp)

# roughly a second of 48 kHz stereo in ~512-frame blocks; older blocks are dropped
# rather than queued, so a stalled consumer hears "now" and not a lag
_MAX_BLOCKS = 96


class SystemAudioError(RuntimeError):
    pass


class SystemAudioSignal:
    """System output as an input signal, resampled to the device's rate.

    Presents the same `sample(n)` interface as SilentSignal and SynthSignal. Floats
    arrive at the tap's own rate — 48 kHz on most Macs — and are resampled to the 32 kHz
    the device runs at, so the 16-sample averaging downstream lowpasses them the way the
    hardware does.

    `n` is ignored. The pull API asks for an absolute sample index, but a live source
    has only what has arrived, so samples come out in the order they were captured.
    Underrun returns the noise floor rather than zeroes, which keeps the OSD's VU from
    looking dead between blocks.
    """

    def __init__(self, exclude_pids=()):
        self.tap_id = ctypes.c_uint32(0)
        self.device_id = ctypes.c_uint32(0)
        self.proc_id = ctypes.c_void_p()
        self._blocks = deque(maxlen=_MAX_BLOCKS)
        self._current = ()      # the block being drained
        self._pos = 0.0         # fractional read position within _current
        self._last = (0.0, 0.0)
        self._started = False

        tap_uid = self._create_tap(exclude_pids)
        self._create_aggregate(tap_uid)
        self.src_rate, self.channels = self._read_tap_format()
        self._ratio = self.src_rate / SAMPLE_RATE
        self._start()

        self.label = f'system audio ({self.src_rate / 1000:.1f} kHz, {self.channels}ch)'

    # -- setup ----------------------------------------------------------
    @staticmethod
    def _process_object(pid):
        """Translate a Unix pid to the AudioObjectID a tap description wants.

        CATapDescription takes Core Audio process objects, not pids; handing it a raw
        pid returns kAudioHardwareBadObjectError. A process that has never touched Core
        Audio has no object at all, which is not an error here.
        """
        addr = _PropertyAddress(_fourcc('id2p'), _fourcc('glob'), 0)
        wanted = ctypes.c_int32(int(pid))
        obj = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(obj))
        status = _get_property(
            _SYSTEM_OBJECT, ctypes.byref(addr), ctypes.sizeof(wanted),
            ctypes.byref(wanted), ctypes.byref(size), ctypes.byref(obj))
        return obj.value if status == 0 and obj.value else None

    def _create_tap(self, exclude_pids):
        cls = _objc.objc_getClass(b'CATapDescription')
        if not cls:
            raise SystemAudioError(
                'CATapDescription is unavailable; system capture needs '
                'macOS 14.2 or newer')

        objects = [self._process_object(p) for p in exclude_pids]
        excluded = _nsarray([_nsnum(o) for o in objects if o is not None])
        desc = _msg(
            ctypes.c_void_p, _msg(ctypes.c_void_p, cls, b'alloc'),
            b'initStereoGlobalTapButExcludeProcesses:', ctypes.c_void_p(excluded))
        if not desc:
            raise SystemAudioError('could not build a tap description')

        # private keeps it out of other apps' device lists; mute behavior 0 leaves
        # playback audible, so the tap is passive
        _msg(None, desc, b'setPrivate:', ctypes.c_bool(True))
        _msg(None, desc, b'setMuteBehavior:', ctypes.c_int32(0))
        _msg(None, desc, b'setName:', ctypes.c_void_p(_nsstr('EYESY Simulator')))
        self._desc = desc       # hold a reference; ObjC would free it otherwise

        uuid = _msg(
            ctypes.c_void_p, _msg(ctypes.c_void_p, desc, b'UUID'), b'UUIDString')
        tap_uid = ctypes.string_at(_msg(ctypes.c_void_p, uuid, b'UTF8String')).decode()

        status = _tap_create(ctypes.c_void_p(desc), ctypes.byref(self.tap_id))
        if status != 0 or not self.tap_id.value:
            raise SystemAudioError(
                f'AudioHardwareCreateProcessTap failed ({status}); grant the terminal '
                'audio recording access in System Settings > Privacy & Security')
        return tap_uid

    def _create_aggregate(self, tap_uid):
        description = _nsdict([
            ('uid',          _nsstr('com.chrisdegiere.eyesy-simulator.tap')),
            ('name',         _nsstr('EYESY Simulator Capture')),
            ('private',      _nsnum(1)),
            ('stacked',      _nsnum(0)),
            ('tapautostart', _nsnum(1)),
            ('taps',         _nsarray([_nsdict([('uid', _nsstr(tap_uid))])])),
            ('subdevices',   _nsarray([])),
        ])
        status = _aggregate_create(
            ctypes.c_void_p(description), ctypes.byref(self.device_id))
        if status != 0 or not self.device_id.value:
            self.close()
            raise SystemAudioError(f'could not create the capture device ({status})')

    def _read_tap_format(self):
        addr = _PropertyAddress(_fourcc('tfmt'), _fourcc('glob'), 0)
        asbd = _ASBD()
        size = ctypes.c_uint32(ctypes.sizeof(_ASBD))
        status = _get_property(
            self.tap_id, ctypes.byref(addr), 0, None,
            ctypes.byref(size), ctypes.byref(asbd))
        if status != 0 or not asbd.mSampleRate:
            return 48000.0, 2       # the near-universal default if the query fails
        return asbd.mSampleRate, max(1, asbd.mChannelsPerFrame)

    def _start(self):
        blocks = self._blocks

        def io_proc(device, now, in_data, in_time, out_data, out_time, client):
            # realtime thread: one copy, no arithmetic
            try:
                buffer_list = in_data[0]
                if buffer_list.mNumberBuffers:
                    buf = buffer_list.mBuffers[0]
                    if buf.mData and buf.mDataByteSize:
                        blocks.append(ctypes.string_at(buf.mData, buf.mDataByteSize))
            except Exception:
                pass        # never raise back into CoreAudio
            return 0

        self._callback = _IOPROC(io_proc)       # hold a reference or it is collected
        status = _ioproc_create(
            self.device_id, self._callback, None, ctypes.byref(self.proc_id))
        if status != 0:
            self.close()
            raise SystemAudioError(f'could not attach to the capture device ({status})')

        status = _device_start(self.device_id, self.proc_id)
        if status != 0:
            self.close()
            raise SystemAudioError(f'could not start capture ({status})')
        self._started = True

    # -- the signal interface -------------------------------------------
    def sample(self, n):
        """Return one stereo frame scaled to the int16 range modes expect."""
        frames = self._current
        if self._pos >= len(frames):
            if not self._blocks:
                return self._last       # underrun: hold the last frame
            self._current = frames = self._unpack(self._blocks.popleft())
            self._pos = 0.0
            if not frames:
                return self._last

        i = int(self._pos)
        self._pos += self._ratio
        self._last = frames[i]
        return self._last

    def _unpack(self, raw):
        """Interleaved Float32 bytes to a list of (left, right) int16-scaled pairs."""
        count = len(raw) // 4
        floats = struct.unpack(f'<{count}f', raw[:count * 4])
        step = self.channels
        if step >= 2:
            return [
                (floats[i] * 32767.0, floats[i + 1] * 32767.0)
                for i in range(0, count - step + 1, step)]
        return [(v * 32767.0, v * 32767.0) for v in floats]

    # -- teardown -------------------------------------------------------
    def close(self):
        if self.device_id.value and self.proc_id:
            if self._started:
                _device_stop(self.device_id, self.proc_id)
                self._started = False
            _ioproc_destroy(self.device_id, self.proc_id)
            self.proc_id = ctypes.c_void_p()

        if self.device_id.value:
            _aggregate_destroy(self.device_id)
            self.device_id = ctypes.c_uint32(0)

        if self.tap_id.value:
            _tap_destroy(self.tap_id)
            self.tap_id = ctypes.c_uint32(0)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
