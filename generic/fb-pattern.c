// A deliberately protocol-free fb0 client used to prove generic capture.
// It opens /dev/fb0, mmaps XRGB8888 pixels, draws one deterministic frame, and exits.
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

static const unsigned char digits[10][5] = {
    {7,5,5,5,7},{2,6,2,2,7},{7,1,7,4,7},{7,1,7,1,7},{5,5,7,1,1},
    {7,4,7,1,7},{7,4,7,5,7},{7,1,1,1,1},{7,5,7,5,7},{7,5,7,1,7}
};

static void box(uint32_t *p, int w, int h, int x, int y, int bw, int bh, uint32_t c) {
    for (int yy=y; yy<y+bh && yy<h; yy++) for (int xx=x; xx<x+bw && xx<w; xx++)
        if (xx >= 0 && yy >= 0) p[(size_t)yy*w+xx] = c;
}

int main(void) {
    int w = atoi(getenv("PF_FB_WIDTH") ? getenv("PF_FB_WIDTH") : "0");
    int h = atoi(getenv("PF_FB_HEIGHT") ? getenv("PF_FB_HEIGHT") : "0");
    const char *path = getenv("PF_FB0") ? getenv("PF_FB0") : "/dev/fb0";
    if (w <= 0 || h <= 0) { fputs("PF_FB_WIDTH/HEIGHT required\n", stderr); return 2; }
    int fd = open(path, O_RDWR);
    size_t n = (size_t)w*h*4;
    if (fd < 0) { perror(path); return 3; }
    uint32_t *p = mmap(NULL, n, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) { perror("mmap fb0"); return 4; }
    for (size_t i=0; i<(size_t)w*h; i++) p[i] = 0x00101828;
    box(p,w,h,0,0,w/3,h/3,0x00d62f2f);
    box(p,w,h,w-w/3,0,w/3,h/3,0x002fc85a);
    box(p,w,h,0,h-h/3,w/3,h/3,0x003445d9);
    box(p,w,h,w-w/3,h-h/3,w/3,h/3,0x00e5c51b);
    box(p,w,h,w/2-w/12,h/2-h/12,w/6,h/6,0x00f0f0f0);
    // Frame counter 1, rendered rather than transported over a side protocol.
    int s = (w < h ? w : h) / 32; if (s < 2) s = 2;
    for (int row=0; row<5; row++) for (int col=0; col<3; col++)
        if (digits[1][row] & (1u << (2-col))) box(p,w,h,w/2-2*s+col*s,h/2+3*s+row*s,s,s,0x00ffffff);
    msync(p,n,MS_SYNC); munmap(p,n); close(fd);
    return 0;
}
