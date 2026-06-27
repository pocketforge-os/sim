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

tsp-an4.7 grows this stub into the full sensor/pose path: ``set_pose`` now drives a SINGLE
physical model (``sensor/physical_model.PhysicalModel``) whose derived accel/gyro the control
surface writes into a synthesized virtual IIO device the app reads; and a unified rumble/haptics
NO-OP SHAPE makes "absent motor" (a133) and "accessibility hapticsEnabled off" (a523, E4) the
SAME typed no-op — a handle whose ``pulse()`` returns a status and NEVER crashes.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sensor"))
from physical_model import PhysicalModel   # noqa: E402  (the .7 single physical model)


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


# Unified no-op SHAPE (epic acceptance + E4 unification): an actuator call ALWAYS returns a typed
# status and NEVER raises. "no motor on this descriptor" (a133) and "user disabled hapticsEnabled"
# (a523, E4 accessibility) collapse into the SAME no-op — only the reason differs.
RUMBLE_FIRED = "fired"               # motor present AND preference enabled -> would actuate
RUMBLE_NOOP_ABSENT = "noop-absent"   # descriptor advertises no rumble motor (a133)
RUMBLE_NOOP_SUPPRESSED = "noop-suppressed"  # motor present but hapticsEnabled == False (E4)


class RumbleHandle:
    """A cosmetic-no-op-tier handle (briefing §A four-way taxonomy). ``pulse`` succeeds and returns
    a status whether or not anything actually buzzes — the app does not special-case absence."""

    def __init__(self, present, haptics_enabled):
        self.present = present
        self.haptics_enabled = haptics_enabled

    def pulse(self, ms=40):
        if not self.present:
            return RUMBLE_NOOP_ABSENT
        if not self.haptics_enabled:
            return RUMBLE_NOOP_SUPPRESSED
        return RUMBLE_FIRED          # honesty: SIM does not actuate silicon; real buzz is hw-gated


class BrokerStub:
    def __init__(self, desc):
        self.desc = desc
        self.model = PhysicalModel()      # the ONE rigid-body state behind the IMU
        self._has_pose = False
        self._caps = {}                   # name -> last set value (cooperative state)
        # accessibility preferences the broker reads + enforces at the primitive (E4). Default on.
        self.prefs = {"hapticsEnabled": True}

    # --- presence (descriptor-derived) ---
    def is_present(self, name):
        pred = _CAP_PRESENCE.get(name.lower())
        return bool(pred(self.desc)) if pred else False

    def is_granted(self, name):
        """Present hardware AND not policy-denied (cooperative facade)."""
        return self.is_present(name) and name.lower() not in _DEFAULT_DENY

    # --- accessibility / user preferences (E4) ---
    def set_preference(self, name, value):
        self.prefs[name] = value
        return value

    def get_preference(self, name, default=None):
        return self.prefs.get(name, default)

    # --- sensors / pose (AVD setPhysicalModel style, single physical model) ---
    # Orientation in DEGREES, angular velocity in deg/s (human/UI units); the model holds radians.
    def set_pose(self, yaw=None, pitch=None, roll=None, x=None, y=None, z=None,
                 wx=None, wy=None, wz=None):
        if not self.is_present("imu"):
            raise HardwareAbsent("imu", "descriptor advertises no accel/gyro sensor")
        d2r = math.radians
        self.model.set(
            yaw=None if yaw is None else d2r(yaw),
            pitch=None if pitch is None else d2r(pitch),
            roll=None if roll is None else d2r(roll),
            x=x, y=y, z=z,
            wx=None if wx is None else d2r(wx),
            wy=None if wy is None else d2r(wy),
            wz=None if wz is None else d2r(wz),
        )
        self._has_pose = True
        return self.get_pose()

    def get_pose(self):
        """Pose in the human/UI units set_pose accepts (degrees, deg/s) — the model is radians."""
        if not self.is_present("imu"):
            raise HardwareAbsent("imu")
        s = self.model.state()
        r2d = math.degrees
        return {"yaw": r2d(s["yaw"]), "pitch": r2d(s["pitch"]), "roll": r2d(s["roll"]),
                "x": s["x"], "y": s["y"], "z": s["z"],
                "wx": r2d(s["wx"]), "wy": r2d(s["wy"]), "wz": r2d(s["wz"])}

    # --- actuators: rumble/haptics (unified no-op shape; E4 preference-gated) ---
    def acquire_rumble(self):
        """Always returns a handle (cosmetic-no-op tier). Present iff the descriptor has a rumble
        actuator; gated by the hapticsEnabled accessibility preference. NEVER raises."""
        return RumbleHandle(self.is_present("rumble"), bool(self.prefs.get("hapticsEnabled", True)))

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
