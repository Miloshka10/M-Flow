import {createOrbitScene, observeOrbitAppearance} from './linear-orbit.js?v=20261111-orbit';

const canvas = document.querySelector('[data-access-orbit]');
const scene = createOrbitScene(canvas, {
  accepted: 4, active: 4, selected: 4, seed: 3, paused: true,
});
const stop = observeOrbitAppearance(({theme}) => scene.update({theme}));
window.addEventListener('pagehide', event => {
  if (!event.persisted) {
    stop();
    scene.dispose();
  }
});
