/* Exercise real, patched dnsmasq sources; only logging/exit and OOM are stubbed. */
#include "dnsmasq.h"
#include <assert.h>
#include <sys/wait.h>

/* Standard DNS RR type numbers absent from the minimal dnsmasq header. */
enum { FIXTURE_T_NULL = 10, FIXTURE_T_SVCB = 64 };

static struct daemon state;
struct daemon *daemon = &state;
static int fail_blocks, allocation_failures;

void *__real_whine_malloc_real(const char *, unsigned int, size_t);
void *__wrap_whine_malloc_real(const char *func, unsigned int line, size_t size)
{
  if (fail_blocks && strcmp(func, "add_blocks") == 0)
    {
      allocation_failures++;
      return NULL;
    }
  return __real_whine_malloc_real(func, line, size);
}

void my_syslog(int priority, const char *format, ...)
{
  (void)priority;
  (void)format;
}

void die(char *message, char *arg, int code)
{
  fprintf(stderr, "%s: %s (%d)\n", message, arg ? arg : "", code);
  exit(1);
}

static void check_data(struct blockdata *block, const char *data, size_t len)
{
  char result[4 * KEYBLOCK_LEN];
  assert(block && len <= sizeof(result));
  memset(result, 0, sizeof(result));
  assert(blockdata_retrieve(block, len, result) == result);
  assert(memcmp(result, data, len) == 0);
}

/* Real pipes, with a trailing marker to detect unread bytes or over-reading. */
static int data_pipe(const char *data, size_t len)
{
  int fds[2];
  assert(pipe(fds) == 0);
  assert(read_write(fds[1], (unsigned char *)data, len, RW_WRITE));
  assert(read_write(fds[1], (unsigned char *)"!", 1, RW_WRITE));
  assert(close(fds[1]) == 0);
  return fds[0];
}

static void check_marker(int fd)
{
  unsigned char marker;
  assert(read(fd, &marker, 1) == 1 && marker == '!');
  assert(read(fd, &marker, 1) == 0);
  assert(close(fd) == 0);
}

static void test_blocks(const char *test)
{
  char data[3 * KEYBLOCK_LEN + 7];
  struct blockdata *block;
  size_t i;
  int fd;

  for (i = 0; i < sizeof(data); i++)
    data[i] = (char)(i + 1);

  if (strcmp(test, "empty") == 0)
    {
      block = blockdata_alloc(NULL, 0);
      assert(block && !block->next);
      assert(blockdata_expand(block, 0, data, KEYBLOCK_LEN - 1));
      assert(blockdata_expand(block, KEYBLOCK_LEN - 1, data + KEYBLOCK_LEN - 1,
                             sizeof(data) - KEYBLOCK_LEN + 1));
      check_data(block, data, sizeof(data));
      blockdata_free(block);
    }
  else if (strcmp(test, "positive") == 0)
    {
      block = blockdata_alloc(data, sizeof(data));
      check_data(block, data, sizeof(data));
      blockdata_free(block);
      fd = data_pipe(data, sizeof(data));
      block = blockdata_read(fd, sizeof(data));
      check_data(block, data, sizeof(data));
      check_marker(fd);
      blockdata_free(block);
    }
  else if (strcmp(test, "zero-read") == 0)
    {
      fd = data_pipe(data, 0);
      block = blockdata_read(fd, 0);
      assert(block && !block->next);
      check_marker(fd);
      blockdata_free(block);
    }
  else
    {
      struct blockdata *held[128];
      size_t count = 0;

      /* Exhaust the real free list, then fail only pool replenishment. */
      fail_blocks = 1;
      while ((block = blockdata_alloc(data, 1)))
        {
          assert(count < countof(held));
          held[count++] = block;
        }
      assert(count > 0 && allocation_failures > 0);
      if (strcmp(test, "oom-partial") == 0)
        blockdata_free(held[--count]); /* allow one block before failure */
      else
        assert(strcmp(test, "oom-first") == 0);

      allocation_failures = 0;
      fd = data_pipe(data, sizeof(data));
      assert(blockdata_read(fd, sizeof(data)) == NULL);
      assert(allocation_failures > 0);
      check_marker(fd); /* 009 must still drain every byte on OOM. */
      fail_blocks = 0;
      while (count)
        blockdata_free(held[--count]);
      block = blockdata_alloc(data, sizeof(data));
      check_data(block, data, sizeof(data));
      blockdata_free(block);
    }
}

static unsigned char *put_name(unsigned char *p, const char *name)
{
  char copy[MAXDNAME];
  strcpy(copy, name);
  return do_rfc1035_name(p, copy, NULL);
}

static void test_negative(const char *test)
{
  union { struct dns_header header; unsigned char bytes[512]; } packet = {0};
  struct dns_header *header = &packet.header;
  unsigned char *p = (unsigned char *)(header + 1), *rdlen, *rdata;
  char name[MAXDNAMESTR + 1], workspace[MAXDNAMESTR + 1];
  char query[] = "missing.example.test", zone[] = "example.test";
  time_t now = 1000;
  int nx = strcmp(test, "nxdomain") == 0;
  int unrelated = strcmp(test, "unrelated-soa") == 0;
  unsigned long soa_ttl = nx ? 300 : 30, minimum = 60;
  unsigned long negative_ttl = soa_ttl < minimum ? soa_ttl : minimum;
  struct crec *entry, *soa;

  daemon->cachesize = 32;
  daemon->pipe_to_parent = -1;
  daemon->namebuff = name;
  daemon->workspacename = workspace;
  assert(daemon->neg_ttl == 0 && !option_bool(OPT_NO_NEG));
  cache_init();

  header->hb3 = HB3_QR;
  header->hb4 = HB4_RA | (nx ? NXDOMAIN : NOERROR);
  header->qdcount = htons(1);
  header->nscount = htons(1);
  p = put_name(p, query);
  *p++ = 0;
  PUTSHORT(T_A, p);
  PUTSHORT(C_IN, p);
  p = put_name(p, unrelated ? "ample.test" : zone);
  *p++ = 0;
  PUTSHORT(T_SOA, p);
  PUTSHORT(C_IN, p);
  PUTLONG(soa_ttl, p);
  rdlen = p;
  p += 2;
  rdata = p;
  p = put_name(p, "ns.example.test");
  *p++ = 0;
  p = put_name(p, "hostmaster.example.test");
  *p++ = 0;
  PUTLONG(1, p); /* SERIAL */
  PUTLONG(1200, p); /* REFRESH */
  PUTLONG(180, p); /* RETRY */
  PUTLONG(86400, p); /* EXPIRE */
  PUTLONG(minimum, p);
  PUTSHORT(p - rdata, rdlen);

  /* extract_addresses calls the real static find_soa and commits real cache entries. */
  assert(extract_addresses(header, p - packet.bytes, name, now, NULL, NULL, 0, 0, 0) == 0);
  entry = cache_find_by_name(NULL, query, now, F_IPV4 | F_NXDOMAIN);
  if (unrelated)
    {
      assert(entry == NULL);
      assert(cache_find_by_name(NULL, "ample.test", now, F_RR) == NULL);
      return;
    }

  assert(entry && (entry->flags & F_NEG));
  assert(!(entry->flags & (F_CNAME | F_NO_RR)));
  assert(!!(entry->flags & F_NXDOMAIN) == nx);
  assert(entry->ttd == now + (time_t)negative_ttl);
  assert(entry->addr.rrdata.datalen == strlen("missing."));
  soa = cache_find_by_name(NULL, zone, now, F_RR);
  assert(soa && (soa->flags & F_KEYTAG) && !(soa->flags & F_NEG));
  assert(soa->addr.rrblock.rrtype == T_SOA);
  assert(soa->addr.rrblock.datalen == p - rdata);
  check_data(soa->addr.rrblock.rrdata, (char *)rdata, p - rdata);
  assert(cache_find_by_name(NULL, query, now + negative_ttl, F_IPV4 | F_NXDOMAIN) == NULL);
}

/* Non-address RRs start with an empty block and expand it, like SOA. */
static void test_rr(const char *test)
{
  union { struct dns_header header; unsigned char bytes[512]; } packet = {0};
  struct dns_header *header = &packet.header;
  unsigned char *p = (unsigned char *)(header + 1), *rdlen, *rdata;
  char name[MAXDNAMESTR + 1], workspace[MAXDNAMESTR + 1];
  char query[] = "service.example.test";
  unsigned short type = strcmp(test, "mx") == 0 ? T_MX :
                        strcmp(test, "srv") == 0 ? T_SRV : FIXTURE_T_SVCB;
  struct rrlist cache_rr = { .rr = type, .next = NULL };
  struct crec *entry;
  time_t now = 1000;

  daemon->cachesize = 32;
  daemon->pipe_to_parent = -1;
  daemon->namebuff = name;
  daemon->workspacename = workspace;
  daemon->cache_rr = &cache_rr;
  cache_init();
  header->hb3 = HB3_QR;
  header->hb4 = HB4_RA;
  header->qdcount = htons(1);
  header->ancount = htons(1);
  p = put_name(p, query);
  *p++ = 0;
  PUTSHORT(type, p);
  PUTSHORT(C_IN, p);
  p = put_name(p, query);
  *p++ = 0;
  PUTSHORT(type, p);
  PUTSHORT(C_IN, p);
  PUTLONG(120, p);
  rdlen = p;
  p += 2;
  rdata = p;
  PUTSHORT(1, p); /* MX preference / SRV priority / SVCB priority */
  if (type == T_SRV)
    {
      PUTSHORT(5, p);
      PUTSHORT(443, p);
    }
  p = put_name(p, "target.example.test");
  *p++ = 0;
  PUTSHORT(p - rdata, rdlen);
  assert(extract_addresses(header, p - packet.bytes, name, now, NULL, NULL, 0, 0, 0) == 0);
  entry = cache_find_by_name(NULL, query, now, F_RR);
  assert(entry && (entry->flags & F_KEYTAG) && !(entry->flags & F_NEG));
  assert(entry->addr.rrblock.rrtype == type);
  assert(entry->addr.rrblock.datalen == p - rdata);
  check_data(entry->addr.rrblock.rrdata, (char *)rdata, p - rdata);
  assert(entry->ttd == now + 120);
  assert(cache_find_by_name(NULL, query, now + 120, F_RR) == NULL);
}

/* Use real child-to-parent marshalling, including a zero-byte block RR. */
static void test_empty_rr_ipc(void)
{
  int fds[2], status;
  pid_t child;
  char name[MAXDNAMESTR + 1], workspace[MAXDNAMESTR + 1];
  char query[] = "empty.example.test";
  time_t now = 1000;
  struct crec *entry;

  daemon->cachesize = 32;
  daemon->pipe_to_parent = -1;
  daemon->namebuff = name;
  daemon->workspacename = workspace;
  cache_init();
  assert(pipe(fds) == 0);
  assert((child = fork()) >= 0);
  if (child == 0)
    {
      union all_addr addr = {0};
      assert(close(fds[0]) == 0);
      daemon->pipe_to_parent = fds[1];
      addr.rrblock.rrtype = FIXTURE_T_NULL;
      addr.rrblock.datalen = 0;
      addr.rrblock.rrdata = blockdata_alloc(NULL, 0);
      assert(addr.rrblock.rrdata);
      cache_start_insert();
      assert(cache_insert(query, &addr, C_IN, now, 60, F_FORWARD | F_RR | F_KEYTAG));
      cache_end_insert();
      assert(close(fds[1]) == 0);
      _exit(0);
    }
  assert(close(fds[1]) == 0);
  assert(cache_recv_insert(now, fds[0]) == 1);
  assert(cache_recv_insert(now, fds[0]) == 0);
  assert(close(fds[0]) == 0);
  assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
  entry = cache_find_by_name(NULL, query, now, F_RR);
  assert(entry && (entry->flags & F_KEYTAG));
  assert(entry->addr.rrblock.rrtype == FIXTURE_T_NULL);
  assert(entry->addr.rrblock.datalen == 0 && entry->addr.rrblock.rrdata);
  assert(entry->ttd == now + 60);
  assert(cache_find_by_name(NULL, query, now + 60, F_RR) == NULL);
}

int main(int argc, char **argv)
{
  assert(argc == 2);
  blockdata_init();
  if (strcmp(argv[1], "nxdomain") == 0 || strcmp(argv[1], "nodata") == 0 ||
      strcmp(argv[1], "unrelated-soa") == 0)
    test_negative(argv[1]);
  else if (strcmp(argv[1], "mx") == 0 || strcmp(argv[1], "srv") == 0 || strcmp(argv[1], "svcb") == 0)
    test_rr(argv[1]);
  else if (strcmp(argv[1], "empty-rr-ipc") == 0)
    test_empty_rr_ipc();
  else
    test_blocks(argv[1]);
  return 0;
}
