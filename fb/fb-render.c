// fb-render.c — tsp-an4.4: render a known pattern to a VIRTUAL framebuffer with SDL3's
// SOFTWARE renderer, on a GPU-LESS host, and dump it for assertion. The headless analog of
// the supervisor->app fb-handoff: a memfd stands in for /dev/fb0 (the "fd" the supervisor
// hands the app), the app mmaps it and renders into it via a software renderer, and the
// supervisor reads it back.
//
// HONESTY (see README.md): this is upstream SDL3's portable SOFTWARE rasterizer. It proves
// layout/widget logic + the renderer-creation recipe ONLY. It is NOT the on-device
// libSDL3-pocketforge sunxifb backend, NOT the PowerVR/dc_sunxi->DE2.0->fb0 path, and the
// "present" rotation is applied as DATA (logical), NOT the per-SoC disp-engine silicon.
//
// tsp-osr: the open SDL3 RENDER segfault is a NULL renderer created on a window WITHOUT
// SDL_WINDOW_OPENGL. We avoid it two ways and PIN the safe recipe for C6/E6:
//   (1) the readback path uses SDL_CreateSoftwareRenderer(surface) — no window, no GL,
//       structurally cannot trip it;
//   (2) the window recipe (what an on-window app like E6 uses) forces the "software" render
//       driver via SDL_CreateRenderer(win, "software") so SDL never enters the GL path.
//
// Canvas size + rotation come from the descriptor (passed by the host wrapper from
// capabilities.toml screens[0]); NOTHING here is hardcoded to a device.
//
// Usage: fb-render --canvas WxH --rotation {none|cw90|cw180|cw270}
//                  --out canvas.ppm [--present-out present.ppm]
#include <SDL3/SDL.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/syscall.h>
#endif

static int make_fb(size_t bytes, void **mem) {
    int fd = -1;
#ifdef SYS_memfd_create
    fd = (int)syscall(SYS_memfd_create, "vfb", 0u);  // the "fb-handoff" fd analog
#endif
    if (fd >= 0) {
        if (ftruncate(fd, (off_t)bytes) != 0) { close(fd); fd = -1; }
    }
    if (fd >= 0) {
        *mem = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (*mem == MAP_FAILED) { close(fd); fd = -1; }
    }
    if (fd < 0) { *mem = calloc(1, bytes); return -1; }  // fallback: anonymous buffer
    return fd;
}

// XRGB8888: the 32-bit value is 0x00RRGGBB regardless of host endianness.
static void put_rgb(unsigned char *out, const unsigned char *fb, int w, int h) {
    const Uint32 *px = (const Uint32 *)fb;
    for (int i = 0; i < w * h; i++) {
        out[i * 3 + 0] = (px[i] >> 16) & 0xff;
        out[i * 3 + 1] = (px[i] >> 8) & 0xff;
        out[i * 3 + 2] = px[i] & 0xff;
    }
}

static int write_ppm(const char *path, const unsigned char *rgb, int w, int h) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror("fopen ppm"); return -1; }
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    fwrite(rgb, 1, (size_t)w * h * 3, f);
    fclose(f);
    return 0;
}

// Logical rotation of an RGB image (the disp-engine's job on-device — DATA here, not silicon).
static unsigned char *rotate(const unsigned char *in, int w, int h, const char *rot, int *ow, int *oh) {
    int cw90 = !strcmp(rot, "cw90"), cw180 = !strcmp(rot, "cw180"), cw270 = !strcmp(rot, "cw270");
    *ow = (cw90 || cw270) ? h : w;
    *oh = (cw90 || cw270) ? w : h;
    unsigned char *out = malloc((size_t)*ow * *oh * 3);
    for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
        int dx, dy;
        if (cw90)       { dx = h - 1 - y; dy = x; }
        else if (cw180) { dx = w - 1 - x; dy = h - 1 - y; }
        else if (cw270) { dx = y;         dy = w - 1 - x; }
        else            { dx = x;         dy = y; }
        memcpy(out + ((size_t)dy * *ow + dx) * 3, in + ((size_t)y * w + x) * 3, 3);
    }
    return out;
}

static void rect(SDL_Renderer *r, int x, int y, int w, int h, int cr, int cg, int cb) {
    SDL_SetRenderDrawColor(r, cr, cg, cb, 255);
    SDL_FRect fr = {(float)x, (float)y, (float)w, (float)h};
    SDL_RenderFillRect(r, &fr);
}

// PIN the tsp-osr-safe WINDOW recipe (the path E6/C6 use): a non-OPENGL window + the forced
// "software" renderer must succeed and not crash. Best-effort; the readback path is mandatory.
static void pin_tsp_osr_recipe(int w, int h) {
    SDL_SetHint(SDL_HINT_RENDER_DRIVER, "software");
    SDL_Window *win = SDL_CreateWindow("tsp-osr-pin", w, h, 0);  // NO SDL_WINDOW_OPENGL
    if (!win) { fprintf(stderr, "tsp-osr-pin: window create skipped (%s)\n", SDL_GetError()); return; }
    SDL_Renderer *r = SDL_CreateRenderer(win, "software");
    if (!r) { fprintf(stderr, "tsp-osr-pin: FAIL renderer NULL (%s)\n", SDL_GetError()); SDL_DestroyWindow(win); return; }
    const char *name = SDL_GetRendererName(r);
    fprintf(stderr, "tsp-osr-pin: OK window(no-GL)+SDL_CreateRenderer(\"software\") -> '%s'\n",
            name ? name : "?");
    SDL_DestroyRenderer(r);
    SDL_DestroyWindow(win);
}

int main(int argc, char **argv) {
    int W = 1280, H = 720;
    const char *rot = "none", *out = NULL, *pout = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--canvas") && i + 1 < argc) sscanf(argv[++i], "%dx%d", &W, &H);
        else if (!strcmp(argv[i], "--rotation") && i + 1 < argc) rot = argv[++i];
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "--present-out") && i + 1 < argc) pout = argv[++i];
    }
    if (!out) { fprintf(stderr, "need --out <ppm>\n"); return 2; }

    SDL_SetHint(SDL_HINT_VIDEO_DRIVER, "dummy");  // no real display, GPU-less
    if (!SDL_Init(SDL_INIT_VIDEO))
        fprintf(stderr, "warn: SDL_Init(VIDEO) failed (%s) — surface path still works\n", SDL_GetError());
    pin_tsp_osr_recipe(W, H);

    size_t bytes = (size_t)W * H * 4;
    void *fbmem = NULL;
    int fbfd = make_fb(bytes, &fbmem);
    fprintf(stderr, "virtual fb: %s %dx%d (%zu bytes)\n",
            fbfd >= 0 ? "memfd" : "anon-buffer", W, H, bytes);

    SDL_Surface *surf = SDL_CreateSurfaceFrom(W, H, SDL_PIXELFORMAT_XRGB8888, fbmem, W * 4);
    if (!surf) { fprintf(stderr, "FAIL CreateSurfaceFrom: %s\n", SDL_GetError()); return 3; }
    SDL_Renderer *r = SDL_CreateSoftwareRenderer(surf);   // tsp-osr-safe: no window, no GL
    if (!r) { fprintf(stderr, "FAIL CreateSoftwareRenderer: %s\n", SDL_GetError()); return 3; }

    // Deterministic test pattern (checkable regions): dark bg, 4 corner quadrant swatches
    // (TL red, TR green, BL blue, BR yellow), a white center box. Quadrant colors also let
    // the rotation be verified (which corner lands where).
    SDL_SetRenderDrawColor(r, 24, 24, 24, 255); SDL_RenderClear(r);
    int qw = W / 4, qh = H / 4;
    rect(r, 0,          0,          qw, qh, 220, 30,  30);   // TL red
    rect(r, W - qw,     0,          qw, qh, 30,  200, 30);   // TR green
    rect(r, 0,          H - qh,     qw, qh, 40,  60,  220);  // BL blue
    rect(r, W - qw,     H - qh,     qw, qh, 230, 210, 20);   // BR yellow
    rect(r, W / 2 - 40, H / 2 - 40, 80, 80, 240, 240, 240);  // center white
    SDL_RenderPresent(r);   // flush software renderer into the surface (== fbmem)

    unsigned char *rgb = malloc((size_t)W * H * 3);
    put_rgb(rgb, (unsigned char *)fbmem, W, H);
    if (write_ppm(out, rgb, W, H) != 0) return 4;
    fprintf(stderr, "wrote canvas %dx%d -> %s\n", W, H, out);

    if (pout) {
        int ow, oh;
        unsigned char *pres = rotate(rgb, W, H, rot, &ow, &oh);
        write_ppm(pout, pres, ow, oh);
        fprintf(stderr, "wrote present(rotation=%s) %dx%d -> %s\n", rot, ow, oh, pout);
        free(pres);
    }

    free(rgb);
    SDL_DestroyRenderer(r);
    SDL_DestroySurface(surf);
    if (fbfd >= 0) { munmap(fbmem, bytes); close(fbfd); } else free(fbmem);
    SDL_Quit();
    return 0;
}
