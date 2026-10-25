"""Conservative surface-water prototype and diagnostic 2-D potential wind field.

This is deliberately not a calibrated sewer model or building-scale CFD solver.
All arrays use projected metres, with rows increasing southward.
"""
from dataclasses import dataclass
import numpy as np


@dataclass
class Budget:
    initial_m3: float = 0.0
    rain_m3: float = 0.0
    drained_m3: float = 0.0
    coastal_exchange_m3: float = 0.0


def advance_water(h, ground, buildings, ocean, rain_rate, drain_rate, tide, dx, dt, budget, roughness=0.035):
    """Manning diffusive-wave fluxes, donor limited to preserve nonnegative water."""
    area = dx * dx
    rain = rain_rate * dt
    h += rain
    budget.rain_m3 += float(np.sum(rain) * area)
    # Simplified tide-locking assumption. No underground network is inferred.
    drained = np.minimum(h, drain_rate * dt) * ((ground + h) > tide)
    h -= drained
    budget.drained_m3 += float(np.sum(drained) * area)
    eta = ground + h

    def flux(a, b, za, zb, blocked):
        head = a - b
        wet_depth = np.maximum(0.0, np.maximum(a, b) - np.maximum(za, zb))
        q = np.sign(head) * wet_depth ** (5 / 3) / roughness * np.sqrt(np.abs(head) / dx)
        # Cap transport velocity, not water depth. dt <= dx / (4 * 3 m/s).
        q = np.clip(q, -3 * wet_depth, 3 * wet_depth) * dt / dx
        q[blocked] = 0
        return q

    qx = flux(eta[:, :-1], eta[:, 1:], ground[:, :-1], ground[:, 1:], buildings[:, :-1] | buildings[:, 1:])
    qy = flux(eta[:-1], eta[1:], ground[:-1], ground[1:], buildings[:-1] | buildings[1:])
    outgoing = np.zeros_like(h)
    outgoing[:, :-1] += np.maximum(qx, 0)
    outgoing[:, 1:] += np.maximum(-qx, 0)
    outgoing[:-1] += np.maximum(qy, 0)
    outgoing[1:] += np.maximum(-qy, 0)
    factor = np.minimum(1.0, h / np.maximum(outgoing, 1e-15))
    qx *= np.where(qx >= 0, factor[:, :-1], factor[:, 1:])
    qy *= np.where(qy >= 0, factor[:-1], factor[1:])
    h[:, :-1] -= qx
    h[:, 1:] += qx
    h[:-1] -= qy
    h[1:] += qy
    if float(h.min()) < -1e-10:
        raise ArithmeticError("Negative water depth: positivity contract failed")
    np.maximum(h, 0, out=h)
    before = float(h[ocean].sum())
    h[ocean] = np.maximum(tide - ground[ocean], 0)
    budget.coastal_exchange_m3 += (float(h[ocean].sum()) - before) * area
    return h


def potential_wind(buildings, speed, direction_from_deg, dx, iterations=450):
    """Inviscid 2-D diagnostic flow; excludes turbulence, wakes and failure loads."""
    theta = np.deg2rad(direction_from_deg)
    east, north = -speed * np.sin(theta), -speed * np.cos(theta)
    y, x = np.indices(buildings.shape)
    phi = east * x * dx - north * y * dx
    fixed = phi.copy()
    free = ~buildings
    for _ in range(iterations):
        total = np.zeros_like(phi)
        count = np.zeros_like(phi)
        for axis, step in [(0, 1), (0, -1), (1, 1), (1, -1)]:
            neighbor = np.roll(phi, step, axis=axis)
            valid = np.roll(free, step, axis=axis)
            total += np.where(valid, neighbor, 0)
            count += valid
        phi = np.where(free & (count > 0), total / np.maximum(count, 1), phi)
        phi[[0, -1]] = fixed[[0, -1]]
        phi[:, [0, -1]] = fixed[:, [0, -1]]
    # No-through-building ghost cells avoid huge spurious derivatives at walls.
    e = np.where(np.roll(free, -1, 1), np.roll(phi, -1, 1), phi)
    w = np.where(np.roll(free, 1, 1), np.roll(phi, 1, 1), phi)
    n = np.where(np.roll(free, 1, 0), np.roll(phi, 1, 0), phi)
    s = np.where(np.roll(free, -1, 0), np.roll(phi, -1, 0), phi)
    u, v = (e - w) / (2 * dx), (n - s) / (2 * dx)
    u[buildings] = 0
    v[buildings] = 0
    u[[0, -1]], v[[0, -1]] = east, north
    u[:, [0, -1]], v[:, [0, -1]] = east, north
    return u, v
