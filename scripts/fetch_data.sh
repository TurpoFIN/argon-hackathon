#!/usr/bin/env bash
# Fetch real SF data for the storm sim tile (SoMa + Mission Creek basin + downtown towers).
set -u
S=37.758; N=37.795; W=-122.425; E=-122.385
OUT="${1:-data/raw}"; mkdir -p "$OUT"
SF=https://data.sf.gov/resource
box() { echo "within_box($1,$N,$W,$S,$E)"; }
get() { echo "-> $2"; curl -sL -m 300 --retry 3 -G "$1" "${@:3}" -o "$OUT/$2"; ls -la "$OUT/$2" | awk '{print "   ",$5,"bytes"}'; }
# USGS 3DEP lidar terrain, ~1.75 m cells over 3.5 x 4.1 km (bigger exports 502)
get "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage" dem_3dep.tif \
  --data-urlencode "bbox=$W,$S,$E,$N" --data-urlencode "bboxSR=4326" --data-urlencode "imageSR=32610" \
  --data-urlencode "size=2000,2400" --data-urlencode "format=tiff" --data-urlencode "pixelType=F32" \
  --data-urlencode "interpolation=RSP_BilinearInterpolation" --data-urlencode "f=image"
get "$SF/ynuv-fyni.geojson" buildings.geojson --data-urlencode "\$where=$(box shape)" --data-urlencode "\$limit=200000"
get "$SF/tkzw-k3nq.json" trees.json --data-urlencode "\$where=latitude between $S and $N and longitude between $W and $E" --data-urlencode "\$limit=200000"
get "$SF/jtgq-b7c5.geojson" storm_inlets.geojson --data-urlencode "\$where=$(box location)" --data-urlencode "\$limit=200000"
get "$SF/jzu3-4yxp.geojson" sfpuc_100yr_flood_zone.geojson --data-urlencode "\$limit=50000"
get "$SF/wppz-u2hi.geojson" sfpuc_100yr_flood_elev_2024.geojson --data-urlencode "\$where=$(box the_geom)" --data-urlencode "\$limit=200000"
get "$SF/vw6y-z8j6.geojson" 311_storm_2022-12-31.geojson \
  --data-urlencode "\$where=requested_datetime between '2022-12-30T12:00:00' and '2023-01-02T00:00:00' AND (service_subtype in ('Flooding','Sewage_back_up','Trees - Damaged_Tree') OR service_name='Tree Maintenance')" \
  --data-urlencode "\$limit=50000"
get "$SF/wv5m-vpq2.geojson" assessor_roll.geojson --data-urlencode "\$where=closed_roll_year='2023' AND $(box the_geom)" --data-urlencode "\$limit=200000"
get "https://api.weather.gov/alerts/active" nws_alerts_now.json --data-urlencode "point=37.7749,-122.4194" -A "argonhack (touko.ursin@gmail.com)"
echo done
