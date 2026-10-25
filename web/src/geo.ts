// UTM zone 10N <-> WGS84 and an ENU (east-north-up) frame at the tile centre.
// The three.js storm layer lives in this ENU frame (z up, metres) in both render modes.
const a = 6378137.0, f = 1 / 298.257223563, k0 = 0.9996;
const e2 = f * (2 - f), ep2 = e2 / (1 - e2);
const D2R = Math.PI / 180;

export function lonLatToUtm(lon: number, lat: number, zone = 10): [number, number] {
  const lon0 = ((zone - 1) * 6 - 180 + 3) * D2R;
  const phi = lat * D2R, lam = lon * D2R;
  const N = a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
  const T = Math.tan(phi) ** 2, C = ep2 * Math.cos(phi) ** 2, A = Math.cos(phi) * (lam - lon0);
  const M = a * ((1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 ** 3 / 256) * phi - (3 * e2 / 8 + 3 * e2 * e2 / 32 + 45 * e2 ** 3 / 1024) * Math.sin(2 * phi)
    + (15 * e2 * e2 / 256 + 45 * e2 ** 3 / 1024) * Math.sin(4 * phi) - (35 * e2 ** 3 / 3072) * Math.sin(6 * phi));
  const x = k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5 / 120) + 500000;
  const y = k0 * (M + N * Math.tan(phi) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24 + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6 / 720));
  return [x, y];
}

export function utmToLonLat(x: number, y: number, zone = 10): [number, number] {
  const lon0 = ((zone - 1) * 6 - 180 + 3) * D2R;
  const e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const M = y / k0;
  const mu = M / (a * (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 ** 3 / 256));
  const phi1 = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu) + (21 * e1 * e1 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
    + (151 * e1 ** 3 / 96) * Math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * Math.sin(8 * mu);
  const N1 = a / Math.sqrt(1 - e2 * Math.sin(phi1) ** 2), T1 = Math.tan(phi1) ** 2, C1 = ep2 * Math.cos(phi1) ** 2;
  const R1 = a * (1 - e2) / Math.pow(1 - e2 * Math.sin(phi1) ** 2, 1.5), D = (x - 500000) / (N1 * k0);
  const lat = phi1 - (N1 * Math.tan(phi1) / R1) * (D * D / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * ep2) * D ** 4 / 24
    + (61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * ep2 - 3 * C1 * C1) * D ** 6 / 720);
  const lon = lon0 + (D - (1 + 2 * T1 + C1) * D ** 3 / 6 + (5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * ep2 + 24 * T1 * T1) * D ** 5 / 120) / Math.cos(phi1);
  return [lon / D2R, lat / D2R];
}

function ecef(lon: number, lat: number, h: number): [number, number, number] {
  const phi = lat * D2R, lam = lon * D2R;
  const N = a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
  return [(N + h) * Math.cos(phi) * Math.cos(lam), (N + h) * Math.cos(phi) * Math.sin(lam), (N * (1 - e2) + h) * Math.sin(phi)];
}

/** Tile frame: local metres (x east, y north from the grid SW corner, UTM 10N) -> ENU at the tile centre. */
export class TileFrame {
  x0: number; y0: number; w: number; h: number;
  lon0: number; lat0: number;
  private o: [number, number, number];
  private R: number[]; // 3x3 ecef->enu
  zOffset = 0; // datum offset added to all heights (NAVD88 -> renderer's elevation)
  constructor(x0: number, y0: number, w: number, h: number) {
    this.x0 = x0; this.y0 = y0; this.w = w; this.h = h;
    [this.lon0, this.lat0] = utmToLonLat(x0 + w / 2, y0 + h / 2);
    this.o = ecef(this.lon0, this.lat0, 0);
    const sl = Math.sin(this.lon0 * D2R), cl = Math.cos(this.lon0 * D2R), sp = Math.sin(this.lat0 * D2R), cp = Math.cos(this.lat0 * D2R);
    this.R = [-sl, cl, 0, -sp * cl, -sp * sl, cp, cp * cl, cp * sl, sp];
  }
  /** local (x,y) metres + height z (NAVD88) -> ENU [e, n, u] */
  toENU(x: number, y: number, z: number, out: number[] | Float32Array = [0, 0, 0], o = 0) {
    const [lon, lat] = utmToLonLat(this.x0 + x, this.y0 + y);
    const p = ecef(lon, lat, z + this.zOffset);
    const dx = p[0] - this.o[0], dy = p[1] - this.o[1], dz = p[2] - this.o[2], R = this.R;
    out[o] = R[0] * dx + R[1] * dy + R[2] * dz;
    out[o + 1] = R[3] * dx + R[4] * dy + R[5] * dz;
    out[o + 2] = R[6] * dx + R[7] * dy + R[8] * dz;
    return out;
  }
  lonLatToLocal(lon: number, lat: number): [number, number] {
    const [x, y] = lonLatToUtm(lon, lat); return [x - this.x0, y - this.y0];
  }
  localToLonLat(x: number, y: number): [number, number] { return utmToLonLat(this.x0 + x, this.y0 + y); }
}
