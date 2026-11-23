import {observeOrbitAppearance} from './linear-orbit.js?v=20261111-orbit';

const canvas = document.querySelector('[data-access-orbit]');
const pauseButton = document.querySelector('[data-orbit-pause]');
const form = canvas.closest('.auth-page').querySelector('.access-form');
const context = canvas.getContext('2d');
const fullTurn = Math.PI * 2;

// The rings are tilted ellipses in separate planes, viewed by the same camera.
const orbits = [
  {radius: .95, tilt: .82, turn: -.43, eccentricity: .08, speed: .34, phase: .18, size: 4.4},
  {radius: .86, tilt: .96, turn: .49, eccentricity: .12, speed: -.39, phase: 3.46, size: 3.7},
  {radius: .74, tilt: 1.35, turn: -.24, eccentricity: .05, speed: .54, phase: 2.75, size: 3.1},
  {radius: .61, tilt: .72, turn: .19, eccentricity: .06, speed: -.47, phase: 5.64, size: 2.7},
].map(orbit => ({
  ...orbit,
  planeWidth: Math.sqrt(1 - orbit.eccentricity ** 2),
  cosTilt: Math.cos(orbit.tilt),
  sinTilt: Math.sin(orbit.tilt),
  cosTurn: Math.cos(orbit.turn),
  sinTurn: Math.sin(orbit.turn),
}));

const palettes = {
  dark: {track: '#aab2d3', tail: '#9e9af0', glow: '#9185f3', bright: '#faf9ff', surface: '#beb9ff', shade: '#48418a'},
  light: {track: '#53669b', tail: '#5069bc', glow: '#6b79cc', bright: '#f4f6ff', surface: '#8b9ce0', shade: '#36478d'},
};

function orbitPoint(orbit, angle, camera) {
  const x = orbit.radius * (Math.cos(angle) - orbit.eccentricity);
  const y = orbit.radius * orbit.planeWidth * Math.sin(angle);
  const depth = y * orbit.sinTilt;
  const skyY = y * orbit.cosTilt;
  const perspective = 3.8 / (3.8 - depth);
  return {
    x: camera.x + (x * orbit.cosTurn - skyY * orbit.sinTurn) * camera.width * perspective,
    y: camera.y + (x * orbit.sinTurn + skyY * orbit.cosTurn) * camera.height * perspective,
    depth,
    perspective,
  };
}

function makeTrack(orbit, camera) {
  const distant = new Path2D();
  const near = new Path2D();
  let start = orbitPoint(orbit, 0, camera);
  for (let step = 1; step <= 112; step++) {
    const end = orbitPoint(orbit, step / 112 * fullTurn, camera);
    const path = start.depth + end.depth < 0 ? distant : near;
    path.moveTo(start.x, start.y);
    path.lineTo(end.x, end.y);
    start = end;
  }
  return {distant, near};
}

function startOrbits() {
  let appearance = {theme: 'dark', reduced: false};
  let width = 0, height = 0, camera, tracks = [];
  let frame = 0, previousTime = 0, clock = 0;
  let paused = false;

  function paintParticle(point, size, palette) {
    const radius = size * point.perspective;
    const {x, y} = point;
    context.globalAlpha = Math.max(.33, .76 + point.depth * .27);
    context.shadowColor = palette.glow;
    context.shadowBlur = radius * 2.8;
    const surface = context.createRadialGradient(x - radius * .32, y - radius * .38, 0, x, y, radius);
    surface.addColorStop(0, palette.bright);
    surface.addColorStop(.42, palette.surface);
    surface.addColorStop(1, palette.shade);
    context.fillStyle = surface;
    context.beginPath();
    context.arc(x, y, radius, 0, fullTurn);
    context.fill();
    context.shadowBlur = 0;
  }

  function draw() {
    if (!camera) return;
    const palette = palettes[appearance.theme] ?? palettes.dark;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = palette.track;
    for (const track of tracks) {
      context.globalAlpha = .15;
      context.lineWidth = .65;
      context.stroke(track.distant);
      context.globalAlpha = .35;
      context.lineWidth = .85;
      context.stroke(track.near);
    }
    const moving = !paused && !appearance.reduced;
    for (const [index, orbit] of orbits.entries()) {
      const angle = orbit.phase + clock * orbit.speed;
      const point = orbitPoint(orbit, angle, camera);
      if (moving) {
        let end = point;
        context.strokeStyle = palette.tail;
        context.lineWidth = 1 + point.perspective * .55;
        for (let segment = 1; segment <= 6; segment++) {
          const start = orbitPoint(orbit, angle - segment * orbit.speed * .08, camera);
          context.globalAlpha = (7 - segment) * .048;
          context.beginPath();
          context.moveTo(start.x, start.y);
          context.lineTo(end.x, end.y);
          context.stroke();
          end = start;
        }
      }
      paintParticle(point, orbit.size, palette);
      const satellite = orbitPoint(orbit, angle + 1.8 + index * .23, camera);
      paintParticle(satellite, orbit.size * .46, palette);
    }
    context.globalAlpha = 1;
  }

  function tick(now) {
    if (previousTime) clock += Math.min(now - previousTime, 50) / 1000;
    previousTime = now;
    draw();
    frame = requestAnimationFrame(tick);
  }

  function refresh() {
    cancelAnimationFrame(frame);
    previousTime = 0;
    draw();
    if (camera && !paused && !appearance.reduced && !document.hidden) {
      frame = requestAnimationFrame(tick);
    }
  }

  function resize() {
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    width = bounds.width;
    height = bounds.height;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    const fields = form.getBoundingClientRect();
    camera = {
      x: width / 2,
      y: Math.max(30, Math.min(height - 30, fields.top - bounds.top + fields.height * .44)),
      width: Math.min(width * .49, 560),
      height: Math.min(height * .43, 235),
    };
    tracks = orbits.map(orbit => makeTrack(orbit, camera));
    refresh();
  }

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);
  resizeObserver.observe(form);
  const stopAppearance = observeOrbitAppearance(next => {
    appearance = next;
    pauseButton.hidden = next.reduced;
    refresh();
  });
  pauseButton.addEventListener('click', () => {
    paused = !paused;
    pauseButton.setAttribute('aria-pressed', String(paused));
    refresh();
  });
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('pagehide', event => {
    if (event.persisted) return;
    cancelAnimationFrame(frame);
    resizeObserver.disconnect();
    stopAppearance();
    document.removeEventListener('visibilitychange', refresh);
  });
}

if (context) startOrbits();
