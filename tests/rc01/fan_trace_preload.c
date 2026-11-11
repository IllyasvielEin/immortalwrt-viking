// SPDX-License-Identifier: GPL-2.0-only
// Test-only LD_PRELOAD tracer for rc01-fan-guard fixtures. It appends one
// line per event to $RC01_TRACE_LOG, in the order the events happen across
// all processes (O_APPEND writes):
//   read <path>            open for reading of a path under $RC01_TRACE_SYS
//   pwm-open-failed <pid>  open for writing of $RC01_TRACE_PWM failed
//   pwm <pid> <value>      a writer of $RC01_TRACE_PWM released it; the value
//                          is the file content at that moment
// stdio writes inside libc cannot be intercepted, so a PWM write is recorded
// when the last descriptor referring to it is closed or replaced.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#define MAX_FD 1024
static unsigned char pwm_fd[MAX_FD];

static void emit(const char *fmt, ...)
{
	const char *log = getenv("RC01_TRACE_LOG");
	char line[512];
	va_list ap;
	int saved = errno, fd, n;
	if (!log) return;
	va_start(ap, fmt);
	n = vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	fd = syscall(SYS_openat, AT_FDCWD, log, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0644);
	if (fd >= 0) {
		syscall(SYS_write, fd, line, n < (int)sizeof(line) ? n : (int)sizeof(line) - 1);
		syscall(SYS_close, fd);
	}
	errno = saved;
}

static int pwm_refs(void)
{
	int n = 0;
	for (int i = 0; i < MAX_FD; i++) n += pwm_fd[i];
	return n;
}

static void release(int fd)
{
	const char *path = getenv("RC01_TRACE_PWM");
	char value[64] = "";
	int file, n;
	if (fd < 0 || fd >= MAX_FD || !pwm_fd[fd]) return;
	pwm_fd[fd] = 0;
	if (pwm_refs()) return;
	file = syscall(SYS_openat, AT_FDCWD, path, O_RDONLY | O_CLOEXEC);
	if (file >= 0) {
		n = syscall(SYS_read, file, value, sizeof(value) - 1);
		value[n > 0 ? n : 0] = 0;
		syscall(SYS_close, file);
	}
	value[strcspn(value, "\n")] = 0;
	emit("pwm %d %s\n", (int)getpid(), value);
}

static void opened(const char *path, int flags, int fd)
{
	const char *pwm = getenv("RC01_TRACE_PWM"), *sys = getenv("RC01_TRACE_SYS");
	int writing = (flags & O_ACCMODE) != O_RDONLY;
	if (!path) return;
	if (pwm && writing && !strcmp(path, pwm)) {
		if (fd < 0) emit("pwm-open-failed %d\n", (int)getpid());
		else if (fd < MAX_FD) pwm_fd[fd] = 1;
	} else if (sys && !writing && fd >= 0 && !strncmp(path, sys, strlen(sys))) {
		emit("read %s\n", path + strlen(sys));
	}
}

#define REAL(name, ...) __VA_ARGS__ = dlsym(RTLD_NEXT, #name)

static int do_open(const char *sym, int dirfd, const char *path, int flags, mode_t mode)
{
	typedef int (*open_t)(const char *, int, ...);
	typedef int (*openat_t)(int, const char *, int, ...);
	int fd;
	if (dirfd == -1000) {
		open_t f = (open_t)dlsym(RTLD_NEXT, sym);
		fd = f(path, flags, mode);
	} else {
		openat_t f = (openat_t)dlsym(RTLD_NEXT, sym);
		fd = f(dirfd, path, flags, mode);
	}
	int saved = errno;
	opened(path, flags, fd);
	errno = saved;
	return fd;
}

#define MODE_ARG mode_t mode = 0; if (flags & (O_CREAT | O_TMPFILE)) { va_list ap; va_start(ap, flags); mode = va_arg(ap, int); va_end(ap); }
int open(const char *path, int flags, ...) { MODE_ARG return do_open("open", -1000, path, flags, mode); }
int open64(const char *path, int flags, ...) { MODE_ARG return do_open("open64", -1000, path, flags, mode); }
int openat(int d, const char *path, int flags, ...) { MODE_ARG return do_open("openat", d, path, flags, mode); }
int openat64(int d, const char *path, int flags, ...) { MODE_ARG return do_open("openat64", d, path, flags, mode); }

int close(int fd)
{
	REAL(close, int (*real_close)(int));
	int r = real_close(fd), saved = errno;
	if (r == 0) release(fd);
	errno = saved;
	return r;
}

static void duplicated(int old, int new)
{
	if (new < 0 || new >= MAX_FD || new == old) return;
	if (pwm_fd[new]) release(new);
	if (old >= 0 && old < MAX_FD && pwm_fd[old]) pwm_fd[new] = 1;
}

int dup2(int old, int new)
{
	REAL(dup2, int (*real_dup2)(int, int));
	int r = real_dup2(old, new), saved = errno;
	if (r >= 0) duplicated(old, r);
	errno = saved;
	return r;
}

int dup3(int old, int new, int flags)
{
	REAL(dup3, int (*real_dup3)(int, int, int));
	int r = real_dup3(old, new, flags), saved = errno;
	if (r >= 0) duplicated(old, r);
	errno = saved;
	return r;
}

int dup(int old)
{
	REAL(dup, int (*real_dup)(int));
	int r = real_dup(old), saved = errno;
	if (r >= 0) duplicated(old, r);
	errno = saved;
	return r;
}

static int do_fcntl(const char *sym, int fd, int cmd, long arg)
{
	int (*f)(int, int, ...) = (int (*)(int, int, ...))dlsym(RTLD_NEXT, sym);
	int r = f(fd, cmd, arg), saved = errno;
	if (r >= 0 && (cmd == F_DUPFD || cmd == F_DUPFD_CLOEXEC)) duplicated(fd, r);
	errno = saved;
	return r;
}

int fcntl(int fd, int cmd, ...) { va_list ap; va_start(ap, cmd); long a = va_arg(ap, long); va_end(ap); return do_fcntl("fcntl", fd, cmd, a); }
int fcntl64(int fd, int cmd, ...) { va_list ap; va_start(ap, cmd); long a = va_arg(ap, long); va_end(ap); return do_fcntl("fcntl64", fd, cmd, a); }
