// Moves vertices toward the camera along the view ray (depth only) so far, coarse photoreal LOD meshes don't swallow ground-level storm effects.
export const BIAS_GLSL = `vec4 biasMV(vec4 mv){ float l = length(mv.xyz); float b = clamp((l - 700.0) * 0.012, 0.0, 45.0); return vec4(mv.xyz * (1.0 - b / max(l, 1.0)), 1.0); }`;
