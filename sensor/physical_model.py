#!/usr/bin/env python3
"""physical_model.py — tsp-an4.7: the SINGLE physical model behind the IMU (AVD `setPhysicalModel`).

ONE rigid-body state — orientation (yaw/pitch/roll) + position (x/y/z) + angular-velocity
(wx/wy/wz) — drives BOTH derived sensor channels:

  * accelerometer (m/s^2) = the specific force a real accel reads at rest: the gravity reaction
    vector projected into the device frame by the orientation. Flat face-up -> (0,0,+g) (the AVD
    convention). Linear acceleration (position 2nd derivative) is NOT modeled in v0 -> 0; noted.
  * gyroscope (rad/s) = the body-frame angular velocity (wx,wy,wz). At rest -> 0 (an honest
    static gyro reads ~0, not a fabricated echo of the accel).

This is host-side and feeds BOTH clients of the control surface (the GUI tilt-bubble drag and the
headless test both call ``set_pose`` -> here), so the app reads the SAME injected values whichever
client drove them (the "one model, two clients" invariant the wall proves).

HONESTY: this is a deterministic geometric MODEL of the pose->sensor mapping, exercising the
units pipeline + the descriptor mount_matrix transform. It is NOT real qmi8658 silicon, real
calibration, noise, bias, or sample timing — those stay the flash->serial hardware gate's
authority (epic HONESTY CONTRACT). No wall-clock is read (reproducible + byte-identical parity).

Conventions (documented so the round-trip is unambiguous):
  device frame  X = right, Y = up (toward screen top), Z = out of the screen toward the viewer.
  pitch  rotation about X (tilt top away/toward you); roll about Y (tilt left/right);
  yaw    about Z (heading) — does NOT change the gravity reading (rotation about the vertical).
  g      = 9.80665 m/s^2.
  accel(device) = ( -g*sin(roll),  g*cos(roll)*sin(pitch),  g*cos(roll)*cos(pitch) )
All angles are RADIANS.
"""
import math

G = 9.80665  # standard gravity, m/s^2


def _matvec(m, v):
    """3x3 (row-major list of lists) times a 3-vector. Plain mul/add, no FMA contraction (the C
    consumer compiles -ffp-contract=off to match) so host-expected == app-reported bit-for-bit."""
    return [m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] for r in range(3)]


def _transpose(m):
    return [[m[c][r] for c in range(3)] for r in range(3)]


class PhysicalModel:
    """The one rigid-body state. set_* mutate it; accel()/gyro() derive the sensor channels in the
    DEVICE frame. The IIO synth applies the (inverse) mount_matrix to get the CHIP frame it writes."""

    def __init__(self):
        self.yaw = self.pitch = self.roll = 0.0      # radians
        self.x = self.y = self.z = 0.0               # metres (position; informational in v0)
        self.wx = self.wy = self.wz = 0.0            # rad/s (body angular velocity)

    def set(self, yaw=None, pitch=None, roll=None, x=None, y=None, z=None,
            wx=None, wy=None, wz=None):
        for k, val in (("yaw", yaw), ("pitch", pitch), ("roll", roll),
                       ("x", x), ("y", y), ("z", z),
                       ("wx", wx), ("wy", wy), ("wz", wz)):
            if val is not None:
                setattr(self, k, float(val))
        return self.state()

    def state(self):
        return {"yaw": self.yaw, "pitch": self.pitch, "roll": self.roll,
                "x": self.x, "y": self.y, "z": self.z,
                "wx": self.wx, "wy": self.wy, "wz": self.wz}

    # --- derived sensor channels, DEVICE frame ---
    def accel(self):
        """Gravity reaction in the device frame (m/s^2). Yaw drops out (rotation about vertical)."""
        cr, sr = math.cos(self.roll), math.sin(self.roll)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return [-G * sr, G * cr * sp, G * cr * cp]

    def gyro(self):
        """Body angular velocity in the device frame (rad/s)."""
        return [self.wx, self.wy, self.wz]


# ---- GUI tilt-bubble gesture -> pose (the .6-style client mapping; one model, two clients) ----
# A drag on the imu's ui="tilt_bubble" widget maps a normalized displacement (dx,dy in -1..1 from
# the bubble centre) to a (pitch, roll) tilt. The GUI calls this; the headless test calls set_pose
# with the SAME numbers -> identical broker state -> the app reads identical values. Proving THAT
# (without rendering pixels) is the device-free invariant; rendering the widget is the separate,
# owner-VISUAL-gated piece (deferred while the owner is away). Returns DEGREES (the units
# BrokerStub.set_pose accepts), so the GUI client and the test client are literally interchangeable.
TILT_BUBBLE_MAX_DEG = 45.0   # full-deflection drag == 45 deg tilt


def pose_from_drag(dx, dy, max_tilt_deg=TILT_BUBBLE_MAX_DEG):
    """Map a tilt-bubble drag (dx,dy in [-1,1]) -> {pitch, roll} in DEGREES. dy(up) -> pitch
    forward, dx(right) -> roll right. The single source the GUI widget and any test share."""
    dx = max(-1.0, min(1.0, dx))
    dy = max(-1.0, min(1.0, dy))
    return {"pitch": dy * max_tilt_deg, "roll": dx * max_tilt_deg}


def apply_mount(m, vec):
    """device = M . chip (M = descriptor mount_matrix). Used by the host to PREDICT what the app
    will report after it applies M to the chip-frame raws it reads from the IIO node."""
    return _matvec(m, vec)


def inverse_mount(m, vec):
    """chip = M^T . device for an orthonormal axis-alignment M (mount matrices are ±1/0 axis
    permutations). The IIO synth writes chip-frame raws; the app re-applies M to recover device."""
    return _matvec(_transpose(m), vec)
