/* SPDX-License-Identifier: LGPL-3.0-or-later */

#include "shared.h"
#include <string.h>

#ifdef HAVE_SHADER_CACHE
#define VGL_DIAG_AVAILABLE_STAGES ((1u << VGL_SHADER_DIAG_STAGE_COUNT) - 1)
#else
#define VGL_DIAG_AVAILABLE_STAGES (((1u << VGL_SHADER_DIAG_STAGE_COUNT) - 1) & ~( \
	(1u << VGL_SHADER_DIAG_CUSTOM_VERTEX_CACHE) | (1u << VGL_SHADER_DIAG_CUSTOM_FRAGMENT_CACHE)))
#endif

vglShaderDiagnostics vgl_shader_diagnostics = {
	.abi_version = VGL_SHADER_DIAGNOSTICS_ABI_VERSION,
	.size = sizeof(vglShaderDiagnostics),
	.available_stages = VGL_DIAG_AVAILABLE_STAGES
};

void vglSetShaderDiagnostics(GLboolean enable) {
	vgl_shader_diagnostics.enabled = enable != GL_FALSE;
}

void vglResetShaderDiagnostics(void) {
	uint32_t enabled = vgl_shader_diagnostics.enabled;
	memset(&vgl_shader_diagnostics, 0, sizeof(vgl_shader_diagnostics));
	vgl_shader_diagnostics.abi_version = VGL_SHADER_DIAGNOSTICS_ABI_VERSION;
	vgl_shader_diagnostics.size = sizeof(vglShaderDiagnostics);
	vgl_shader_diagnostics.available_stages = VGL_DIAG_AVAILABLE_STAGES;
	vgl_shader_diagnostics.enabled = enabled;
}

GLboolean vglGetShaderDiagnostics(vglShaderDiagnostics *out, uint32_t size, uint32_t abi_version) {
	if (!out || size != sizeof(vglShaderDiagnostics) || abi_version != VGL_SHADER_DIAGNOSTICS_ABI_VERSION)
		return GL_FALSE;
	*out = vgl_shader_diagnostics;
	out->snapshot_frame = vgl_framecount;
	out->snapshot_us = vgl_shader_diagnostics.enabled ? sceKernelGetProcessTimeWide() : 0;
	return GL_TRUE;
}
