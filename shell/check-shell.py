#!/usr/bin/env python3
"""F12 cooperative qemu transcript suite. All lifecycle observations are MODELED."""
import argparse, hashlib, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HONESTY = ("MODELED ONLY: cooperative qemu-tsp transcripts do not prove real systemd/unit/"
           "foreground-target lifecycle, enforced termination, authority survivability, isolation, "
           "GPU/fbdev-ioctl/KMS, or physical timing. F13 owns real packaged lifecycle/enforcement "
           "proof; F14 owns sanctioned device acceptance and measurements.")

def parse(text):
    rows=[]
    for raw in text.splitlines():
        fields=dict(field.split("=",1) for field in raw.split() if "=" in field)
        if {"case","kind","value"} <= fields.keys(): rows.append(fields)
    return rows

def check_case(case, rows):
    kinds=[r["kind"] for r in rows]
    values=[r["value"] for r in rows]
    if case == "recovery":
        return "RecoveryRequired" in values and not any(r["kind"]=="frame" and r["value"]=="shell" for r in rows)
    expected={"graceful":"Returned", "forced":"ForcedClose", "crash":"Crash"}[case]
    receipt=next((i for i,r in enumerate(rows) if r["kind"]=="receipt" and expected in r["value"]),-1)
    ack=next((i for i,r in enumerate(rows) if r["kind"]=="ack"),-1)
    shell=next((i for i,r in enumerate(rows) if r["kind"]=="frame" and r["value"]=="shell"),-1)
    protected=[i for i,r in enumerate(rows) if r["kind"]=="protected-intake"]
    no_shell_during=not protected or all(not (r["kind"]=="frame" and r["value"]=="shell") for r in rows[:ack])
    return 0 <= ack < receipt < shell and no_shell_during

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--authority", required=True); ap.add_argument("--launcher", required=True)
    ap.add_argument("--qemu-tsp", required=True); ap.add_argument("--rootfs", required=True)
    ap.add_argument("--harness", required=True); ap.add_argument("--platform", required=True)
    ap.add_argument("--skin-render", required=True)
    a=ap.parse_args(); failed=False; transcripts=[]
    for case in ("graceful","forced","crash","recovery"):
        env=os.environ.copy(); env.update(QEMU_TSP=a.qemu_tsp, ROOTFS=a.rootfs)
        run=subprocess.run([a.harness,a.authority,case],env=env,text=True,capture_output=True)
        rows=parse(run.stdout); ok=run.returncode==0 and check_case(case,rows)
        print(f"{'PASS' if ok else 'FAIL'} modeled-{case}-ordering")
        failed |= not ok; transcripts.extend(run.stdout.splitlines())
    # The real launcher is run twice through the same qemu harness and generic fb0 attachment.
    # Its fixture embeds the shipped east-A/south-B and unlabeled guide contract.
    from pathlib import Path
    descriptor=(Path(a.platform)/"devices"/"a133"/"capabilities.toml").read_text()
    glyph_ok=(all(token in descriptor for token in ('id = "east"','label = "A"',
              'id = "south"','label = "B"','id = "guide"'))
              and 'id = "guide"\nkind = "button"\nev_type = "EV_KEY"' in descriptor)
    print(f"{'PASS' if glyph_ok else 'FAIL'} tsp-east-A-south-B-unlabeled-guide-prompts")
    failed |= not glyph_ok
    with tempfile.TemporaryDirectory(prefix="shell-suite-") as work:
        hashes=[]
        for n in (1,2):
            frame=os.path.join(work,f"frame-{n}.bin")
            with open(frame,"wb") as f: f.truncate(1280*720*4)
            env=os.environ.copy(); env.update(QEMU_TSP=a.qemu_tsp,ROOTFS=a.rootfs,FB0_BIND=frame,
                PF_FB_WIDTH="1280",PF_FB_HEIGHT="720",PF_FB_STRIDE=str(1280*4))
            run=subprocess.run([a.harness,a.launcher,"--sim-frame"],env=env,capture_output=True)
            if run.returncode: failed=True
            hashes.append(hashlib.sha256(open(frame,"rb").read()).hexdigest())
        ok=hashes[0]==hashes[1]
        print(f"{'PASS' if ok else 'FAIL'} deterministic-frame-hash sha256={hashes[0]}")
        failed |= not ok
        shot=os.path.join(work,"shell-skin.ppm")
        capture=os.path.join(HERE,"..","generic","generic_capture.py")
        composed=subprocess.run([sys.executable,capture,"--device","a133","--platform",a.platform,
            "--launcher","qemu","--app",a.launcher,"--qemu-tsp",a.qemu_tsp,"--rootfs",a.rootfs,
            "--harness",a.harness,"--skin-render",a.skin_render,"--frame",os.path.join(work,"shell.ppm"),
            "--shot",shot,"--","--sim-frame"],capture_output=True)
        ok=composed.returncode==0 and os.path.getsize(shot)>100
        print(f"{'PASS' if ok else 'FAIL'} s1-single-display-skin-composition")
        failed |= not ok
    transcript="\n".join(transcripts)+"\n"
    expected=open(os.path.join(HERE,"fixtures","modeled-transcript.txt"),encoding="utf-8").read()
    ok=transcript==expected
    digest=hashlib.sha256(transcript.encode()).hexdigest()
    print(f"{'PASS' if ok else 'FAIL'} deterministic-semantic-transcript sha256={digest}")
    failed |= not ok
    print("HONESTY: "+HONESTY)
    return int(failed)
if __name__ == "__main__": sys.exit(main())
