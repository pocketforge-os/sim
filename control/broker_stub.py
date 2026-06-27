#!/usr/bin/env python3
"""broker_stub.py — tsp-an4.5: a THIN in-process stand-in for the E2 capability broker.

The control surface routes ``set_pose`` / ``set_capability`` / capability queries through a
broker, NOT raw evdev (briefing §C.3). The real broker is E2 / C7 (an out-of-process facade);
for .5 a cooperative in-process stub is enough to make the capability/permission CONTRACT
assertable off-hardware (the kickoff explicitly allows a thin stub here). The control surface
keeps the SAME call shape, so swapping in the real broker later is a constructor change.

HONESTY (epic contract item 4): this proves the capability/permission *contract + ergonomics*
and the descriptor-honest MISSING-HARDWARE degradation — NOT enforcement. Under qemu-user guest
seccomp is unenforceable and the v0 facade is cooperative; a hostile app is NOT confined here.
That stays the hardware/substrate gate's authority.

Capability presence is DERIVED FROM THE DESCRIPTOR (zero per-device code): a sensor/actuator row
present => the capability exists; omitted => it is hardware-absent. So a133 (no [[sensors]]) and
a523 (qmi8658 imu) differ only by descriptor data.
"""


class HardwareAbsent(Exception):
    """Typed graceful-degradation signal: the descriptor does not advertise this hardware, so
    the operation is a descriptor-honest no-op — NEVER a crash (epic acceptance)."""

    def __init__(self, name, detail=""):
        self.name = name
        self.detail = detail
        super().__init__(f"hardware-absent: '{name}'" + (f" ({detail})" if detail else ""))


class PermissionDenied(Exception):
    """The cooperative permission contract denied this capability (v0 facade; not enforced)."""

    def __init__(self, name):
        self.name = name
        super().__init__(f"permission-denied: '{name}'")


# capability name -> the descriptor evidence that makes it PRESENT. Each predicate inspects the
# descriptor's sensor/actuator rows; nothing is per-device coded.
def _has_sensor(desc, *kinds):
    for s in desc.get("sensors", []):
        k = (s.get("kind") or "").lower()
        if any(want in k for want in kinds):
            return True
    return False


def _has_actuator(desc, *kinds):
    for a in desc.get("actuators", []):
        k = (a.get("kind") or "").lower()
        if any(want in k for want in kinds):
            return True
    return False


_CAP_PRESENCE = {
    "imu":           lambda d: _has_sensor(d, "accel", "gyro"),
    "accelerometer": lambda d: _has_sensor(d, "accel"),
    "gyroscope":     lambda d: _has_sensor(d, "gyro"),
    "magnetometer":  lambda d: _has_sensor(d, "mag"),
    "location":      lambda d: _has_sensor(d, "gnss", "gps"),
    "gnss":          lambda d: _has_sensor(d, "gnss", "gps"),
    "rumble":        lambda d: _has_actuator(d, "rumble"),
    "leds":          lambda d: _has_actuator(d, "led"),
}

# Capabilities the cooperative v0 facade refuses by policy even where hardware exists (the
# permission-model contract the headless test asserts). Privacy-sensitive caps default-deny.
_DEFAULT_DENY = {"location", "gnss"}


class BrokerStub:
    def __init__(self, desc):
        self.desc = desc
        self._pose = None
        self._caps = {}   # name -> last set value (cooperative state)

    # --- presence (descriptor-derived) ---
    def is_present(self, name):
        pred = _CAP_PRESENCE.get(name.lower())
        return bool(pred(self.desc)) if pred else False

    def is_granted(self, name):
        """Present hardware AND not policy-denied (cooperative facade)."""
        return self.is_present(name) and name.lower() not in _DEFAULT_DENY

    # --- sensors / pose (AVD setPhysicalModel style, single physical model) ---
    def set_pose(self, yaw=0.0, pitch=0.0, roll=0.0, x=0.0, y=0.0, z=0.0):
        if not self.is_present("imu"):
            raise HardwareAbsent("imu", "descriptor advertises no accel/gyro sensor")
        self._pose = {"yaw": yaw, "pitch": pitch, "roll": roll, "x": x, "y": y, "z": z}
        return self._pose

    def get_pose(self):
        if not self.is_present("imu"):
            raise HardwareAbsent("imu")
        return self._pose

    # --- generic capability set/get (cooperative) ---
    def set_capability(self, name, value):
        if not self.is_present(name):
            raise HardwareAbsent(name)
        if name.lower() in _DEFAULT_DENY:
            raise PermissionDenied(name)
        self._caps[name] = value
        return value

    def get_capability(self, name):
        if not self.is_present(name):
            raise HardwareAbsent(name)
        if name.lower() in _DEFAULT_DENY:
            raise PermissionDenied(name)
        return self._caps.get(name)

    # --- assertions the headless test uses ---
    def assert_capability_absent(self, name):
        if self.is_present(name):
            raise AssertionError(f"capability '{name}' IS present on this descriptor "
                                 f"(expected hardware-absent)")
        return True

    def assert_capability_present(self, name):
        if not self.is_present(name):
            raise AssertionError(f"capability '{name}' is hardware-absent (expected present)")
        return True

    def assert_capability_denied(self, name):
        """Passes when the capability is NOT granted — either hardware-absent OR policy-denied
        by the cooperative facade. The permission-model contract (honesty item 4: contract, not
        enforcement)."""
        if self.is_granted(name):
            raise AssertionError(f"capability '{name}' IS granted (expected denied)")
        return True
