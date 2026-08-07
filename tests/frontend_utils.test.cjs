'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..');

function loadBrowserScript(relativePath) {
  const filename = path.join(projectRoot, relativePath);
  const sandbox = {
    clearTimeout,
    console,
    document: { querySelector: () => null },
    setTimeout,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(filename, 'utf8'), sandbox, { filename });
  return sandbox;
}

function closeTo(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} should be close to ${expected}`);
}

async function testGalleryUtilities() {
  const createSandbox = loadBrowserScript('static/js/gallery-new.js');
  const create = createSandbox.__galleryTestHooks;
  assert.ok(create, 'gallery creation hooks should load without a DOM');
  assert.equal(create.uploadConcurrency, 3);
  assert.deepEqual(Array.from(create.moveItem(['a', 'b', 'c'], 0, 2)), ['b', 'c', 'a']);
  assert.deepEqual(Array.from(create.moveItem(['a', 'b', 'c'], 2, -5)), ['c', 'a', 'b']);

  let active = 0;
  let peak = 0;
  const completed = [];
  const inputs = Array.from({ length: 11 }, (_, index) => index);
  await create.runConcurrent(inputs, 3, async (item) => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 3 + (item % 3)));
    completed.push(item);
    active -= 1;
  });
  assert.equal(peak, 3, 'upload scheduler must never exceed three active uploads');
  assert.deepEqual([...completed].sort((a, b) => a - b), inputs);

  const viewSandbox = loadBrowserScript('static/js/gallery-view.js');
  const view = viewSandbox.__galleryTestHooks;
  assert.ok(view, 'gallery view hooks should load without a DOM');
  assert.equal(view.calculateGridSpan(100, 8, 18), 5);
  assert.equal(view.calculateGridSpan(0, 8, 18), 1);
}

function testAnalyzeCoordinates() {
  const sandbox = loadBrowserScript('static/js/analyze-view.js');
  const utils = sandbox.__analyzeTestHooks;
  assert.ok(utils, 'analysis coordinate hooks should load without project markup');

  closeTo(utils.calculateFitScale(100, 100, 200, 100), 0.5);
  closeTo(utils.calculateBaseScale('actual', 100, 100, 200, 100), 1);
  closeTo(utils.calculateBaseScale('actual', 100, 100, 2000, 1000), 1);
  const transform = utils.normalizedToTransform({
    centerX: 0.5,
    centerY: 0.5,
    zoom: 1,
    viewportWidth: 100,
    viewportHeight: 100,
    imageWidth: 200,
    imageHeight: 100,
  });
  closeTo(transform.scale, 0.5);
  closeTo(transform.left, 0);
  closeTo(transform.top, 25);

  const actualTransform = utils.normalizedToTransform({
    centerX: 0.5,
    centerY: 0.5,
    zoom: 1,
    scaleMode: 'actual',
    viewportWidth: 100,
    viewportHeight: 100,
    imageWidth: 200,
    imageHeight: 100,
  });
  closeTo(actualTransform.scale, 1);

  const common = {
    screenX: 70,
    screenY: 40,
    centerX: 0.5,
    centerY: 0.5,
    viewportWidth: 120,
    viewportHeight: 90,
    imageWidth: 240,
    imageHeight: 180,
  };
  const anchoredBefore = utils.screenToNormalized({ ...common, zoom: 1 });
  const nextCenter = utils.centerForZoomAtPoint({ ...common, oldZoom: 1, newZoom: 2.5 });
  const anchoredAfter = utils.screenToNormalized({
    ...common,
    centerX: nextCenter.x,
    centerY: nextCenter.y,
    zoom: 2.5,
  });
  closeTo(anchoredAfter.x, anchoredBefore.x);
  closeTo(anchoredAfter.y, anchoredBefore.y);

  const panned = utils.panCenter({
    centerX: 0.5,
    centerY: 0.5,
    deltaX: 10,
    deltaY: -10,
    zoom: 2,
    viewportWidth: 100,
    viewportHeight: 100,
    imageWidth: 200,
    imageHeight: 100,
  });
  closeTo(panned.x, 0.45);
  closeTo(panned.y, 0.6);
}

(async () => {
  await testGalleryUtilities();
  testAnalyzeCoordinates();
  process.stdout.write('frontend utility tests passed\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
