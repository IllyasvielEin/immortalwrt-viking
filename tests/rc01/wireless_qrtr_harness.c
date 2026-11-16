#define ATH12K_QMI_EVENT_FW_MEM_READY 4
#define ATH12K_QMI_EVENT_FW_READY 5
#define ATH12K_QMI_EVENT_HOST_CAP 6
#define ATH12K_FLAG_UNREGISTERING 4
#define ATH12K_FLAG_QMI_FAIL 5
#define ATH12K_FLAG_CRASH_FLUSH 6
#define ATH12K_FLAG_QMI_FW_READY_COMPLETE 7
#define U8_MAX 255
#define READ_ONCE(x) (x)
#define complete(p) (++*(p))
#define set_bit(n,p) (*(p)|=1UL<<(n))
#define clear_bit(n,p) (*(p)&=~(1UL<<(n)))
#define list_empty(p) ((p)->next==(p))
#define list_first_entry(p,t,m) container_of((p)->next,t,m)
static int ath12k_qmi_ops_new_server(struct qmi_handle *,struct qmi_service *);
static int arrives, exits, ready, inject;
static void ath12k_core_queue_reset(struct ath12k_base *ab) { (void)ab; }
static struct qmi_service injected={.node=50,.port=60};
static int ath12k_qmi_event_server_arrive(struct ath12k_qmi *q) {
 unsigned node=q->sq.sq_node;arrives++;
 if(inject) {
  inject=0;assert(!ath12k_qmi_ops_new_server(&q->handle,&injected));
  /* A callback must not reconnect/change the active transaction peer. */
  assert(q->sq.sq_node==node);return -ENOMEM;
 }
 return node==10?-ECONNRESET:0;
}
static int ath12k_qmi_event_load_bdf(struct ath12k_qmi *q) { (void)q;return 0; }
static int ath12k_qmi_event_host_cap(struct ath12k_qmi *q) { (void)q;return 0; }
static int ath12k_core_qmi_firmware_ready(struct ath12k_base *ab) { (void)ab;ready++;return 0; }
#include "qrtr-functions.inc"
static void run(struct ath12k_qmi *q) { ath12k_qmi_driver_event_work(&q->event_work); }
static void clear_service(struct ath12k_qmi *q,struct qmi_service *s) { if(s->priv) ath12k_qmi_ops_del_server(&q->handle,s); }
int main(void) {
 struct ath12k_hw_group ag={};struct ath12k_base ab={.ag=&ag};struct ath12k_qmi *q=&ab.qmi;
 struct qmi_service old={.node=1,.port=2},new={.node=3,.port=4},duplicate={.node=3,.port=4};
 q->ab=&ab;INIT_LIST_HEAD(&q->event_list);
 assert(!ath12k_qmi_ops_new_server(&q->handle,&old));
 assert(!ath12k_qmi_ops_new_server(&q->handle,&new));
 u64 gen=q->server_gen;
 assert(!ath12k_qmi_ops_new_server(&q->handle,&duplicate));assert(q->server_gen==gen);
 ath12k_qmi_ops_del_server(&q->handle,&old);
 assert(q->server_present && ath12k_qmi_generation_current(q,gen));
 run(q);assert(arrives==1 && q->sq.sq_node==3 && !recovery_error);
 /* Deleting the two matching lookup objects carries their actual generation. */
 clear_service(q,&duplicate);clear_service(q,&new);run(q);assert(!q->server_present);
 struct qmi_service stale={.node=10,.port=20},real={.node=11,.port=21};
 assert(!ath12k_qmi_ops_new_server(&q->handle,&stale));run(q);
 assert(test_bit(ATH12K_FLAG_QMI_FAIL,&ab.dev_flags));
 clear_service(q,&stale);
 assert(!ath12k_qmi_ops_new_server(&q->handle,&real));run(q);
 assert(!test_bit(ATH12K_FLAG_QMI_FAIL,&ab.dev_flags) && q->sq.sq_node==11);
 /* In-flight obsolete ENOMEM cannot poison a newer lookup or change peer. */
 clear_service(q,&real);run(q);
 struct qmi_service in_flight={.node=30,.port=40};inject=1;
 assert(!ath12k_qmi_ops_new_server(&q->handle,&in_flight));run(q);
 assert(q->sq.sq_node==50 && !recovery_error && !test_bit(ATH12K_FLAG_QMI_FAIL,&ab.dev_flags));
 clear_service(q,&in_flight);run(q);assert(q->server_present);
 struct sockaddr_qrtr obsolete={.sq_node=30,.sq_port=40};assert(!ath12k_qmi_indication_current(q,&obsolete));
 q->events_stopped=true;clear_service(q,&injected);run(q);
 assert(!outstanding && list_empty(&q->event_list));
 (void)exits;
}
