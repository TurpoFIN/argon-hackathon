# San Francisco 3D

Run `npm start` and open http://localhost:5173. No installation or API key is needed.

Full-window textured San Francisco buildings, satellite imagery, and elevation.
The initial camera looks from the bay toward downtown and the Embarcadero.
Drag to pan, right-drag to orbit, and scroll to zoom. Touch supports pan, pinch,
and two-finger rotation. Only source attribution is visible.

The scene is exposed as `window.sfScene` for further development.

Data is streamed and requires an internet connection:

- Textured city buildings: Precision LightWorks / Esri, via the public [San Francisco scene service](https://tiles.arcgis.com/tiles/z2tnIkrLQ2BRzr6P/arcgis/rest/services/SanFrancisco_Bldgs/SceneServer/layers/0).
- Satellite imagery: Esri World Imagery, with source credits displayed in the scene.
- Terrain: Esri World Elevation.
- Renderer: ArcGIS Maps SDK for JavaScript 4.33.

This is a geographically referenced textured city model, rather than Google
Photorealistic 3D Tiles. Google tiles require a separately configured Maps API key.
Building ages and detail reflect the source dataset; this is not live city data.
