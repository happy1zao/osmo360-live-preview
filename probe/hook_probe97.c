/*
 * hook_probe97.c —— 零落盘 + 2:1 代理流真写进内存
 *
 * 事实依据（本轮 A/B/C 对照）：
 *   probe79(空壳) / probe80(纯假写)            → 稳定长录
 *   probe93(LRF 真写 /dev/shm)                → ~1.7s 被掐
 *   probe95(OSV+LRF 都在 /dev/shm, 同 st_dev)   → ~1.7s 仍被掐
 *   probe96(同 probe93 + proxy tee)            → 1.7s 被掐，但 proxy_*.bin 正常产出（tee 有效）
 *   结论：录制会话存活的前提是「所有录制文件都表现为写入成功」，
 *         一旦 LRF 真的落到任何地方，会话约 1.7s 必死。
 *
 * 所以 probe97 = 全部假写（含 LRF）+ 只改 proxy 管线的 tee：
 *   .OSV/.MET/.trinf/.LRF → /data/local/tmp/spool 假写（数据丢弃，只维护逻辑大小）
 *   2:1 代理流从 proxy 编码器出口 tee 出去（h264parse → AnnexB → TCP 8888）
 * fallocate/fsync 一律吞掉，ftruncate 只影响逻辑大小，避免预分配 ENOSPC 掐会话。
 */
#include <stdarg.h>

typedef unsigned int mode_t;
#define O_CREAT 0100
void *dlsym(void *handle, const char *symbol);
#define RTLD_NEXT ((void *)-1)

static inline long sc6(long n, long a, long b, long c, long d, long e, long f) {
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    register long x3 __asm__("x3") = d;
    register long x4 __asm__("x4") = e;
    register long x5 __asm__("x5") = f;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5) : "memory");
    return x0;
}
static inline long sc4(long n, long a, long b, long c, long d) { return sc6(n, a, b, c, d, 0, 0); }

#define SYS_openat    56
#define SYS_close     57
#define SYS_write     64
#define SYS_getpid    172
#define SYS_mkdirat   34
#define SYS_ftruncate 46

#define O_WR 1
#define O_CR 64
#define O_AP 1024

static long k_open(const char *p, long f) { return sc4(SYS_openat, -100, (long)p, f, 0644); }
static void k_mkdir(const char *p) { sc4(SYS_mkdirat, -100, (long)p, 0777, 0); }
static void alog(const char *path, const char *buf, long len) {
    long fd = k_open(path, O_WR | O_CR | O_AP);
    if (fd < 0) return;
    sc4(SYS_write, fd, (long)buf, len, 0);
    sc4(SYS_close, fd, 0, 0, 0);
}
static void alogn(const char *path, const char *buf) { long n = 0; while (buf[n]) n++; alog(path, buf, n); }
static int contains(const char *s, const char *pat) {
    long i = 0, j;
    while (s[i]) { j = 0; while (pat[j] && s[i + j] == pat[j]) j++; if (!pat[j]) return 1; i++; }
    return 0;
}
static int endswith(const char *s, const char *suf) {
    long n = 0, m = 0;
    while (s[n]) n++; while (suf[m]) m++;
    if (m > n) return 0;
    for (long i = 0; i < m; i++) if (s[n - m + i] != suf[i]) return 0;
    return 1;
}

#define LOG "/dev/shm/probe97.log"

static int is_rec_file(const char *p) {
    if (!contains(p, "/DCIM/")) return 0;
    return endswith(p, ".OSV") || endswith(p, ".LRF") || endswith(p, ".MET") || endswith(p, ".trinf");
}

static char g_pb[320];
static const char *remap_dir(const char *p, const char *pfx) {
    long n = 0; while (p[n]) n++;
    long s = n; while (s > 0 && p[s - 1] != '/') s--;
    long k = 0;
    for (long i = 0; pfx[i]; i++) g_pb[k++] = pfx[i];
    for (long i = s; i < n && k < 300; i++) g_pb[k++] = p[i];
    g_pb[k] = 0;
    return g_pb;
}
static const char *remap(const char *p) { return remap_dir(p, "/data/local/tmp/spool/"); }

#define MAXFD 96
/* kind: 0=空 1=假写（丢弃） */
static int g_fd[MAXFD];
static int g_kind[MAXFD];
static long g_vs[MAXFD], g_fl[MAXFD];
static int slot_find(int fd) { for (int i = 0; i < MAXFD; i++) if (fd != 0 && g_fd[i] == fd) return i; return -1; }
static int slot_new(int fd, int kind) { for (int i = 0; i < MAXFD; i++) if (g_fd[i] == 0) { g_fd[i] = fd; g_kind[i] = kind; g_vs[i] = 0; g_fl[i] = 0; return i; } return -1; }
static void slot_del(int fd) { int i = slot_find(fd); if (i >= 0) { g_fd[i] = 0; g_kind[i] = 0; g_vs[i] = 0; g_fl[i] = 0; } }
static void grow(int fd, long vs) { sc4(SYS_ftruncate, fd, vs, 0, 0); }
static void grow_slot(int i, int fd) { if (g_vs[i] - g_fl[i] >= (1L << 20)) { grow(fd, g_vs[i]); g_fl[i] = g_vs[i]; } }

typedef int (*fn_open)(const char *, int, ...);
typedef int (*fn_openat)(int, const char *, int, ...);
static fn_open r_open, r_open64;
static fn_openat r_openat, r_openat64;
typedef long (*fn_write)(int, const void *, unsigned long);
typedef long (*fn_pwrite)(int, const void *, unsigned long, long long);
static fn_write r_w;
static fn_pwrite r_pw;

static int handle_open(const char *path, int flags, mode_t mode, int dirfd, int use_at, int use_at64) {
    if (is_rec_file(path)) {
        const char *np = remap(path);
        int fd;
        if (use_at) { if (!r_openat) r_openat = (fn_openat)dlsym(RTLD_NEXT, "openat"); fd = r_openat(dirfd, np, flags, mode); }
        else if (use_at64) { if (!r_openat64) r_openat64 = (fn_openat)dlsym(RTLD_NEXT, "openat64"); fd = r_openat64(dirfd, np, flags, mode); }
        else { if (!r_open) r_open = (fn_open)dlsym(RTLD_NEXT, "open"); fd = r_open(np, flags, mode); }
        if (fd >= 0) { slot_new(fd, 1); alogn(LOG, "fake "); alogn(LOG, np); alogn(LOG, "\n"); }
        return fd;
    }
    if (use_at) { if (!r_openat) r_openat = (fn_openat)dlsym(RTLD_NEXT, "openat"); return r_openat(dirfd, path, flags, mode); }
    if (use_at64) { if (!r_openat64) r_openat64 = (fn_openat)dlsym(RTLD_NEXT, "openat64"); return r_openat64(dirfd, path, flags, mode); }
    if (!r_open) r_open = (fn_open)dlsym(RTLD_NEXT, "open");
    return r_open(path, flags, mode);
}

int open(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    return handle_open(path, flags, mode, -100, 0, 0);
}
int open64(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    mode_t m = mode;
    if (is_rec_file(path)) {
        const char *np = remap(path);
        if (!r_open64) r_open64 = (fn_open)dlsym(RTLD_NEXT, "open64");
        int fd = r_open64(np, flags, m);
        if (fd >= 0) { slot_new(fd, 1); alogn(LOG, "fake "); alogn(LOG, np); alogn(LOG, "\n"); }
        return fd;
    }
    if (!r_open64) r_open64 = (fn_open)dlsym(RTLD_NEXT, "open64");
    return r_open64(path, flags, m);
}
int openat(int dirfd, const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    return handle_open(path, flags, mode, dirfd, 1, 0);
}
int openat64(int dirfd, const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    return handle_open(path, flags, mode, dirfd, 0, 1);
}

int fallocate(int fd, int mode, long long off, long long len) {
    if (slot_find(fd) >= 0) return 0;
    typedef int (*fn)(int, int, long long, long long);
    fn f = (fn)dlsym(RTLD_NEXT, "fallocate");
    return f ? f(fd, mode, off, len) : 0;
}
int fallocate64(int fd, int mode, long long off, long long len) {
    if (slot_find(fd) >= 0) return 0;
    typedef int (*fn)(int, int, long long, long long);
    fn f = (fn)dlsym(RTLD_NEXT, "fallocate64");
    return f ? f(fd, mode, off, len) : 0;
}
long posix_fallocate(int fd, long off, long len) {
    if (slot_find(fd) >= 0) return 0;
    typedef long (*fn)(int, long, long);
    fn f = (fn)dlsym(RTLD_NEXT, "posix_fallocate");
    return f ? f(fd, off, len) : 0;
}
long posix_fallocate64(int fd, long long off, long long len) {
    if (slot_find(fd) >= 0) return 0;
    typedef long (*fn)(int, long long, long long);
    fn f = (fn)dlsym(RTLD_NEXT, "posix_fallocate64");
    return f ? f(fd, off, len) : 0;
}
long write(int fd, const void *buf, unsigned long n) {
    int i = slot_find(fd);
    if (i >= 0 && g_kind[i] == 1) { g_vs[i] += (long)n; grow_slot(i, fd); return (long)n; }
    if (!r_w) r_w = (fn_write)dlsym(RTLD_NEXT, "write");
    long rr = r_w(fd, buf, n);
    if (i >= 0) g_vs[i] += (long)n;
    return rr;
}
long pwrite(int fd, const void *buf, unsigned long n, long long off) {
    int i = slot_find(fd);
    if (i >= 0 && g_kind[i] == 1) { long long e = off + (long long)n; if (e > g_vs[i]) g_vs[i] = e; grow_slot(i, fd); return (long)n; }
    if (!r_pw) r_pw = (fn_pwrite)dlsym(RTLD_NEXT, "pwrite");
    long rr = r_pw(fd, buf, n, off);
    if (i >= 0) { long long e = off + (long long)n; if (e > g_vs[i]) g_vs[i] = e; }
    return rr;
}
long pwrite64(int fd, const void *buf, unsigned long n, long long off) { return pwrite(fd, buf, n, off); }

int ftruncate(int fd, long long len) {
    int i = slot_find(fd);
    if (i >= 0) { if (g_kind[i] == 1) { g_vs[i] = len; grow(fd, len); } return 0; }
    typedef int (*fn)(int, long long);
    fn f = (fn)dlsym(RTLD_NEXT, "ftruncate");
    return f ? f(fd, len) : 0;
}
int ftruncate64(int fd, long long len) { return ftruncate(fd, len); }

int fsync(int fd) {
    if (slot_find(fd) >= 0) return 0;
    typedef int (*fn)(int);
    fn f = (fn)dlsym(RTLD_NEXT, "fsync");
    return f ? f(fd) : 0;
}
int fdatasync(int fd) {
    if (slot_find(fd) >= 0) return 0;
    typedef int (*fn)(int);
    fn f = (fn)dlsym(RTLD_NEXT, "fdatasync");
    return f ? f(fd) : 0;
}
int close(int fd) {
    int i = slot_find(fd);
    if (i >= 0) { if (g_kind[i] == 1) grow(fd, g_vs[i]); slot_del(fd); }
    return (int)sc4(SYS_close, fd, 0, 0, 0);
}

/* ---------- proxy 管线改写：h264parse 转 AnnexB + tcpserversink ----------
 * 只认 desc 里带 proxy_yuv_src 的那条（PARSE #9），其它管线原样放行，最小侵入。
 * 编码器出口原本是 AVCC（长度前缀），ffmpeg 直接读 tcp 认不出来；
 * 串一个 h264parse config-interval=-1 就变成带 SPS/PPS 的 AnnexB 字节流。
 * 原 proxy_sink 的 appsink 分支保留，落盘行为不变（配合上面的零写盘层即为「不落卡」）。
 */
typedef void *(*fn_parse)(const char *, void *);
static fn_parse r_parse;
static int g_proxy_rw;

static const char PROXY_NEW[] =
    "appsrc name=proxy_yuv_src ! djicamc2venc name=proxy_encoder priority=0 enable-camera-memory-heap=1 "
    "! capsfilter name=proxy_enc_config ! tee name=pte "
    "pte. ! queue max-size-buffers=6 leaky=2 ! h264parse config-interval=-1 "
    "! video/x-h264,stream-format=byte-stream,alignment=au "
    "! tcpserversink host=127.0.0.1 port=8888 sync=false "
    "pte. ! queue max-size-buffers=4 leaky=2 "
    "! multifilesink location=/dev/shm/dcam_rec/proxy_%05d.bin max-files=24 sync=false "
    "pte. ! queue ! appsink name=proxy_sink emit-signals=true sync=false async=false enable-last-sample=false";

__attribute__((visibility("default"), used))
void *gst_parse_launch(const char *desc, void *err) {
    if (!r_parse) r_parse = (fn_parse)dlsym(RTLD_NEXT, "gst_parse_launch");
    if (!r_parse) return 0;
    if (desc && contains(desc, "proxy_yuv_src")) {
        if (!g_proxy_rw) { alogn(LOG, "rw:proxy97\n"); g_proxy_rw = 1; }
        return r_parse(PROXY_NEW, err);
    }
    return r_parse(desc, err);
}

__attribute__((constructor)) static void init_hook(void) {
    k_mkdir("/dev/shm");
    k_mkdir("/data/local/tmp");
    k_mkdir("/data/local/tmp/spool");
    k_mkdir("/dev/shm/dcam_rec");
    long fd = k_open("/dev/shm/dcam_hook_alive.txt", O_WR | O_CR | O_AP);
    if (fd >= 0) {
        static const char tag[] = "probe97(camera3) loaded pid=";
        sc4(SYS_write, fd, (long)tag, sizeof(tag) - 1, 0);
        char b[16]; int i = 0, j;
        long pid = sc4(SYS_getpid, 0, 0, 0, 0);
        if (pid == 0) b[i++] = '0';
        else { char t[16]; j = 0; while (pid) { t[j++] = (char)('0' + pid % 10); pid /= 10; } while (j) b[i++] = t[--j]; }
        sc4(SYS_write, fd, (long)b, i, 0);
        sc4(SYS_write, fd, (long)"\n", 1, 0);
        sc4(SYS_close, fd, 0, 0, 0);
    }
}
