// mkuinput.c — host-native uinput virtual gamepad creator (the canonical "TRIMUI Player1").
// Identical to qemu-tsp/regression/mkuinput.c — the SPIKE-3 device under test. Advertises
// the Xbox-360 HID SUPERSET (045e:028e) the real TrimUI exposes; the a133 descriptor lists
// the PHYSICAL subset (asymmetric rule: descriptor codes subset-of advertised codes).
// tsp-an4.3 generalizes this to register codes/absinfo straight from the descriptor.
// Stays alive (pause()); on SIGUSR1 injects a small known event sequence for read tests.
#include <linux/uinput.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <time.h>

static int g_fd = -1;

static void emit(int fd, int type, int code, int val){
  struct input_event ie; memset(&ie,0,sizeof ie);
  ie.type=type; ie.code=code; ie.value=val;
  if(write(fd,&ie,sizeof ie)<0) perror("emit write");
}

static void inject(int sig){
  (void)sig;
  emit(g_fd, EV_KEY, BTN_SOUTH, 1);
  emit(g_fd, EV_SYN, SYN_REPORT, 0);
  emit(g_fd, EV_ABS, ABS_Z, 128);
  emit(g_fd, EV_SYN, SYN_REPORT, 0);
  emit(g_fd, EV_KEY, BTN_SOUTH, 0);
  emit(g_fd, EV_SYN, SYN_REPORT, 0);
}

int main(){
  int fd=open("/dev/uinput",O_RDWR|O_NONBLOCK); if(fd<0){perror("open uinput");return 2;}
  g_fd=fd;
  ioctl(fd,UI_SET_EVBIT,EV_KEY);
  int keys[]={BTN_SOUTH,BTN_EAST,BTN_NORTH,BTN_WEST,BTN_TL,BTN_TR,BTN_SELECT,BTN_START,BTN_MODE,BTN_THUMBL,BTN_THUMBR};
  for(unsigned i=0;i<sizeof keys/sizeof*keys;i++) ioctl(fd,UI_SET_KEYBIT,keys[i]);
  ioctl(fd,UI_SET_EVBIT,EV_ABS);
  // Full a133-descriptor axis superset: left stick (X/Y), RIGHT stick (RX/RY), triggers
  // (Z/RZ), d-pad hat (HAT0X/Y). The qemu-tsp regression mkuinput omitted RX/RY (it only
  // probed 6 named axes); the a133 descriptor's `rstick` needs them, so the synthesized
  // device must advertise them (asymmetric rule: descriptor codes subset-of advertised).
  int abss[]={ABS_X,ABS_Y,ABS_RX,ABS_RY,ABS_Z,ABS_RZ,ABS_HAT0X,ABS_HAT0Y};
  for(unsigned i=0;i<sizeof abss/sizeof*abss;i++) ioctl(fd,UI_SET_ABSBIT,abss[i]);
  ioctl(fd,UI_SET_EVBIT,EV_SYN);
  struct uinput_user_dev ud; memset(&ud,0,sizeof ud);
  snprintf(ud.name,sizeof ud.name,"TRIMUI Player1");
  ud.id.bustype=BUS_USB; ud.id.vendor=0x045e; ud.id.product=0x028e; ud.id.version=0x0110;
  ud.absmin[ABS_X]=-32768; ud.absmax[ABS_X]=32767; ud.absflat[ABS_X]=128; ud.absfuzz[ABS_X]=16;
  ud.absmin[ABS_Y]=-32768; ud.absmax[ABS_Y]=32767; ud.absflat[ABS_Y]=128; ud.absfuzz[ABS_Y]=16;
  ud.absmin[ABS_RX]=-32768; ud.absmax[ABS_RX]=32767; ud.absflat[ABS_RX]=128; ud.absfuzz[ABS_RX]=16;
  ud.absmin[ABS_RY]=-32768; ud.absmax[ABS_RY]=32767; ud.absflat[ABS_RY]=128; ud.absfuzz[ABS_RY]=16;
  ud.absmin[ABS_Z]=0; ud.absmax[ABS_Z]=255; ud.absmin[ABS_RZ]=0; ud.absmax[ABS_RZ]=255;
  ud.absmin[ABS_HAT0X]=-1; ud.absmax[ABS_HAT0X]=1; ud.absmin[ABS_HAT0Y]=-1; ud.absmax[ABS_HAT0Y]=1;
  if(write(fd,&ud,sizeof ud)<0) perror("write ud");
  if(ioctl(fd,UI_DEV_CREATE)<0) perror("UI_DEV_CREATE");
  signal(SIGUSR1, inject);
  printf("uinput created, sleeping (pid=%d)\n", getpid()); fflush(stdout);
  for(;;) pause();
  return 0;
}
