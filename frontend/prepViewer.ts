import {installCameraControls, type CameraView} from './cameraControls';

declare global { interface Window {planView?: CameraView;} }

// Extend the existing preparation viewer; do not mount another planner or panel.
let installed=false;
function connect(){
  if(installed||!window.planView)return;
  installCameraControls(window.planView);
  installed=true;
}
window.addEventListener('prep-view-ready',connect);
connect();
