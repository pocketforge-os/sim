// hwprobe-lite.c — tsp-an4.5: the IDENTICAL arm64 OCI app the control surface drives.
//
// This is the tiny app that makes the headline contract REAL: it BOTH reads the descriptor-
// synthesized uinput device (the .3 device, bound in at /dev/input) AND lights the pressed
// control onto a virtual framebuffer (the .4 software-render path). The host injects via the
// control surface (writes the uinput device), tells this app to snapshot, and asserts the
// region colour — so "press(south) lights btn_south" is proven end-to-end THROUGH real
// uinput->evdev->qemu-tsp ioctl translation, not faked by the host.
//
// It is descriptor-driven with ZERO per-device code: the host writes a layout.txt (computed by
// layout.py from capabilities.toml) listing the evdev nodes + each control's canvas rect +
// codes; this app just draws what the descriptor says and lights whatever the kernel reports.
// a133 vs a523 differ only by that generated layout.
//
// Coordination is a FIFO request/response handshake (no cross-pid-ns signals): the host writes
// "snap <ppm-path>" -> this app drains pending evdev events, updates control state, software-
// renders the canvas, writes the PPM, replies "ok". "quit" -> "bye" + exit. Injection always
// happens-before the snap, and uinput->evdev delivery is synchronous, so the drain is race-free.
//
// HONESTY (see ../docs/HONESTY-CONTRACT.md): upstream SDL3 SOFTWARE rasterizer to a memfd fb,
// tsp-osr-safe recipe (SDL_CreateSoftwareRenderer on a surface — no window/GL). Proves the
// input->render BINDING + widget logic ONLY; NOT the on-device PowerVR/sunxifb/dc_sunxi path.
//
// Usage: hwprobe-lite <io-dir>   (reads <io-dir>/layout.txt, <io-dir>/req, <io-dir>/resp)
#include <SDL3/SDL.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/mman.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/syscall.h>
#endif

#define EV_SYN 0
#define EV_KEY 1
#define EV_ABS 3

// kernel input_event on 64-bit: struct timeval (2x long) + u16 type + u16 code + s32 value.
// The synth packs exactly this ("<llHHi" = 24 bytes); define locally to avoid header deps.
struct in_ev { long sec, usec; unsigned short type, code; int value; };

#define MAX_CODES 4
#define MAX_CTL   40
#define MAX_NODES 4

// widget kind (from layout.txt; layout.py derives it from the descriptor — zero per-device code)
enum { KIND_BUTTON = 0, KIND_TRIGGER, KIND_HAT, KIND_STICK };

// role tags each code so we can draw DIRECTION without hand-typing ABI codes here:
// 'k' digital press, 't' trigger axis, 'x'/'y' the two axes of a stick or hat.
struct code { int type, code, vmin, vmax, value; char role; };
struct ctl {
    char skin[40];
    int kind;               // KIND_BUTTON | KIND_TRIGGER | KIND_HAT | KIND_STICK
    int x, y, w, h;
    int ncodes;
    struct code codes[MAX_CODES];
};

static struct ctl ctls[MAX_CTL];
static int n_ctl = 0;
static int node_fd[MAX_NODES];
static int n_node = 0;
static int CANVAS_W = 1280, CANVAS_H = 720;

// ---- virtual fb (memfd, the fb-handoff fd analog), reused from fb-render.c ----
static int make_fb(size_t bytes, void **mem) {
    int fd = -1;
#ifdef SYS_memfd_create
    fd = (int)syscall(SYS_memfd_create, "vfb", 0u);
#endif
    if (fd >= 0 && ftruncate(fd, (off_t)bytes) != 0) { close(fd); fd = -1; }
    if (fd >= 0) {
        *mem = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (*mem == MAP_FAILED) { close(fd); fd = -1; }
    }
    if (fd < 0) { *mem = calloc(1, bytes); return -1; }
    return fd;
}
static void put_rgb(unsigned char *out, const unsigned char *fb, int w, int h) {
    const Uint32 *px = (const Uint32 *)fb;
    for (int i = 0; i < w * h; i++) {
        out[i*3+0] = (px[i] >> 16) & 0xff;
        out[i*3+1] = (px[i] >> 8) & 0xff;
        out[i*3+2] = px[i] & 0xff;
    }
}
static int write_ppm(const char *path, const unsigned char *rgb, int w, int h) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror("fopen ppm"); return -1; }
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    fwrite(rgb, 1, (size_t)w*h*3, f);
    fclose(f);
    return 0;
}
static void fill(SDL_Renderer *r, int x, int y, int w, int h, int cr, int cg, int cb) {
    SDL_SetRenderDrawColor(r, cr, cg, cb, 255);
    SDL_FRect fr = {(float)x, (float)y, (float)w, (float)h};
    SDL_RenderFillRect(r, &fr);
}
// PIN the tsp-osr-safe WINDOW recipe (the path C6/E6 use), best-effort (logged for evidence).
static void pin_tsp_osr_recipe(int w, int h) {
    SDL_SetHint(SDL_HINT_RENDER_DRIVER, "software");
    SDL_Window *win = SDL_CreateWindow("tsp-osr-pin", w, h, 0);   // NO SDL_WINDOW_OPENGL
    if (!win) { fprintf(stderr, "tsp-osr-pin: window create skipped (%s)\n", SDL_GetError()); return; }
    SDL_Renderer *r = SDL_CreateRenderer(win, "software");
    if (!r) { fprintf(stderr, "tsp-osr-pin: FAIL renderer NULL (%s)\n", SDL_GetError()); SDL_DestroyWindow(win); return; }
    fprintf(stderr, "tsp-osr-pin: OK window(no-GL)+SDL_CreateRenderer(\"software\") -> '%s'\n",
            SDL_GetRendererName(r) ? SDL_GetRendererName(r) : "?");
    SDL_DestroyRenderer(r);
    SDL_DestroyWindow(win);
}

// ---- layout.txt parsing (the descriptor, computed host-side by layout.py) ----
static int parse_layout(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "FAIL open layout %s: %s\n", path, strerror(errno)); return -1; }
    char line[1024];
    while (fgets(line, sizeof line, f)) {
        char *tok = strtok(line, " \t\n");
        if (!tok) continue;
        if (!strcmp(tok, "canvas")) {
            CANVAS_W = atoi(strtok(NULL, " \t\n"));
            CANVAS_H = atoi(strtok(NULL, " \t\n"));
            // rotation token ignored: regions are asserted in canvas space (.4 owns rotation).
        } else if (!strcmp(tok, "node")) {
            char *p = strtok(NULL, " \t\n");
            if (p && n_node < MAX_NODES) {
                int fd = open(p, O_RDONLY | O_NONBLOCK);
                if (fd < 0) { fprintf(stderr, "FAIL open node %s: %s\n", p, strerror(errno)); fclose(f); return -1; }
                node_fd[n_node++] = fd;
                fprintf(stderr, "opened evdev node %s\n", p);
            }
        } else if (!strcmp(tok, "ctl")) {
            if (n_ctl >= MAX_CTL) continue;
            struct ctl *c = &ctls[n_ctl++];
            memset(c, 0, sizeof *c);
            snprintf(c->skin, sizeof c->skin, "%s", strtok(NULL, " \t\n"));
            char *kind = strtok(NULL, " \t\n");
            c->kind = !kind ? KIND_BUTTON
                    : !strcmp(kind, "trigger") ? KIND_TRIGGER
                    : !strcmp(kind, "hat")     ? KIND_HAT
                    : !strcmp(kind, "stick")   ? KIND_STICK : KIND_BUTTON;
            c->x = atoi(strtok(NULL, " \t\n"));
            c->y = atoi(strtok(NULL, " \t\n"));
            c->w = atoi(strtok(NULL, " \t\n"));
            c->h = atoi(strtok(NULL, " \t\n"));
            c->ncodes = atoi(strtok(NULL, " \t\n"));
            if (c->ncodes > MAX_CODES) c->ncodes = MAX_CODES;
            for (int i = 0; i < c->ncodes; i++) {
                struct code *cc = &c->codes[i];
                cc->type = atoi(strtok(NULL, " \t\n"));
                cc->code = atoi(strtok(NULL, " \t\n"));
                cc->vmin = atoi(strtok(NULL, " \t\n"));
                cc->vmax = atoi(strtok(NULL, " \t\n"));
                char *role = strtok(NULL, " \t\n");
                cc->role = role ? role[0] : '?';
                // rest value: triggers start released (min); sticks/hats start centred.
                cc->value = (cc->type == EV_ABS && c->kind != KIND_TRIGGER) ? (cc->vmin + cc->vmax) / 2
                          : (cc->type == EV_ABS) ? cc->vmin : 0;
            }
        }
    }
    fclose(f);
    fprintf(stderr, "layout: canvas %dx%d, %d controls, %d nodes\n", CANVAS_W, CANVAS_H, n_ctl, n_node);
    return 0;
}

// ---- event handling: kernel reports -> control state (the binding under test) ----
static void apply_event(const struct in_ev *ev) {
    if (ev->type == EV_SYN) return;
    for (int i = 0; i < n_ctl; i++)
        for (int j = 0; j < ctls[i].ncodes; j++)
            if (ctls[i].codes[j].type == ev->type && ctls[i].codes[j].code == ev->code)
                ctls[i].codes[j].value = ev->value;
}
static void drain_events(void) {
    for (int n = 0; n < n_node; n++) {
        struct in_ev ev;
        for (;;) {
            ssize_t r = read(node_fd[n], &ev, sizeof ev);
            if (r == (ssize_t)sizeof ev) apply_event(&ev);
            else break;   // EAGAIN / partial -> nothing more pending
        }
    }
}

static int ctl_active(const struct ctl *c) {
    for (int j = 0; j < c->ncodes; j++) {
        const struct code *cc = &c->codes[j];
        if (cc->type == EV_KEY) { if (cc->value) return 1; }
        else {  // EV_ABS displacement past 25% deadzone
            double centre = (cc->vmin + cc->vmax) / 2.0;
            double thr = (cc->vmax - cc->vmin) * 0.25;
            if (thr < 0) thr = -thr;
            if ((double)cc->value - centre > thr || centre - (double)cc->value > thr) return 1;
        }
    }
    return 0;
}
static double ctl_fraction(const struct ctl *c) {  // trigger fill 0..1
    const struct code *cc = &c->codes[0];
    if (cc->vmax == cc->vmin) return 0.0;
    double f = (double)(cc->value - cc->vmin) / (double)(cc->vmax - cc->vmin);
    return f < 0 ? 0 : f > 1 ? 1 : f;
}

// normalized -1..1 deflection of the stick/hat axis tagged <role> (0 if absent).
static double axis_norm(const struct ctl *c, char role) {
    for (int j = 0; j < c->ncodes; j++) {
        const struct code *cc = &c->codes[j];
        if (cc->role == role && cc->type == EV_ABS) {
            double half = (cc->vmax - cc->vmin) / 2.0;
            if (half == 0) return 0;
            double n = ((double)cc->value - (cc->vmin + cc->vmax) / 2.0) / half;
            return n < -1 ? -1 : n > 1 ? 1 : n;
        }
    }
    return 0;
}
// is any digital (stick-click L3/R3) code on? a133 sticks have NO such code -> never pressed.
static int any_key(const struct ctl *c) {
    for (int j = 0; j < c->ncodes; j++)
        if (c->codes[j].type == EV_KEY && c->codes[j].value) return 1;
    return 0;
}

// D-pad: a directional cross. ONLY the pressed direction's arm lights (no always-on centre
// hub). Each lit arm extends THROUGH the centre square, so the region-sample at the rect centre
// is red iff a direction is pressed — keeping the .5/.6 assertion valid without a separate hub.
static void render_hat(SDL_Renderer *r, struct ctl *c) {
    int hx = (int)axis_norm(c, 'x'), hy = (int)axis_norm(c, 'y');
    int hxs = hx > 0 ? 1 : hx < 0 ? -1 : 0, hys = hy > 0 ? 1 : hy < 0 ? -1 : 0;
    int t = c->w / 3 < c->h / 3 ? c->w / 3 : c->h / 3, hl = t / 2;
    int x0 = c->x, y0 = c->y, x1 = c->x + c->w, y1 = c->y + c->h;
    int cx = c->x + c->w / 2, cy = c->y + c->h / 2, g = 70, R = 220, D = 30;
    fill(r, cx - hl, y0, t, c->h, g, g, g);                  // vertical bar (gray base)
    fill(r, x0, cy - hl, c->w, t, g, g, g);                  // horizontal bar (gray base)
    if (hxs > 0) fill(r, cx - hl, cy - hl, x1 - (cx - hl), t, R, D, D);  // right (incl. centre)
    if (hxs < 0) fill(r, x0, cy - hl, (cx + hl) - x0, t, R, D, D);       // left  (incl. centre)
    if (hys > 0) fill(r, cx - hl, cy - hl, t, y1 - (cy - hl), R, D, D);  // down  (incl. centre)
    if (hys < 0) fill(r, cx - hl, y0, t, (cy + hl) - y0, R, D, D);       // up    (incl. centre)
}

// Analog stick: a calibration box. A vector from centre to the deflection position shows HOW
// FAR + WHICH WAY (like a stick-calibration chart); a red border shows the stick PRESSED (L3/R3,
// a523 only — a133 has no stick-click code so it never lights). The centre hub guarantees the
// region-sample is red iff the stick is active (deflected past deadzone OR pressed).
static void render_stick(SDL_Renderer *r, struct ctl *c) {
    int cx = c->x + c->w / 2, cy = c->y + c->h / 2;
    int pressed = any_key(c), active = ctl_active(c);
    fill(r, c->x, c->y, c->w, c->h, 40, 40, 40);            // box interior
    int bw = pressed ? 4 : 1;                               // border (red + thick = pressed)
    for (int k = 0; k < bw; k++) {
        SDL_SetRenderDrawColor(r, pressed ? 220 : 110, pressed ? 30 : 110, pressed ? 30 : 110, 255);
        SDL_FRect e = {(float)(c->x + k), (float)(c->y + k),
                       (float)(c->w - 2 * k), (float)(c->h - 2 * k)};
        SDL_RenderRect(r, &e);
    }
    fill(r, cx, c->y + 5, 1, c->h - 10, 80, 80, 80);        // crosshair guides (faint)
    fill(r, c->x + 5, cy, c->w - 10, 1, 80, 80, 80);
    double nx = axis_norm(c, 'x'), ny = axis_norm(c, 'y');
    int margin = c->w / 8 + 2;
    int dx = cx + (int)(nx * (c->w / 2 - margin)), dy = cy + (int)(ny * (c->h / 2 - margin));
    int hub = c->w / 8 < 6 ? 6 : c->w / 8;
    int cr = active ? 220 : 150, cg = active ? 30 : 150, cb = active ? 30 : 150;
    SDL_SetRenderDrawColor(r, cr, cg, cb, 255);
    SDL_RenderLine(r, (float)cx, (float)cy, (float)dx, (float)dy);          // deflection vector
    fill(r, dx - hub / 2, dy - hub / 2, hub, hub, cr, cg, cb);              // position dot
    fill(r, cx - hub / 2, cy - hub / 2, hub, hub, active ? 220 : 70,        // centre hub
         active ? 30 : 70, active ? 30 : 70);
}

static void render(SDL_Renderer *r) {
    fill(r, 0, 0, CANVAS_W, CANVAS_H, 24, 24, 24);            // bg gray (matches .4)
    for (int i = 0; i < n_ctl; i++) {
        struct ctl *c = &ctls[i];
        switch (c->kind) {
        case KIND_TRIGGER: {
            fill(r, c->x, c->y, c->w, c->h, 70, 70, 70);     // track
            int fw = (int)(c->w * ctl_fraction(c) + 0.5);
            if (fw > 0) fill(r, c->x, c->y, fw, c->h, 220, 30, 30);   // proportional fill
            break;
        }
        case KIND_HAT:   render_hat(r, c);   break;
        case KIND_STICK: render_stick(r, c); break;
        default: {  // KIND_BUTTON
            int on = ctl_active(c);
            fill(r, c->x, c->y, c->w, c->h, on ? 220 : 70, on ? 30 : 70, on ? 30 : 70);
            break;
        }
        }
    }
    SDL_RenderPresent(r);
}

// ---- FIFO line protocol (O_RDWR so opens never block; one reader/one writer per fifo) ----
static int read_line(int fd, char *buf, int cap) {
    int n = 0;
    while (n < cap - 1) {
        char ch;
        ssize_t r = read(fd, &ch, 1);
        if (r <= 0) return -1;
        if (ch == '\n') break;
        buf[n++] = ch;
    }
    buf[n] = 0;
    return n;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: hwprobe-lite <io-dir>\n"); return 2; }
    const char *dir = argv[1];
    char layout_p[512], req_p[512], resp_p[512];
    snprintf(layout_p, sizeof layout_p, "%s/layout.txt", dir);
    snprintf(req_p,    sizeof req_p,    "%s/req", dir);
    snprintf(resp_p,   sizeof resp_p,   "%s/resp", dir);

    if (parse_layout(layout_p) != 0) return 3;

    SDL_SetHint(SDL_HINT_VIDEO_DRIVER, "dummy");
    if (!SDL_Init(SDL_INIT_VIDEO))
        fprintf(stderr, "warn: SDL_Init(VIDEO) failed (%s) — surface path still works\n", SDL_GetError());
    pin_tsp_osr_recipe(CANVAS_W, CANVAS_H);

    size_t bytes = (size_t)CANVAS_W * CANVAS_H * 4;
    void *fbmem = NULL;
    int fbfd = make_fb(bytes, &fbmem);
    fprintf(stderr, "virtual fb: %s %dx%d (%zu bytes)\n",
            fbfd >= 0 ? "memfd" : "anon-buffer", CANVAS_W, CANVAS_H, bytes);
    SDL_Surface *surf = SDL_CreateSurfaceFrom(CANVAS_W, CANVAS_H, SDL_PIXELFORMAT_XRGB8888, fbmem, CANVAS_W * 4);
    if (!surf) { fprintf(stderr, "FAIL CreateSurfaceFrom: %s\n", SDL_GetError()); return 3; }
    SDL_Renderer *r = SDL_CreateSoftwareRenderer(surf);   // tsp-osr-safe
    if (!r) { fprintf(stderr, "FAIL CreateSoftwareRenderer: %s\n", SDL_GetError()); return 3; }

    int req = open(req_p, O_RDWR);
    int resp = open(resp_p, O_RDWR);
    if (req < 0 || resp < 0) { fprintf(stderr, "FAIL open fifos: %s\n", strerror(errno)); return 4; }

    unsigned char *rgb = malloc((size_t)CANVAS_W * CANVAS_H * 3);
    dprintf(resp, "ready\n");
    fprintf(stderr, "ready: awaiting commands\n");

    char line[600];
    while (read_line(req, line, sizeof line) >= 0) {
        if (!strncmp(line, "snap", 4)) {
            const char *path = line + 4;
            while (*path == ' ') path++;
            drain_events();
            render(r);
            put_rgb(rgb, (unsigned char *)fbmem, CANVAS_W, CANVAS_H);
            int ok = write_ppm(path, rgb, CANVAS_W, CANVAS_H) == 0;
            dprintf(resp, ok ? "ok\n" : "err\n");
        } else if (!strncmp(line, "quit", 4)) {
            dprintf(resp, "bye\n");
            break;
        } else if (line[0]) {
            dprintf(resp, "err\n");
        }
    }

    free(rgb);
    SDL_DestroyRenderer(r);
    SDL_DestroySurface(surf);
    if (fbfd >= 0) { munmap(fbmem, bytes); close(fbfd); } else free(fbmem);
    SDL_Quit();
    return 0;
}
