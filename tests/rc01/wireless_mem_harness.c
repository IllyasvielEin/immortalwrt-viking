#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <errno.h>
struct list_head { struct list_head *next, *prev; };
#define INIT_LIST_HEAD(p) ((p)->next = (p)->prev = (p))
static void list_add_tail(struct list_head *n, struct list_head *h) {
 n->prev=h->prev; n->next=h; h->prev->next=n; h->prev=n;
}
static void list_del(struct list_head *n) { n->prev->next=n->next; n->next->prev=n->prev; }
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define spin_lock(p) assert(++*(p)==1)
#define spin_unlock(p) assert(--*(p)==0)
#define ATH12K_QMI_WLANFW_MAX_NUM_MEM_SEG_V01 8
#define ATH12K_QMI_EVENT_REQUEST_MEM 3
#define ATH12K_FLAG_FIXED_MEM_REGION 0
#define GFP_ATOMIC 0
#define ath12k_dbg(...) ((void)0)
#define ath12k_warn(...) ((void)0)
static int outstanding, fail_alloc, allocations, responses, delayed;
static void *event_alloc(size_t n, int flags) {
 (void)flags; if (fail_alloc) return NULL;
 void *p=calloc(1,n); if (p) outstanding++; return p;
}
static void event_free(void *p) { if(p) outstanding--; free(p); }
#define kzalloc event_alloc
#define kfree event_free
struct qmi_handle { int unused; };
struct sockaddr_qrtr { int unused; };
struct qmi_txn { int unused; };
struct mem_seg { unsigned type, size; };
struct qmi_wlanfw_request_mem_ind_msg_v01 { unsigned mem_seg_len; struct mem_seg mem_seg[8]; };
enum ath12k_qmi_event_type { unused_event };
struct ath12k_qmi_driver_event { struct list_head list; int type; void *data; };
struct ath12k_base;
struct ath12k_qmi { struct ath12k_base *ab; struct qmi_handle handle;
 unsigned mem_seg_count; struct mem_seg target_mem[8]; bool events_stopped;
 int event_lock, event_wq, event_work; struct list_head event_list; };
struct ath12k_base { struct ath12k_qmi qmi; unsigned long dev_flags; };
#define test_bit(n,p) (!!(*(p)&(1UL<<(n))))
static int queue_work(int wq, int *work) { (void)wq; (void)work; return 1; }
static unsigned seen[8], seen_size[8];
static int ath12k_qmi_alloc_target_mem_chunk(struct ath12k_base *ab) {
 allocations++; if (delayed) { ab->qmi.mem_seg_count=0; return 0; }
 return 0;
}
#define ath12k_qmi_assign_target_mem_chunk ath12k_qmi_alloc_target_mem_chunk
static int ath12k_qmi_respond_fw_mem_request(struct ath12k_base *ab) {
 seen[responses]=ab->qmi.mem_seg_count;
 seen_size[responses++]=ab->qmi.target_mem[0].size; return 0;
}
#include "mem-functions.inc"
static void consume(struct ath12k_qmi *q) {
 while(q->event_list.next!=&q->event_list) {
  struct ath12k_qmi_driver_event *e=container_of(q->event_list.next,struct ath12k_qmi_driver_event,list);
  list_del(&e->list);
  if (!q->events_stopped) assert(!ath12k_qmi_event_mem_request(q,e->data));
  kfree(e);
 }
}
int main(void) {
 struct ath12k_base ab={}; struct ath12k_qmi *q=&ab.qmi;
 struct qmi_wlanfw_request_mem_ind_msg_v01 msg={.mem_seg_len=1,.mem_seg={{.type=7,.size=100}}};
 q->ab=&ab; INIT_LIST_HEAD(&q->event_list);
 ath12k_qmi_msg_mem_request_cb(&q->handle,0,0,&msg);
 msg.mem_seg[0].size=200;
 ath12k_qmi_msg_mem_request_cb(&q->handle,0,0,&msg);
 assert(outstanding==2 && !q->mem_seg_count && !allocations && !responses);
 msg.mem_seg[0].size=999;
 consume(q);
 assert(!outstanding && allocations==2 && responses==2);
 assert(seen_size[0]==100 && seen_size[1]==200);
 delayed=1;
 ath12k_qmi_msg_mem_request_cb(&q->handle,0,0,&msg); consume(q);
 assert(seen[2]==0 && responses==3);
 fail_alloc=1;
 ath12k_qmi_msg_mem_request_cb(&q->handle,0,0,&msg);
 assert(!outstanding && allocations==3); fail_alloc=0;
 ath12k_qmi_msg_mem_request_cb(&q->handle,0,0,&msg);
 q->events_stopped=true;
 ath12k_qmi_msg_mem_request_cb(&q->handle,0,0,&msg);
 assert(outstanding==1); consume(q);
 assert(!outstanding && allocations==3 && responses==3);
 q->events_stopped=false; msg.mem_seg_len=9;
 ath12k_qmi_msg_mem_request_cb(&q->handle,0,0,&msg);
 assert(!outstanding);
}
