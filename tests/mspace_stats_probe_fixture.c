#include "shared.h"
#include "memory_stats_probe.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned create_calls, delete_calls;
static int fail_create_at = -1, fail_lock, fail_unlock;
static unsigned raw_calls;
static unsigned incomplete_stats;
static pthread_mutex_t barrier_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t barrier_cond = PTHREAD_COND_INITIALIZER;
static int block_alloc, alloc_entered, release_alloc, query_waiting, query_done;
static _Thread_local int query_thread;

int sceKernelCreateLwMutex(SceKernelLwMutexWork *mutex, const char *name, unsigned attr, int count, void *opts) {
	(void)name; (void)attr; (void)count; (void)opts;
	if ((int)create_calls++ == fail_create_at) return -1;
	return pthread_mutex_init(mutex, NULL);
}
int sceKernelDeleteLwMutex(SceKernelLwMutexWork *mutex) {
	++delete_calls;
	return pthread_mutex_destroy(mutex);
}
int sceKernelLockLwMutex(SceKernelLwMutexWork *mutex, int count, void *timeout) {
	(void)count; (void)timeout;
	if (fail_lock) return -1;
	if (query_thread) {
		pthread_mutex_lock(&barrier_mutex);
		query_waiting = 1;
		pthread_cond_broadcast(&barrier_cond);
		pthread_mutex_unlock(&barrier_mutex);
	}
	return pthread_mutex_lock(mutex);
}
int sceKernelUnlockLwMutex(SceKernelLwMutexWork *mutex, int count) {
	(void)count;
	int result = pthread_mutex_unlock(mutex);
	return fail_unlock ? -1 : result;
}

static FakeSpace spaces[4] = {
	{{1000, 1000, 20, 20}}, {{2000, 2000, 30, 30}},
	{{3000, 3000, 40, 40}}, {{4000, 4000, 50, 50}}
};
static void *mempool_mspace[4] = {&spaces[0], &spaces[1], &spaces[2], &spaces[3]};
static size_t mempool_size[5] = {1000, 2000, 3000, 4000, 0};
static int fail_allocation;

void sceClibMspaceMallocStats(SceClibMspace space, SceClibMspaceStats *stats) {
	__atomic_add_fetch(&raw_calls, 1, __ATOMIC_RELAXED);
	if (space && stats && incomplete_stats != 1) {
		uint32_t peak = stats->peak_in_use;
		*stats = ((FakeSpace *)space)->stats;
		if (incomplete_stats == 2) stats->peak_in_use = peak;
	}
}
static void *fake_alloc(SceClibMspace space, size_t bytes) {
	if (block_alloc) {
		pthread_mutex_lock(&barrier_mutex);
		alloc_entered = 1;
		pthread_cond_broadcast(&barrier_cond);
		while (!release_alloc) pthread_cond_wait(&barrier_cond, &barrier_mutex);
		pthread_mutex_unlock(&barrier_mutex);
	}
	if (fail_allocation || !space) return NULL;
	FakeSpace *s = space;
	s->stats.current_in_use += bytes;
	if (s->stats.current_in_use > s->stats.peak_in_use) s->stats.peak_in_use = s->stats.current_in_use;
	return s;
}
void *sceClibMspaceMalloc(SceClibMspace space, size_t size) { return fake_alloc(space, size); }
void *sceClibMspaceCalloc(SceClibMspace space, size_t count, size_t size) { return fake_alloc(space, count * size); }
void *sceClibMspaceMemalign(SceClibMspace space, size_t align, size_t size) { (void)align; return fake_alloc(space, size); }
void *sceClibMspaceRealloc(SceClibMspace space, void *ptr, size_t size) { (void)ptr; return fake_alloc(space, size); }
void sceClibMspaceFree(SceClibMspace space, void *ptr) { (void)ptr; if (space) --((FakeSpace *)space)->stats.current_in_use; }
size_t sceClibMspaceMallocUsableSize(void *ptr) { (void)ptr; return 1; }
static vglMemType vgl_mem_get_type_by_addr(void *ptr) {
	for (unsigned i = 0; i < 4; ++i) if (ptr == &spaces[i]) return (vglMemType)i;
	return VGL_MEM_EXTERNAL;
}

#include "actual_wrappers.inc"

static vglMspaceStatsProbe snapshot(void) {
	vglMspaceStatsProbe value;
	assert(vglGetMspaceStatsProbe(&value, sizeof(value), VGL_MSPACE_STATS_PROBE_ABI_VERSION));
	assert(value.abi_version == 1 && value.struct_size == sizeof(value));
	return value;
}
static void query_pair(void) {
	assert(vgl_mem_get_free_space(VGL_MEM_RAM) == spaces[1].stats.capacity - spaces[1].stats.current_in_use);
	assert(vgl_mem_get_free_space(VGL_MEM_RAM) == spaces[1].stats.capacity - spaces[1].stats.current_in_use);
}
static void *allocation_thread(void *unused) { (void)unused; assert(vgl_malloc(7, VGL_MEM_RAM)); return NULL; }
static void *blocked_query_thread(void *unused) {
	(void)unused; query_thread = 1;
	(void)vgl_mem_get_free_space(VGL_MEM_RAM);
	pthread_mutex_lock(&barrier_mutex);
	query_done = 1;
	pthread_cond_broadcast(&barrier_cond);
	pthread_mutex_unlock(&barrier_mutex);
	return NULL;
}
static void *stress_thread(void *unused) {
	(void)unused;
	for (unsigned i = 0; i < 1000; ++i) {
		assert(vgl_malloc(1, VGL_MEM_VRAM));
		(void)vgl_mem_get_free_space(VGL_MEM_VRAM);
		vgl_free(&spaces[0]);
	}
	return NULL;
}

int main(int argc, char **argv) {
	assert(argc == 2);
	const int off = strcmp(argv[1], "off") == 0;
	const int create_failure = strcmp(argv[1], "create-failure") == 0;
	const int lock_failure = strcmp(argv[1], "lock-failure") == 0;
	const int unlock_failure = strcmp(argv[1], "unlock-failure") == 0;
	vglMspaceStatsProbe poison, preserved;
	memset(&poison, 0xa7, sizeof(poison)); preserved = poison;
	assert(!vglGetMspaceStatsProbe(&poison, sizeof(poison) - 1, 1));
	assert(!memcmp(&poison, &preserved, sizeof(poison)));
	assert(!vglGetMspaceStatsProbe(&poison, sizeof(poison), 2));
	assert(!vglGetMspaceStatsProbe(NULL, sizeof(poison), 1));
	assert(vglSetMspaceStatsProbe(!off));
	assert(!snapshot().initialized);
	if (create_failure) fail_create_at = 2;
	vgl_mspace_stats_probe_initialize();
	assert(!vglSetMspaceStatsProbe(GL_FALSE));
	if (create_failure) {
		assert(create_calls == 3 && delete_calls == 2);
		assert(vgl_malloc(1, VGL_MEM_RAM)); query_pair();
		assert(!vglGetMspaceStatsProbe(&poison, sizeof(poison), 1));
		assert(!memcmp(&poison, &preserved, sizeof(poison)));
		puts("create failure: original allocator/stat calls retained, snapshot rejected"); return 0;
	}
	assert(snapshot().initialized && snapshot().enabled == !off);
	query_pair();
	if (off) {
		assert(create_calls == 0 && raw_calls == 2);
		assert(vgl_malloc(1, VGL_MEM_RAM));
		assert(snapshot().pools[VGL_MEM_RAM].queries == 0);
		assert(snapshot().pools[VGL_MEM_RAM].mutation_attempts == 0);
		puts("OFF: no probe locks/counters; every real stats call retained"); return 0;
	}
	assert(create_calls == 4);
	assert(snapshot().pools[VGL_MEM_RAM].queries == 2);
	assert(snapshot().pools[VGL_MEM_RAM].eligible_hits == 1);
	if (lock_failure || unlock_failure) {
		fail_lock = lock_failure; fail_unlock = unlock_failure;
		assert(vgl_malloc(1, VGL_MEM_RAM)); query_pair();
		assert(!vglGetMspaceStatsProbe(&poison, sizeof(poison), 1));
		assert(!memcmp(&poison, &preserved, sizeof(poison)));
		puts("mutex failure: original calls retained, diagnostic snapshot rejected"); return 0;
	}
	assert(!vgl_mspace_stats_probe_begin_impl((vglMemType)-1));
	assert(!vgl_mspace_stats_probe_begin_impl(VGL_MEM_EXTERNAL));
	assert(!vgl_mspace_stats_probe_begin_impl(VGL_MEM_ALL));
	assert(!vgl_mspace_stats_probe_begin_impl((vglMemType)999));
	unsigned before_raw = raw_calls;
	mempool_size[VGL_MEM_BUDGET] = 0;
	assert(vgl_mem_get_free_space(VGL_MEM_BUDGET) == 0);
	assert(vgl_mem_get_free_space(VGL_MEM_EXTERNAL) == 0);
	assert(raw_calls == before_raw);
	mempool_size[VGL_MEM_BUDGET] = 4000;

	for (unsigned failed = 0; failed < 2; ++failed) {
		fail_allocation = failed;
		for (unsigned op = 0; op < 5; ++op) {
			vglMspaceStatsProbe a = snapshot();
			switch (op) {
			case 0: (void)vgl_malloc(3, VGL_MEM_RAM); break;
			case 1: (void)vgl_calloc(2, 2, VGL_MEM_RAM); break;
			case 2: (void)vgl_memalign(16, 5, VGL_MEM_RAM); break;
			case 3: (void)vgl_realloc(&spaces[1], 6); break;
			case 4: vgl_free(&spaces[1]); break;
			}
			query_pair();
			vglMspaceStatsProbe b = snapshot();
			assert(b.pools[1].mutation_attempts == a.pools[1].mutation_attempts + 1);
			assert(b.pools[1].eligible_hits == a.pools[1].eligible_hits + 1);
			assert(b.pools[1].mismatches == 0);
		}
	}
	fail_allocation = 0;
	before_raw = raw_calls;
	(void)vgl_mem_get_free_space(VGL_MEM_ALL);
	assert(raw_calls == before_raw + 4);
	vgl_mspace_stats_probe_initialize();
	assert(create_calls == 4);
	vglMspaceStatsProbe a = snapshot(); query_pair(); vglMspaceStatsProbe b = snapshot();
	assert(b.pools[1].eligible_hits == a.pools[1].eligible_hits + 1);

	block_alloc = 1;
	pthread_t allocation, query, stress[3];
	assert(!pthread_create(&allocation, NULL, allocation_thread, NULL));
	pthread_mutex_lock(&barrier_mutex);
	while (!alloc_entered) pthread_cond_wait(&barrier_cond, &barrier_mutex);
	pthread_mutex_unlock(&barrier_mutex);
	assert(!pthread_create(&query, NULL, blocked_query_thread, NULL));
	pthread_mutex_lock(&barrier_mutex);
	while (!query_waiting) pthread_cond_wait(&barrier_cond, &barrier_mutex);
	assert(!query_done);
	(void)vgl_mem_get_free_space(VGL_MEM_VRAM);
	release_alloc = 1; pthread_cond_broadcast(&barrier_cond);
	pthread_mutex_unlock(&barrier_mutex);
	pthread_join(allocation, NULL); pthread_join(query, NULL);
	block_alloc = 0;
	assert(query_done);
	for (unsigned i = 0; i < 3; ++i) assert(!pthread_create(&stress[i], NULL, stress_thread, NULL));
	for (unsigned i = 0; i < 3; ++i) pthread_join(stress[i], NULL);
	assert(snapshot().pools[0].mismatches == 0);
	assert(snapshot().pools[1].mismatches == 0);

	uint32_t *field[] = {&spaces[1].stats.capacity,
		&spaces[1].stats.peak_in_use, &spaces[1].stats.current_in_use};
	for (unsigned i = 0; i < 3; ++i) {
		++*field[i];
		spaces[1].stats.unk = spaces[1].stats.capacity;
		(void)vgl_mem_get_free_space(VGL_MEM_RAM);
		assert(snapshot().pools[1].mismatches == i + 1);
	}
	for (unsigned incomplete = 1; incomplete <= 2; ++incomplete) {
		incomplete_stats = incomplete;
		(void)vgl_mem_get_free_space(VGL_MEM_RAM);
	}
	incomplete_stats = 0;
	++spaces[1].stats.unk;
	(void)vgl_mem_get_free_space(VGL_MEM_RAM);
	spaces[1].stats.unk = spaces[1].stats.capacity;
	SceClibMspaceStats invalid = {0};
	vgl_mspace_stats_probe_stats_impl(VGL_MEM_RAM, NULL, &invalid);
	vgl_mspace_stats_probe_stats_impl(VGL_MEM_RAM, &spaces[1], NULL);
	assert(snapshot().pools[1].invalid_queries == 5);
	a = snapshot(); query_pair(); b = snapshot();
	assert(b.pools[1].eligible_hits == a.pools[1].eligible_hits + 1);
	puts("ON: actual wrapper invalidation, full-stat equality, lifecycle, blocked mutation and concurrent queries pass");
	return 0;
}
