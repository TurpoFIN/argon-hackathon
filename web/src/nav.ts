// Ground-level walk / fly camera state (local metres, heading/pitch in degrees). The caller applies it to
// the ArcGIS SceneView camera or the stylized three.js camera each frame.
export class Walker {
  active = false; x = 0; y = 0; yaw = 0; pitch = -4; eye = 2; z = NaN; auto = 0; autoYaw = 0;
  keys = new Set<string>(); private drag: { x: number; y: number } | null = null;
  constructor(private el: HTMLElement) {
    addEventListener('keydown', (e) => { if (!this.active || (e.target as HTMLElement)?.closest?.('input,textarea')) return; const k = this.key(e); if (k) { this.keys.add(k); e.preventDefault(); e.stopImmediatePropagation(); } }, true);
    addEventListener('keyup', (e) => { const k = this.key(e); if (k) this.keys.delete(k); }, true);
    addEventListener('blur', () => this.keys.clear());
    el.addEventListener('pointerdown', (e) => { if (!this.active) return; this.drag = { x: e.clientX, y: e.clientY }; }, true);
    addEventListener('pointermove', (e) => {
      if (!this.active || !this.drag) return;
      this.yaw = (this.yaw + (e.clientX - this.drag.x) * 0.18 + 360) % 360; this.pitch = Math.max(-80, Math.min(80, this.pitch - (e.clientY - this.drag.y) * 0.15));
      this.drag = { x: e.clientX, y: e.clientY };
    });
    addEventListener('pointerup', () => (this.drag = null));
    el.addEventListener('wheel', (e) => { if (!this.active) return; this.eye = Math.max(1.7, Math.min(600, this.eye * (e.deltaY > 0 ? 1.15 : 1 / 1.15))); e.preventDefault(); e.stopPropagation(); }, { capture: true, passive: false });
  }
  private key(e: KeyboardEvent) {
    const m: Record<string, string> = { w: 'f', arrowup: 'f', s: 'b', arrowdown: 'b', a: 'l', arrowleft: 'l', d: 'r', arrowright: 'r', q: 'dn', e: 'up', shift: 'run' };
    return m[e.key.toLowerCase()];
  }
  start(x: number, y: number, yaw: number) { this.active = true; this.x = x; this.y = y; this.yaw = yaw; this.pitch = -4; this.eye = 2; this.z = NaN; this.keys.clear(); }
  stop() { this.active = false; this.auto = 0; this.autoYaw = 0; this.keys.clear(); }
  /** advance; ground(x,y) returns the ground height in the caller's units. returns eye height */
  update(dt: number, ground: (x: number, y: number) => number) {
    const K = this.keys; const sp = (K.has('run') ? 22 : 5) * (1 + this.eye / 30);
    const h = this.yaw * Math.PI / 180; const fx = Math.sin(h), fy = Math.cos(h);
    let mf = (K.has('f') ? 1 : 0) - (K.has('b') ? 1 : 0), mr = (K.has('r') ? 1 : 0) - (K.has('l') ? 1 : 0);
    if (this.auto) mf += this.auto / sp;
    this.yaw = (this.yaw + this.autoYaw * dt + 360) % 360;
    this.x += (fx * mf + fy * mr) * sp * dt; this.y += (fy * mf - fx * mr) * sp * dt;
    if (K.has('up')) this.eye = Math.min(600, this.eye + Math.max(2, this.eye) * 1.2 * dt);
    if (K.has('dn')) this.eye = Math.max(1.7, this.eye - Math.max(2, this.eye) * 1.2 * dt);
    const g = ground(this.x, this.y) + this.eye;
    this.z = isFinite(this.z) ? this.z + (g - this.z) * Math.min(1, dt * 6) : g;
    return this.z;
  }
}
