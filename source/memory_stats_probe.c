#include "shared.h"
#include "memory_stats_probe.h"

#define VGL_MSPACE_STATS_POOL_COUNT VGL_MEM_EXTERNAL

_Static_assert(VGL_MSPACE_STATS_POOL_COUNT == VGL_MSPACE_STATS_PROBE_POOL_COUNT, "pool ABI changed");
_Static_assert(VGL_MSPACE_STATS_POOL_COUNT == VGL_MSPACE_STATS_CACHE_POOL_COUNT, "cache pool ABI changed");
_Static_assert(sizeof(SceClibMspaceStats) == 16, "native stats layout changed");
_Static_assert(sizeof(vglMspaceStatsCache) == 288, "cache ABI layout changed");
_Static_assert(__atomic_always_lock_free(sizeof(unsigned int), 0), "probe failure flag requires lock-free access");

GLboolean vgl_mspace_stats_tracking_enabled = GL_FALSE;
static GLboolean probe_initialized = GL_FALSE;

#ifndef HAVE_CUSTOM_HEAP
static GLboolean vgl_mspace_stats_probe_enabled = GL_FALSE;
static uint32_t cache_mode = VGL_MSPACE_STATS_CACHE_OFF;
typedef struct {
	SceKernelLwMutexWork mutex;
	SceClibMspaceStats previous;
	vglMspaceStatsProbePool counters;
	vglMspaceStatsCachePool cache_counters;
	SceClibMspace previous_space;
	GLboolean valid;
} MspaceStatsProbePool;

static MspaceStatsProbePool pools[VGL_MSPACE_STATS_POOL_COUNT];
static unsigned int probe_failed = 0;
static unsigned int cache_validation_failed = 0;

static GLboolean probe_lock(vglMemType type) {
	if ((unsigned int)type >= VGL_MSPACE_STATS_POOL_COUNT || !probe_initialized
		|| __atomic_load_n(&probe_failed, __ATOMIC_RELAXED))
		return GL_FALSE;
	if (sceKernelLockLwMutex(&pools[type].mutex, 1, NULL) < 0) {
		__atomic_store_n(&probe_failed, 1, __ATOMIC_RELAXED);
		return GL_FALSE;
	}
	if (__atomic_load_n(&probe_failed, __ATOMIC_RELAXED)) {
		sceKernelUnlockLwMutex(&pools[type].mutex, 1);
		return GL_FALSE;
	}
	return GL_TRUE;
}

static GLboolean probe_unlock(vglMemType type) {
	if (sceKernelUnlockLwMutex(&pools[type].mutex, 1) < 0) {
		__atomic_store_n(&probe_failed, 1, __ATOMIC_RELAXED);
		return GL_FALSE;
	}
	return GL_TRUE;
}
#endif

GLboolean vglSetMspaceStatsProbe(GLboolean enable) {
	if (probe_initialized)
		return GL_FALSE;
#ifdef HAVE_CUSTOM_HEAP
	return enable ? GL_FALSE : GL_TRUE;
#else
	if (enable && cache_mode != VGL_MSPACE_STATS_CACHE_OFF)
		return GL_FALSE;
	vgl_mspace_stats_probe_enabled = enable != GL_FALSE;
	vgl_mspace_stats_tracking_enabled = vgl_mspace_stats_probe_enabled || cache_mode != VGL_MSPACE_STATS_CACHE_OFF;
	return GL_TRUE;
#endif
}

GLboolean vglSetMspaceStatsCache(uint32_t mode) {
	if (probe_initialized || mode > VGL_MSPACE_STATS_CACHE_VERIFY)
		return GL_FALSE;
#ifdef HAVE_CUSTOM_HEAP
	return mode == VGL_MSPACE_STATS_CACHE_OFF;
#else
	if (mode != VGL_MSPACE_STATS_CACHE_OFF && vgl_mspace_stats_probe_enabled)
		return GL_FALSE;
	cache_mode = mode;
	vgl_mspace_stats_tracking_enabled = vgl_mspace_stats_probe_enabled || mode != VGL_MSPACE_STATS_CACHE_OFF;
	return GL_TRUE;
#endif
}

void vgl_mspace_stats_probe_initialize(void) {
#ifndef HAVE_CUSTOM_HEAP
	if (probe_initialized) {
		if (vgl_mspace_stats_tracking_enabled) {
			for (unsigned int i = 0; i < VGL_MSPACE_STATS_POOL_COUNT; ++i) {
				GLboolean locked = vgl_mspace_stats_probe_begin_impl((vglMemType)i);
				vgl_mspace_stats_probe_end((vglMemType)i, locked);
			}
		}
		return;
	}
	if (vgl_mspace_stats_tracking_enabled) {
		for (unsigned int i = 0; i < VGL_MSPACE_STATS_POOL_COUNT; ++i) {
			if (sceKernelCreateLwMutex(&pools[i].mutex, "vgl mspace stats", 0, 0, NULL) < 0) {
				__atomic_store_n(&probe_failed, 1, __ATOMIC_RELAXED);
				while (i != 0)
					sceKernelDeleteLwMutex(&pools[--i].mutex);
				break;
			}
		}
	}
#endif
	probe_initialized = GL_TRUE;
}

GLboolean vgl_mspace_stats_probe_begin_impl(vglMemType type) {
#ifndef HAVE_CUSTOM_HEAP
	if (probe_lock(type)) {
		pools[type].valid = GL_FALSE;
		if (vgl_mspace_stats_probe_enabled)
			++pools[type].counters.mutation_attempts;
		else
			++pools[type].cache_counters.mutation_attempts;
		return GL_TRUE;
	}
#endif
	return GL_FALSE;
}

void vgl_mspace_stats_probe_end_impl(vglMemType type) {
#ifndef HAVE_CUSTOM_HEAP
	probe_unlock(type);
#endif
}

#ifndef HAVE_CUSTOM_HEAP
static void cache_stats(vglMemType type, SceClibMspace space, SceClibMspaceStats *stats) {
	if (!probe_lock(type)) {
		sceClibMspaceMallocStats(space, stats);
		return;
	}
	MspaceStatsProbePool *pool = &pools[type];
	vglMspaceStatsCachePool *counters = &pool->cache_counters;
	++counters->queries;
	const GLboolean eligible = space && stats && pool->valid && pool->previous_space == space
		&& !__atomic_load_n(&cache_validation_failed, __ATOMIC_RELAXED);
	if (eligible)
		++counters->eligible_hits;
	if (eligible && cache_mode == VGL_MSPACE_STATS_CACHE_REUSE) {
		*stats = pool->previous;
		++counters->reused;
		if (!probe_unlock(type) || __atomic_load_n(&probe_failed, __ATOMIC_RELAXED))
			sceClibMspaceMallocStats(space, stats);
		return;
	}
	if (stats) {
		stats->capacity = 0;
		stats->unk = UINT32_MAX;
		stats->peak_in_use = UINT32_MAX;
		stats->current_in_use = UINT32_MAX;
	}
	++counters->native_calls;
	sceClibMspaceMallocStats(space, stats);
	if (space && stats && stats->capacity > 0 && stats->unk == stats->capacity
		&& stats->current_in_use <= stats->capacity && stats->peak_in_use <= stats->capacity) {
		if (eligible && cache_mode == VGL_MSPACE_STATS_CACHE_VERIFY) {
			++counters->verification_checks;
			if (pool->previous.capacity != stats->capacity || pool->previous.unk != stats->unk
				|| pool->previous.peak_in_use != stats->peak_in_use
				|| pool->previous.current_in_use != stats->current_in_use) {
				++counters->mismatches;
				__atomic_store_n(&cache_validation_failed, 1, __ATOMIC_RELAXED);
			}
		}
		pool->previous = *stats;
		pool->previous_space = space;
		pool->valid = GL_TRUE;
	} else {
		pool->valid = GL_FALSE;
		++counters->invalid_queries;
		__atomic_store_n(&cache_validation_failed, 1, __ATOMIC_RELAXED);
	}
	probe_unlock(type);
}
#endif

void vgl_mspace_stats_probe_stats_impl(vglMemType type, SceClibMspace space, SceClibMspaceStats *stats) {
#ifndef HAVE_CUSTOM_HEAP
	if (cache_mode != VGL_MSPACE_STATS_CACHE_OFF) {
		cache_stats(type, space, stats);
		return;
	}
	GLboolean locked = probe_lock(type);
	if (locked && stats) {
		stats->capacity = 0;
		stats->unk = UINT32_MAX;
		stats->peak_in_use = UINT32_MAX;
		stats->current_in_use = UINT32_MAX;
	}
	sceClibMspaceMallocStats(space, stats);
	if (locked) {
		MspaceStatsProbePool *pool = &pools[type];
		++pool->counters.queries;
		if (space && stats && stats->capacity > 0 && stats->unk == stats->capacity
			&& stats->current_in_use <= stats->capacity && stats->peak_in_use <= stats->capacity) {
			if (pool->valid) {
				++pool->counters.eligible_hits;
				if (pool->previous.capacity != stats->capacity || pool->previous.unk != stats->unk
					|| pool->previous.peak_in_use != stats->peak_in_use
					|| pool->previous.current_in_use != stats->current_in_use)
					++pool->counters.mismatches;
			}
			pool->previous = *stats;
			pool->valid = GL_TRUE;
		} else {
			pool->valid = GL_FALSE;
			++pool->counters.invalid_queries;
		}
		probe_unlock(type);
	}
#else
	sceClibMspaceMallocStats(space, stats);
#endif
}

GLboolean vglGetMspaceStatsProbe(vglMspaceStatsProbe *out, uint32_t size, uint32_t abi_version) {
	if (!out || size != sizeof(*out) || abi_version != VGL_MSPACE_STATS_PROBE_ABI_VERSION)
		return GL_FALSE;
	vglMspaceStatsProbe result = {0};
	result.abi_version = VGL_MSPACE_STATS_PROBE_ABI_VERSION;
	result.struct_size = sizeof(result);
	result.initialized = probe_initialized;
#ifndef HAVE_CUSTOM_HEAP
	result.supported = GL_TRUE;
	result.enabled = vgl_mspace_stats_probe_enabled;
	if (result.enabled && probe_initialized) {
		for (unsigned int i = 0; i < VGL_MSPACE_STATS_POOL_COUNT; ++i) {
			if (!probe_lock((vglMemType)i))
				return GL_FALSE;
			result.pools[i] = pools[i].counters;
			probe_unlock((vglMemType)i);
		}
		if (__atomic_load_n(&probe_failed, __ATOMIC_RELAXED))
			return GL_FALSE;
	}
#endif
	*out = result;
	return GL_TRUE;
}

GLboolean vglGetMspaceStatsCache(vglMspaceStatsCache *out, uint32_t size, uint32_t abi_version) {
	if (!out || size != sizeof(*out) || abi_version != VGL_MSPACE_STATS_CACHE_ABI_VERSION)
		return GL_FALSE;
	vglMspaceStatsCache result = {0};
	result.abi_version = VGL_MSPACE_STATS_CACHE_ABI_VERSION;
	result.struct_size = sizeof(result);
	result.initialized = probe_initialized;
#ifndef HAVE_CUSTOM_HEAP
	result.supported = GL_TRUE;
	result.mode = cache_mode;
	if (cache_mode != VGL_MSPACE_STATS_CACHE_OFF && probe_initialized) {
		for (unsigned int i = 0; i < VGL_MSPACE_STATS_CACHE_POOL_COUNT; ++i) {
			if (!probe_lock((vglMemType)i))
				return GL_FALSE;
			result.pools[i] = pools[i].cache_counters;
			probe_unlock((vglMemType)i);
		}
		if (__atomic_load_n(&probe_failed, __ATOMIC_RELAXED))
			return GL_FALSE;
		result.validation_failed = __atomic_load_n(&cache_validation_failed, __ATOMIC_RELAXED);
	}
#endif
	*out = result;
	return GL_TRUE;
}
