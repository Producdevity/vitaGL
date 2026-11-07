/* SPDX-License-Identifier: LGPL-3.0-or-later */

#ifndef _VGL_SHADER_DIAGNOSTICS_H_
#define _VGL_SHADER_DIAGNOSTICS_H_

extern vglShaderDiagnostics vgl_shader_diagnostics;
extern uint32_t vgl_framecount;

static inline __attribute__((always_inline)) void vgl_diag_request(vglShaderDiagnosticStageStats *stats) {
	if (!stats->requests)
		stats->first_frame = vgl_framecount;
	stats->last_frame = vgl_framecount;
	stats->requests++;
}

static inline __attribute__((always_inline)) uint64_t vgl_diag_begin(vglShaderDiagnosticStage stage) {
	if (!vgl_shader_diagnostics.enabled)
		return 0;
	vgl_diag_request(&vgl_shader_diagnostics.stages[stage]);
	return sceKernelGetProcessTimeWide();
}

static inline __attribute__((always_inline)) void vgl_diag_end(vglShaderDiagnosticStage stage, uint64_t start, GLboolean positive) {
	if (!vgl_shader_diagnostics.enabled)
		return;
	uint64_t elapsed = sceKernelGetProcessTimeWide() - start;
	vglShaderDiagnosticStageStats *stats = &vgl_shader_diagnostics.stages[stage];
	if (positive)
		stats->positive_results++;
	else
		stats->negative_results++;
	stats->total_us += elapsed;
	if (elapsed > stats->max_us)
		stats->max_us = elapsed;
}

static inline __attribute__((always_inline)) void vgl_diag_event(vglShaderDiagnosticStage stage, GLboolean positive) {
	if (!vgl_shader_diagnostics.enabled)
		return;
	vglShaderDiagnosticStageStats *stats = &vgl_shader_diagnostics.stages[stage];
	vgl_diag_request(stats);
	if (positive)
		stats->positive_results++;
	else
		stats->negative_results++;
}

#endif
