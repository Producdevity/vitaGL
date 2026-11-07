#include "shared.h"
#include "memory_stats_probe.h"

#define VGL_MSPACE_STATS_POOL_COUNT VGL_MEM_EXTERNAL

_Static_assert(VGL_MSPACE_STATS_POOL_COUNT == VGL_MSPACE_STATS_PROBE_POOL_COUNT, "pool ABI changed");
_Static_assert(__atomic_always_lock_free(sizeof(unsigned int), 0), "probe failure flag requires lock-free access");

GLboolean vgl_mspace_stats_probe_enabled = GL_FALSE;
static GLboolean probe_initialized = GL_FALSE;

#ifndef HAVE_CUSTOM_HEAP
typedef struct {
	SceKernelLwMutexWork mutex;
	SceClibMspaceStats previous;
	vglMspaceStatsProbePool counters;
	GLboolean valid;
} MspaceStatsProbePool;

static MspaceStatsProbePool pools[VGL_MSPACE_STATS_POOL_COUNT];
static unsigned int probe_failed = 0;

static GLboolean probe_lock(vglMemType type) {
	if ((unsigned int)type >= VGL_MSPACE_STATS_POOL_COUNT || !probe_initialized
		|| __atomic_load_n(&probe_failed, __ATOMIC_RELAXED))
		return GL_FALSE;
	if (sceKernelLockLwMutex(&pools[type].mutex, 1, NULL) < 0) {
		__atomic_store_n(&probe_failed, 1, __ATOMIC_RELAXED);
		return GL_FALSE;
	}
	return GL_TRUE;
}

static void probe_unlock(vglMemType type) {
	if (sceKernelUnlockLwMutex(&pools[type].mutex, 1) < 0)
		__atomic_store_n(&probe_failed, 1, __ATOMIC_RELAXED);
}
#endif

GLboolean vglSetMspaceStatsProbe(GLboolean enable) {
	if (probe_initialized)
		return GL_FALSE;
#ifdef HAVE_CUSTOM_HEAP
	return enable ? GL_FALSE : GL_TRUE;
#else
	vgl_mspace_stats_probe_enabled = enable != GL_FALSE;
	return GL_TRUE;
#endif
}

void vgl_mspace_stats_probe_initialize(void) {
#ifndef HAVE_CUSTOM_HEAP
	if (probe_initialized) {
		if (vgl_mspace_stats_probe_enabled) {
			for (unsigned int i = 0; i < VGL_MSPACE_STATS_POOL_COUNT; ++i) {
				GLboolean locked = vgl_mspace_stats_probe_begin_impl((vglMemType)i);
				vgl_mspace_stats_probe_end((vglMemType)i, locked);
			}
		}
		return;
	}
	if (vgl_mspace_stats_probe_enabled) {
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
		++pools[type].counters.mutation_attempts;
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

void vgl_mspace_stats_probe_stats_impl(vglMemType type, SceClibMspace space, SceClibMspaceStats *stats) {
#ifndef HAVE_CUSTOM_HEAP
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
