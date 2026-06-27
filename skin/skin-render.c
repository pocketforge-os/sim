// skin-render.c — tsp-an4.6: the SDL3 clickable-skin RENDERER (the AVD north star's pixels).
//
// A CLIENT of the .5 control surface: it draws, it does NOT inject. The Python driver
// (check-skin.py / the live supervisor) owns all geometry + control_surface calls via
// skin_model.py; this program only COMPOSITES a scene and (in --window mode) forwards raw mouse
// clicks back to the driver. ONE descriptor -> app + sim + test + SKIN: every rect/rotation in
// the scene came from capabilities.toml through skin_model, nothing here is device-specific.
//
// It composites, in skin-image space (the real bezel body.png at native resolution):
//   1. the unlit chassis  body.png
//   2. per lit control, that control's rect copied from body_lit.png (the lit overlay)
//   3. the LIVE virtual framebuffer (what the IDENTICAL arm64 app rendered, handed in as a PPM
//      by the .5 control_surface snapshot) stretched into screens[0].display_rect, pre-rotated
//      by the DATA-driven composite rotation (NOT per-SoC silicon — honesty item 5)
//   4. the manufacturer>device PICKER panel (TrimUI > 5040 / 5050), selected entry highlighted
//
// tsp-osr-safe recipe (pinned by .4, reused here for the on-window path C6 owns): a non-OPENGL
// window + SDL_CreateRenderer(win,"software"); the offscreen --shot path uses
// SDL_CreateSoftwareRenderer(surface) — neither can trip the NULL-renderer GL segfault.
//
// Modes:
//   --shot OUT.ppm        offscreen software-render the scene -> PPM (the proof + owner artifact)
//   --window              open a live window, redraw on "reload\n" (stdin), emit clicks to stdout
//                         as  "click <skin_x> <skin_y>"  or  "pick <device_id>"  (driver applies)
// Scene comes from --scene FILE or stdin (the protocol skin_model.emit_scene writes).
//
// Build: against the sim's SDL3-render (fb/build-sdl3-render.sh). Font is the committed,
// generated font8x13.h (no SDL_ttf / SDL_image / PIL dep on the build host).
#include <SDL3/SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/time.h>
#include "font8x13.h"

#define PANEL_W 300          // picker panel width (only when the scene carries picker lines)
#define MAXPART 64
#define MAXPICK 32

typedef struct { char name[40], kind[16]; int x, y, w, h, lit, hx, hy; } Part;
typedef struct { char man[40], code[24], model[48]; int sel; } Pick;

typedef struct {
    char body[512], lit_body[512], fb[512];
    int skin_w, skin_h, canvas_w, canvas_h;
    int dx, dy, dw, dh;          // display_rect (skin space)
    char rot[12];                // composite rotation: none|cw90|cw180|cw270
    Part parts[MAXPART]; int nparts;
    Pick picks[MAXPICK]; int npicks;
    char title[256];
} Scene;

// ---- PPM (P6) reader: -> malloc'd RGB + dims ----
static unsigned char *read_ppm(const char *path, int *w, int *h) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "read_ppm: cannot open %s\n", path); return NULL; }
    char magic[3] = {0};
    if (fscanf(f, "%2s", magic) != 1 || strcmp(magic, "P6")) { fclose(f); return NULL; }
    int maxv;
    if (fscanf(f, "%d %d %d", w, h, &maxv) != 3) { fclose(f); return NULL; }
    fgetc(f);  // single whitespace after maxval
    size_t n = (size_t)(*w) * (*h) * 3;
    unsigned char *rgb = malloc(n);
    if (fread(rgb, 1, n, f) != n) { free(rgb); fclose(f); return NULL; }
    fclose(f);
    return rgb;
}

static int write_ppm(const char *path, const unsigned char *rgb, int w, int h) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror("fopen ppm"); return -1; }
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    fwrite(rgb, 1, (size_t)w * h * 3, f);
    fclose(f);
    return 0;
}

// Logical rotation of an RGB buffer (same semantics as fb-render.c — DATA, not silicon).
static unsigned char *rotate_rgb(const unsigned char *in, int w, int h, const char *rot,
                                 int *ow, int *oh) {
    int cw90 = !strcmp(rot, "cw90"), cw180 = !strcmp(rot, "cw180"), cw270 = !strcmp(rot, "cw270");
    *ow = (cw90 || cw270) ? h : w;
    *oh = (cw90 || cw270) ? w : h;
    unsigned char *out = malloc((size_t)*ow * *oh * 3);
    for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
        int dxp, dyp;
        if (cw90)       { dxp = h - 1 - y; dyp = x; }
        else if (cw180) { dxp = w - 1 - x; dyp = h - 1 - y; }
        else if (cw270) { dxp = y;         dyp = w - 1 - x; }
        else            { dxp = x;         dyp = y; }
        memcpy(out + ((size_t)dyp * *ow + dxp) * 3, in + ((size_t)y * w + x) * 3, 3);
    }
    return out;
}

static SDL_Texture *tex_from_ppm(SDL_Renderer *r, const char *path, int *w, int *h) {
    unsigned char *rgb = read_ppm(path, w, h);
    if (!rgb) return NULL;
    SDL_Surface *s = SDL_CreateSurfaceFrom(*w, *h, SDL_PIXELFORMAT_RGB24, rgb, *w * 3);
    if (!s) { free(rgb); return NULL; }
    SDL_Texture *t = SDL_CreateTextureFromSurface(r, s);
    SDL_DestroySurface(s);
    free(rgb);
    if (t) SDL_SetTextureScaleMode(t, SDL_SCALEMODE_NEAREST);
    return t;
}

// ---- text (committed bitmap font) ----
static void draw_text(SDL_Renderer *r, int x, int y, int scale, const char *s,
                      int cr, int cg, int cb) {
    SDL_SetRenderDrawColor(r, cr, cg, cb, 255);
    int cx = x;
    for (; *s; s++) {
        unsigned char ch = (unsigned char)*s;
        if (ch < FONT_FIRST || ch > FONT_LAST) { cx += (FONT_W + 1) * scale; continue; }
        const unsigned char *g = FONT8X13[ch - FONT_FIRST];
        for (int row = 0; row < FONT_H; row++)
            for (int col = 0; col < FONT_W; col++)
                if (g[row] & (1 << (7 - col))) {
                    SDL_FRect px = {(float)(cx + col * scale), (float)(y + row * scale),
                                    (float)scale, (float)scale};
                    SDL_RenderFillRect(r, &px);
                }
        cx += (FONT_W + 1) * scale;
    }
}

static void fill(SDL_Renderer *r, int x, int y, int w, int h, int cr, int cg, int cb) {
    SDL_SetRenderDrawColor(r, cr, cg, cb, 255);
    SDL_FRect fr = {(float)x, (float)y, (float)w, (float)h};
    SDL_RenderFillRect(r, &fr);
}

static void outline(SDL_Renderer *r, int x, int y, int w, int h, int cr, int cg, int cb) {
    SDL_SetRenderDrawColor(r, cr, cg, cb, 255);
    SDL_FRect e[4] = {{x, y, w, 1}, {x, y + h - 1, w, 1}, {x, y, 1, h}, {x + w - 1, y, 1, h}};
    SDL_RenderRects(r, e, 4);
}

// ---- scene parser (whitespace protocol from skin_model.emit_scene) ----
static int parse_scene(FILE *f, Scene *sc) {
    memset(sc, 0, sizeof(*sc));
    strcpy(sc->rot, "none");
    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        char *nl = strchr(line, '\n'); if (nl) *nl = 0;
        char *tok = strtok(line, " ");
        if (!tok) continue;
        if (!strcmp(tok, "skin")) {
            strncpy(sc->body, strtok(NULL, " "), 511);
            strncpy(sc->lit_body, strtok(NULL, " "), 511);
            sc->skin_w = atoi(strtok(NULL, " ")); sc->skin_h = atoi(strtok(NULL, " "));
        } else if (!strcmp(tok, "display")) {
            sc->dx = atoi(strtok(NULL, " ")); sc->dy = atoi(strtok(NULL, " "));
            sc->dw = atoi(strtok(NULL, " ")); sc->dh = atoi(strtok(NULL, " "));
            strncpy(sc->rot, strtok(NULL, " "), 11);
        } else if (!strcmp(tok, "fb")) {
            strncpy(sc->fb, strtok(NULL, " "), 511);
            sc->canvas_w = atoi(strtok(NULL, " ")); sc->canvas_h = atoi(strtok(NULL, " "));
        } else if (!strcmp(tok, "part") && sc->nparts < MAXPART) {
            Part *p = &sc->parts[sc->nparts++];
            strncpy(p->name, strtok(NULL, " "), 39);
            strncpy(p->kind, strtok(NULL, " "), 15);
            p->x = atoi(strtok(NULL, " ")); p->y = atoi(strtok(NULL, " "));
            p->w = atoi(strtok(NULL, " ")); p->h = atoi(strtok(NULL, " "));
            p->lit = atoi(strtok(NULL, " "));
            char *hx = strtok(NULL, " "), *hy = strtok(NULL, " ");
            p->hx = hx ? atoi(hx) : 0; p->hy = hy ? atoi(hy) : 0;
        } else if (!strcmp(tok, "picker") && sc->npicks < MAXPICK) {
            Pick *p = &sc->picks[sc->npicks++];
            strncpy(p->man, strtok(NULL, " "), 39);
            strncpy(p->code, strtok(NULL, " "), 23);
            p->sel = atoi(strtok(NULL, " "));
            char *model = strtok(NULL, "");           // rest-of-line (may contain spaces)
            if (model) { while (*model == ' ') model++; strncpy(p->model, model, 47); }
        } else if (!strcmp(tok, "title")) {
            char *rest = strtok(NULL, "");
            if (rest) strncpy(sc->title, rest, 255);
        }
    }
    return sc->skin_w > 0;
}

// bezel x-origin: shifted right by the picker panel iff the scene carries picker lines.
static int bezel_ox(const Scene *sc) { return sc->npicks ? PANEL_W : 0; }
static int total_w(const Scene *sc) { return bezel_ox(sc) + sc->skin_w; }
static int total_h(const Scene *sc) { return sc->skin_h; }

static void render_scene(SDL_Renderer *r, Scene *sc, int show_outline) {
    int ox = bezel_ox(sc);
    SDL_SetRenderDrawColor(r, 18, 18, 22, 255);
    SDL_RenderClear(r);

    // 1) unlit chassis
    int bw, bh;
    SDL_Texture *body = tex_from_ppm(r, sc->body, &bw, &bh);
    if (body) { SDL_FRect d = {(float)ox, 0, (float)bw, (float)bh}; SDL_RenderTexture(r, body, NULL, &d); }

    // 2) lit overlays (copy each lit control's rect from body_lit over the body). A lit d-pad
    //    with a direction lights ONLY the pressed arm sub-rect(s) — same plus geometry as the
    //    bezel art (t = w/3) — so the bezel shows just the direction hit, not the whole cross.
    SDL_Texture *litb = tex_from_ppm(r, sc->lit_body, &bw, &bh);
    if (litb) for (int i = 0; i < sc->nparts; i++) {
        Part *p = &sc->parts[i];
        if (!p->lit) continue;
        if (!strcmp(p->kind, "hat") && (p->hx || p->hy)) {
            int t = (p->w / 3 < p->h / 3 ? p->w / 3 : p->h / 3), hl = t / 2;
            int cx = p->x + p->w / 2, cy = p->y + p->h / 2;
            int x0 = p->x, y0 = p->y, x1 = p->x + p->w, y1 = p->y + p->h;
            SDL_FRect arms[2]; int na = 0;
            if (p->hx > 0) arms[na++] = (SDL_FRect){cx - hl, cy - hl, x1 - (cx - hl), t};   // right
            if (p->hx < 0) arms[na++] = (SDL_FRect){x0, cy - hl, (cx + hl) - x0, t};         // left
            if (p->hy > 0) arms[na++] = (SDL_FRect){cx - hl, cy - hl, t, y1 - (cy - hl)};    // down
            if (p->hy < 0) arms[na++] = (SDL_FRect){cx - hl, y0, t, (cy + hl) - y0};         // up
            for (int a = 0; a < na; a++) {
                SDL_FRect d = {arms[a].x + ox, arms[a].y, arms[a].w, arms[a].h};
                SDL_RenderTexture(r, litb, &arms[a], &d);
            }
        } else {
            SDL_FRect s = {(float)p->x, (float)p->y, (float)p->w, (float)p->h};
            SDL_FRect d = {(float)(ox + p->x), (float)p->y, (float)p->w, (float)p->h};
            SDL_RenderTexture(r, litb, &s, &d);
        }
    }

    // 3) live framebuffer -> display_rect (pre-rotated by the data-driven composite rotation)
    if (sc->fb[0] && strcmp(sc->fb, "-")) {
        int fw, fh;
        unsigned char *rgb = read_ppm(sc->fb, &fw, &fh);
        if (rgb) {
            int rw = fw, rh = fh; unsigned char *use = rgb;
            if (strcmp(sc->rot, "none")) use = rotate_rgb(rgb, fw, fh, sc->rot, &rw, &rh);
            SDL_Surface *s = SDL_CreateSurfaceFrom(rw, rh, SDL_PIXELFORMAT_RGB24, use, rw * 3);
            SDL_Texture *ft = s ? SDL_CreateTextureFromSurface(r, s) : NULL;
            if (ft) {
                SDL_SetTextureScaleMode(ft, SDL_SCALEMODE_LINEAR);
                SDL_FRect d = {(float)(ox + sc->dx), (float)sc->dy, (float)sc->dw, (float)sc->dh};
                SDL_RenderTexture(r, ft, NULL, &d);
                SDL_DestroyTexture(ft);
            }
            if (s) SDL_DestroySurface(s);
            if (use != rgb) free(use);
            free(rgb);
        }
    }

    // optional: outline the clickable rects (evidence view; off for the clean owner shot)
    if (show_outline) for (int i = 0; i < sc->nparts; i++) {
        Part *p = &sc->parts[i];
        outline(r, ox + p->x, p->y, p->w, p->h, 90, 200, 255);
    }

    // 4) picker panel
    if (sc->npicks) {
        fill(r, 0, 0, PANEL_W, sc->skin_h, 28, 30, 38);
        outline(r, 0, 0, PANEL_W, sc->skin_h, 60, 64, 78);
        draw_text(r, 18, 22, 2, "Virtual Device", 150, 156, 170);
        // manufacturer header = first picker's manufacturer (single-vendor today)
        draw_text(r, 18, 58, 3, sc->picks[0].man, 235, 238, 245);
        int yy = 104;
        for (int i = 0; i < sc->npicks; i++) {
            Pick *p = &sc->picks[i];
            if (p->sel) { fill(r, 12, yy - 6, PANEL_W - 24, 56, 46, 92, 150);
                          outline(r, 12, yy - 6, PANEL_W - 24, 56, 120, 180, 240); }
            char line[80];
            snprintf(line, sizeof(line), "%s", p->code);
            draw_text(r, 26, yy, 3, line, p->sel ? 255 : 200, p->sel ? 255 : 204, 255);
            draw_text(r, 26, yy + 30, 2, p->model, p->sel ? 220 : 150, p->sel ? 225 : 156, 235);
            yy += 70;
        }
        if (sc->title[0]) draw_text(r, 18, sc->skin_h - 30, 2, sc->title, 130, 200, 150);
    }
}

// read pixels of a software-renderer-on-surface target back to RGB (XRGB8888 surface -> RGB24)
static int shot_to_ppm(unsigned char *xrgb, int w, int h, const char *out) {
    unsigned char *rgb = malloc((size_t)w * h * 3);
    const Uint32 *px = (const Uint32 *)xrgb;
    for (int i = 0; i < w * h; i++) {
        rgb[i * 3 + 0] = (px[i] >> 16) & 0xff;
        rgb[i * 3 + 1] = (px[i] >> 8) & 0xff;
        rgb[i * 3 + 2] = px[i] & 0xff;
    }
    int rc = write_ppm(out, rgb, w, h);
    free(rgb);
    return rc;
}

static int load_scene(const char *path, Scene *sc) {
    FILE *f = path ? fopen(path, "r") : stdin;
    if (!f) { fprintf(stderr, "cannot open scene %s\n", path); return 0; }
    int ok = parse_scene(f, sc);
    if (path) fclose(f);
    return ok;
}

int main(int argc, char **argv) {
    const char *shot = NULL, *scene_path = NULL;
    int window = 0, show_outline = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--shot") && i + 1 < argc) shot = argv[++i];
        else if (!strcmp(argv[i], "--scene") && i + 1 < argc) scene_path = argv[++i];
        else if (!strcmp(argv[i], "--window")) window = 1;
        else if (!strcmp(argv[i], "--outline")) show_outline = 1;
    }

    Scene sc;
    if (!load_scene(scene_path, &sc)) { fprintf(stderr, "bad scene\n"); return 2; }
    int W = total_w(&sc), H = total_h(&sc);

    if (!window) {
        // ----- offscreen --shot (tsp-osr-safe: software renderer on a surface) -----
        SDL_SetHint(SDL_HINT_VIDEO_DRIVER, "dummy");
        SDL_Init(SDL_INIT_VIDEO);
        size_t bytes = (size_t)W * H * 4;
        void *mem = calloc(1, bytes);
        SDL_Surface *surf = SDL_CreateSurfaceFrom(W, H, SDL_PIXELFORMAT_XRGB8888, mem, W * 4);
        SDL_Renderer *r = surf ? SDL_CreateSoftwareRenderer(surf) : NULL;
        if (!r) { fprintf(stderr, "FAIL renderer: %s\n", SDL_GetError()); return 3; }
        render_scene(r, &sc, show_outline);
        SDL_RenderPresent(r);
        int rc = shot ? shot_to_ppm((unsigned char *)mem, W, H, shot) : 0;
        fprintf(stderr, "skin-render: shot %dx%d -> %s\n", W, H, shot ? shot : "(none)");
        SDL_DestroyRenderer(r); SDL_DestroySurface(surf); free(mem); SDL_Quit();
        return rc ? 4 : 0;
    }

    // ----- live --window (tsp-osr-safe: non-GL window + forced software renderer) -----
    SDL_SetHint(SDL_HINT_RENDER_DRIVER, "software");
    if (!SDL_Init(SDL_INIT_VIDEO)) { fprintf(stderr, "SDL_Init: %s\n", SDL_GetError()); return 3; }
    SDL_Window *win = SDL_CreateWindow("PocketForge — Virtual Device", W, H, 0);  // no GL
    SDL_Renderer *r = win ? SDL_CreateRenderer(win, "software") : NULL;
    if (!r) { fprintf(stderr, "FAIL window/renderer: %s\n", SDL_GetError()); return 3; }
    render_scene(r, &sc, show_outline);
    SDL_RenderPresent(r);
    fprintf(stderr, "skin-render: window up (%dx%d); click=stdout, 'reload'/'quit' on stdin\n", W, H);

    // stdin is read non-blocking-ish via the event loop's timeout; clicks go to stdout.
    int running = 1;
    while (running) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_EVENT_QUIT) running = 0;
            else if (e.type == SDL_EVENT_MOUSE_BUTTON_DOWN) {
                int ox = bezel_ox(&sc);
                int mx = (int)e.button.x, my = (int)e.button.y;
                if (ox && mx < PANEL_W) {                  // a picker click
                    int idx = (my - 98) / 70;
                    if (idx >= 0 && idx < sc.npicks)
                        printf("pick %s\n", sc.picks[idx].code), fflush(stdout);
                } else {                                   // a bezel click -> skin space
                    printf("click %d %d\n", mx - ox, my); fflush(stdout);
                }
            }
        }
        // drain any driver commands on stdin (reload <file> / quit)
        // (kept simple: the driver writes a line then we re-render)
        struct timeval tv = {0, 0}; fd_set fds; FD_ZERO(&fds); FD_SET(0, &fds);
        if (select(1, &fds, NULL, NULL, &tv) > 0) {
            char cmd[600];
            if (fgets(cmd, sizeof(cmd), stdin)) {
                if (!strncmp(cmd, "quit", 4)) running = 0;
                else if (!strncmp(cmd, "reload", 6)) {
                    char *p = cmd + 6; while (*p == ' ') p++;
                    char *nl = strchr(p, '\n'); if (nl) *nl = 0;
                    Scene ns;
                    if (load_scene(*p ? p : scene_path, &ns)) {
                        sc = ns; render_scene(r, &sc, show_outline); SDL_RenderPresent(r);
                    }
                }
            }
        }
        SDL_Delay(16);
    }
    SDL_DestroyRenderer(r); SDL_DestroyWindow(win); SDL_Quit();
    return 0;
}
