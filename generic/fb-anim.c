// A continuous, protocol-free fb0 writer: redraws /dev/fb0 at 60 Hz until SIGTERM or SIGINT, then
// exits 0. It lets a consumer test frame flow with the image's own app (fb-pattern draws once and
// exits). When PF_FB0_HDR names a 64-byte file, it also publishes the fb0.hdr seqlock header
// (docs/FB0-SIDECAR.md) around every frame.
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define FRAME_NS (1000000000L / 60)

struct pfb0_hdr {             // little-endian on every supported target
    char magic[4];            // "PFB0"
    uint16_t version, header_size;
    uint32_t width, height, stride, fourcc;
    uint64_t seq, timestamp_ns;
    uint8_t reserved[24];
};
_Static_assert(sizeof(struct pfb0_hdr) == 64, "fb0.hdr is 64 bytes");

static volatile sig_atomic_t stop;
static void on_signal(int sig) { (void)sig; stop = 1; }

static void *map_file(const char *path, size_t n) {
    int fd = open(path, O_RDWR);
    if (fd < 0) { perror(path); return NULL; }
    struct stat st;
    if (fstat(fd, &st) < 0) { perror(path); close(fd); return NULL; }
    if ((size_t)st.st_size < n) {
        fprintf(stderr, "%s is %lld bytes, smaller than the %zu needed\n", path, (long long)st.st_size, n);
        close(fd); return NULL;
    }
    void *p = mmap(NULL, n, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (p == MAP_FAILED) { perror(path); return NULL; }
    return p;
}

static void box(uint32_t *p, int w, int h, int x, int y, int bw, int bh, uint32_t c) {
    for (int yy=y; yy<y+bh && yy<h; yy++) for (int xx=x; xx<x+bw && xx<w; xx++)
        if (xx >= 0 && yy >= 0) p[(size_t)yy*w+xx] = c;
}

static void draw(uint32_t *p, int w, int h, uint64_t frame) {
    for (size_t i=0; i<(size_t)w*h; i++) p[i] = 0x00101828;
    int bw = w/16 > 0 ? w/16 : 1;
    box(p, w, h, (int)(frame % (uint64_t)(w + bw)) - bw, 0, bw, h, 0x00f0f0f0);   // sweeping bar
    for (int bit=0; bit<16; bit++)                                                 // frame counter
        box(p, w, h, bit*(w/16), h-h/8, w/16-1, h/8, (frame >> (15-bit)) & 1 ? 0x002fc85a : 0x00303030);
}

int main(void) {
    int w = atoi(getenv("PF_FB_WIDTH") ? getenv("PF_FB_WIDTH") : "0");
    int h = atoi(getenv("PF_FB_HEIGHT") ? getenv("PF_FB_HEIGHT") : "0");
    if (w <= 0 || h <= 0) { fputs("PF_FB_WIDTH/HEIGHT required\n", stderr); return 2; }
    const char *path = getenv("PF_FB0") ? getenv("PF_FB0") : "/dev/fb0";
    size_t n = (size_t)w*h*4;
    uint32_t *px = map_file(path, n);
    if (!px) return 3;
    struct pfb0_hdr *hdr = NULL;
    const char *hdr_path = getenv("PF_FB0_HDR");
    if (hdr_path && *hdr_path && !(hdr = map_file(hdr_path, sizeof *hdr))) return 4;

    struct sigaction sa = {0};
    sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);

    // An odd seq marks a write in progress; a previous writer may have left either parity.
    uint64_t seq = hdr ? (__atomic_load_n(&hdr->seq, __ATOMIC_RELAXED) | 1) : 0;
    struct timespec next;
    clock_gettime(CLOCK_MONOTONIC, &next);
    for (uint64_t frame = 0; !stop; frame++) {
        if (hdr) {
            __atomic_store_n(&hdr->seq, seq, __ATOMIC_RELAXED);
            __atomic_thread_fence(__ATOMIC_RELEASE);
            memcpy(hdr->magic, "PFB0", 4);
            hdr->version = 1; hdr->header_size = sizeof *hdr;
            hdr->width = (uint32_t)w; hdr->height = (uint32_t)h; hdr->stride = (uint32_t)w*4;
            hdr->fourcc = 0x34325258;   // XR24, DRM_FORMAT_XRGB8888
        }
        draw(px, w, h, frame);
        if (hdr) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            hdr->timestamp_ns = (uint64_t)now.tv_sec * 1000000000u + (uint64_t)now.tv_nsec;
            seq += 1;
            __atomic_store_n(&hdr->seq, seq, __ATOMIC_RELEASE);   // even: frame and header whole
            seq += 1;
        }
        next.tv_nsec += FRAME_NS;
        if (next.tv_nsec >= 1000000000L) { next.tv_sec++; next.tv_nsec -= 1000000000L; }
        while (!stop && clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) != 0) {}
    }
    munmap(px, n);
    if (hdr) munmap(hdr, sizeof *hdr);
    return 0;
}
