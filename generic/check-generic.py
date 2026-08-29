#!/usr/bin/env python3
"""End-to-end generic fb0 capture: native == qemu == committed baseline, plus skin shot."""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "generic_capture.py")
sys.path.insert(0, os.path.join(HERE, "..", "fb"))
from ppm2png import read_ppm, write_png

def sha(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("devices", nargs="*", default=["a133", "a523"])
    a = ap.parse_args(); failed = False
    required = {k: os.environ.get(k) for k in
                ("PLATFORM","PATTERN_X86","PATTERN_ARM64","QEMU_TSP","ROOTFS","SKIN_RENDER")}
    missing = [k for k,v in required.items() if not v]
    if missing: sys.exit("missing environment: " + ", ".join(missing))
    harness = os.path.join(HERE, "..", "harness", "run-in-harness.sh")
    with tempfile.TemporaryDirectory(prefix="check-generic-") as work:
        for dev in a.devices:
            print(f"generic {dev}:")
            native=os.path.join(work,f"{dev}.native.ppm"); arm=os.path.join(work,f"{dev}.arm.ppm")
            arm_args=os.path.join(work,f"{dev}.arm-args.ppm")
            shot=os.path.join(work,f"{dev}.shot.ppm"); png=os.path.join(work,f"{dev}.png")
            common=[sys.executable,CAPTURE,"--device",dev,"--platform",required["PLATFORM"]]
            subprocess.run(common+["--launcher","native","--app",required["PATTERN_X86"],"--frame",native],check=True)
            subprocess.run(common+["--launcher","qemu","--app",required["PATTERN_ARM64"],
                "--qemu-tsp",required["QEMU_TSP"],"--rootfs",required["ROOTFS"],"--harness",harness,
                "--frame",arm,"--shot",shot,"--skin-render",required["SKIN_RENDER"]],check=True)
            subprocess.run(common+["--launcher","qemu","--app",required["PATTERN_ARM64"],
                "--qemu-tsp",required["QEMU_TSP"],"--rootfs",required["ROOTFS"],"--harness",harness,
                "--frame",arm_args,
                "--","--app-option","value"],check=True)
            pw, ph, rgb = read_ppm(arm); write_png(png, pw, ph, rgb)
            baseline=os.path.join(HERE,"baseline",dev,"frame.png")
            checks=[("native == qemu",sha(native)==sha(arm)),
                    ("guest receives app options without -- separator",sha(arm_args)==sha(arm)),
                    ("qemu == committed baseline",os.path.isfile(baseline) and sha(png)==sha(baseline)),
                    ("skin composite shot produced",os.path.getsize(shot)>100)]
            for label,ok in checks:
                print(("  PASS " if ok else "  FAIL ")+label)
                failed |= not ok
    return int(failed)
if __name__ == "__main__": sys.exit(main())
