/* Geographic data is streamed from the original services; nothing is fabricated. */
require([
  "esri/Map",
  "esri/Basemap",
  "esri/layers/TileLayer",
  "esri/layers/SceneLayer",
  "esri/layers/ElevationLayer",
  "esri/views/SceneView",
  "esri/core/reactiveUtils"
], (Map, Basemap, TileLayer, SceneLayer, ElevationLayer, SceneView, reactiveUtils) => {
  const status = document.getElementById("status");
  const buildings = new SceneLayer({
    url: "https://tiles.arcgis.com/tiles/z2tnIkrLQ2BRzr6P/arcgis/rest/services/SanFrancisco_Bldgs/SceneServer/layers/0",
    title: "San Francisco textured 3D buildings",
    copyright: "San Francisco 3D buildings: Precision LightWorks (PLW) / Esri",
    popupEnabled: false,
    legendEnabled: false
  });

  const imagery = new TileLayer({
    url: "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer"
  });

  const map = new Map({
    basemap: new Basemap({ baseLayers: [imagery] }),
    ground: {
      layers: [new ElevationLayer({
        url: "https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer"
      })]
    },
    layers: [buildings]
  });

  const view = new SceneView({
    container: "scene",
    map,
    qualityProfile: "high",
    // Looking southwest from the bay: downtown, Ferry Building, piers and bridge.
    camera: {
      position: { longitude: -122.3794, latitude: 37.8077, z: 1350 },
      heading: 237,
      tilt: 66
    },
    ui: { components: ["attribution"] },
    popupEnabled: false,
    highlightOptions: { fillOpacity: 0, haloOpacity: 0 },
    environment: {
      lighting: { date: new Date("2026-06-21T20:00:00Z"), directShadowsEnabled: true },
      atmosphereEnabled: true,
      starsEnabled: false
    },
    constraints: { altitude: { min: 20, max: 15000000 } }
  });

  const fail = (error) => {
    status.hidden = false;
    status.textContent = "The 3D scene could not load. Check your connection and reload.";
    console.error(error);
  };

  Promise.all([view.when(), buildings.load(), imagery.load()])
    .then(async () => {
      const layerView = await view.whenLayerView(buildings);
      await reactiveUtils.whenOnce(() => !view.updating && !layerView.updating);
      status.hidden = true;
      document.documentElement.dataset.sceneReady = "true";
    })
    .catch(fail);

  // Keep the real scene available for hackathon code and runtime inspection.
  window.sfScene = { view, map, buildings };
  reactiveUtils.watch(() => view.fatalError, (error) => { if (error) fail(error); });
});
