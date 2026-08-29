# Generic fb0 capture baseline

`a133/frame.png` and `a523/frame.png` are deterministic captures from protocol-free
`fb-pattern.c`. `check-generic.py` requires native x86 and arm64-under-qemu captures to
be byte-identical, compares the PNG with these committed files, and requires the existing
skin renderer to produce a non-empty bezel composite.
