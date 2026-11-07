#ifndef VGL_MEMORY_STATS_PROBE_INTERNAL_H
#define VGL_MEMORY_STATS_PROBE_INTERNAL_H

extern GLboolean vgl_mspace_stats_probe_enabled;
void vgl_mspace_stats_probe_initialize(void);
GLboolean vgl_mspace_stats_probe_begin_impl(vglMemType type);
void vgl_mspace_stats_probe_end_impl(vglMemType type);
void vgl_mspace_stats_probe_stats_impl(vglMemType type, SceClibMspace space, SceClibMspaceStats *stats);

static inline GLboolean vgl_mspace_stats_probe_begin(vglMemType type) {
	return vgl_mspace_stats_probe_enabled && vgl_mspace_stats_probe_begin_impl(type);
}

static inline void vgl_mspace_stats_probe_end(vglMemType type, GLboolean locked) {
	if (locked)
		vgl_mspace_stats_probe_end_impl(type);
}

static inline void vgl_mspace_stats_probe_stats(vglMemType type, SceClibMspace space, SceClibMspaceStats *stats) {
	if (vgl_mspace_stats_probe_enabled)
		vgl_mspace_stats_probe_stats_impl(type, space, stats);
	else
		sceClibMspaceMallocStats(space, stats);
}

#endif
