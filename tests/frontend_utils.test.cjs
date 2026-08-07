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
  closeTo(view.calculateFitScale(100, 100, 200, 100), 0.5);
  closeTo(view.calculateBaseScale('actual', 100, 100, 200, 100), 1);
  const transform = view.normalizedToTransform({
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

  const zoomContext = {
    screenX: 70,
    screenY: 40,
    centerX: 0.5,
    centerY: 0.5,
    viewportWidth: 120,
    viewportHeight: 90,
    imageWidth: 240,
    imageHeight: 180,
  };
  const anchoredBefore = view.screenToNormalized({ ...zoomContext, zoom: 1 });
  const nextCenter = view.centerForZoomAtPoint({ ...zoomContext, oldZoom: 1, newZoom: 2.5 });
  const anchoredAfter = view.screenToNormalized({
    ...zoomContext,
    centerX: nextCenter.x,
    centerY: nextCenter.y,
    zoom: 2.5,
  });
  closeTo(anchoredAfter.x, anchoredBefore.x);
  closeTo(anchoredAfter.y, anchoredBefore.y);

  const panned = view.panCenter({
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

  assert.equal(view.normalizeImageTitle('  A 标题  '), 'A 标题');
  assert.equal(view.normalizeImageTitle('   '), '');
  assert.equal(view.hasImageTitles('', '   '), false);
  assert.equal(view.hasImageTitles('', ' B 标题 '), true);
  assert.equal(view.clampTitleSize(9), 10);
  assert.equal(view.clampTitleSize(16), 16);
  assert.equal(view.clampTitleSize(99), 32);
  assert.equal(view.clampTitleSize('invalid'), 16);
  assert.equal(view.isFullscreenShortcut({ key: 'F', shiftKey: true }), true);
  assert.equal(view.isFullscreenShortcut({ key: 'f', shiftKey: false }), false);
  assert.equal(view.isFullscreenShortcut({ key: 'F', shiftKey: true, ctrlKey: true }), false);
  assert.equal(view.isFullscreenShortcut({ key: 'F', shiftKey: true, repeat: true }), false);
  assert.equal(view.isFitShortcut({ key: 'f' }), true);
  assert.equal(view.isFitShortcut({ key: 'F', shiftKey: true }), false);
  assert.equal(view.isFitShortcut({ key: 'f', ctrlKey: true }), false);
  const split25 = view.calculateSplitTitleGeometry(25);
  closeTo(split25.leftCenter, 12.5);
  closeTo(split25.rightCenter, 62.5);
  closeTo(split25.leftWidth, 25);
  closeTo(split25.rightWidth, 75);
  assert.equal(split25.showLeft, true);
  assert.equal(split25.showRight, true);
  const split0 = view.calculateSplitTitleGeometry(0);
  assert.equal(split0.showLeft, false);
  assert.equal(split0.showRight, true);
  const split100 = view.calculateSplitTitleGeometry(100);
  assert.equal(split100.showLeft, true);
  assert.equal(split100.showRight, false);
  const splitFallback = view.calculateSplitTitleGeometry('invalid');
  closeTo(splitFallback.split, 50);
  closeTo(splitFallback.leftCenter, 25);
  closeTo(splitFallback.rightCenter, 75);
}

function testAnalyzeCreatorSlots() {
  const sandbox = loadBrowserScript('static/js/analyze-new.js');
  const utils = sandbox.__analyzeCreateTestHooks;
  assert.ok(utils, 'analysis creation hooks should load without page markup');
  assert.equal(utils.minImages, 2);
  assert.equal(utils.hardMaxImages, 8);
  assert.equal(utils.clampImageLimit(99), 8);
  assert.equal(utils.clampImageLimit(1), 2);
  assert.equal(utils.normalizeSlotCount(2, 5, 8), 5);
  assert.equal(utils.normalizeSlotCount(8, 2, 8), 8);
  const targetB = utils.planSlotAssignments([false, false], 1, 1, 8);
  assert.deepEqual(Array.from(targetB.targets), [1]);
  assert.equal(targetB.slotCount, 2);
  assert.equal(targetB.remaining, 0);
  const targetBMultiple = utils.planSlotAssignments([false, false], 2, 1, 8);
  assert.deepEqual(Array.from(targetBMultiple.targets), [1, 2]);
  assert.equal(targetBMultiple.slotCount, 3);
  const targetH = utils.planSlotAssignments(Array(8).fill(false), 1, 7, 8);
  assert.deepEqual(Array.from(targetH.targets), [7]);
  const targetHMultiple = utils.planSlotAssignments(Array(8).fill(false), 2, 7, 8);
  assert.deepEqual(Array.from(targetHMultiple.targets), [7, 0]);
  const autoExpand = utils.planSlotAssignments([true, true], 6, null, 8);
  assert.deepEqual(Array.from(autoExpand.targets), [2, 3, 4, 5, 6, 7]);
  assert.equal(autoExpand.slotCount, 8);
  const overflow = utils.planSlotAssignments(Array(8).fill(true), 2, 0, 8);
  assert.deepEqual(Array.from(overflow.targets), [0]);
  assert.equal(overflow.remaining, 1);
}

function testAnalyzeCoordinates() {
  const sandbox = loadBrowserScript('static/js/analyze-view.js');
  const utils = sandbox.__analyzeTestHooks;
  assert.ok(utils, 'analysis coordinate hooks should load without project markup');
  assert.equal(utils.calculateComparisonColumns(2), 2);
  assert.equal(utils.calculateComparisonColumns(4), 4);
  assert.equal(utils.calculateComparisonColumns(5), 3);
  assert.equal(utils.calculateComparisonColumns(6), 3);
  assert.equal(utils.calculateComparisonColumns(8), 4);
  assert.equal(utils.defaultComparisonMode(2), 'slider');
  assert.equal(utils.defaultComparisonMode(3), 'parallel');
  assert.equal(utils.defaultComparisonMode(8), 'parallel');
  assert.equal(utils.displayVariantId(0), 'A');
  assert.equal(utils.displayVariantId(7), 'H');
  assert.equal(utils.displayVariantId(8), '');
  assert.equal(utils.normalizeImageTitle('  对比标题  '), '对比标题');
  assert.equal(utils.normalizeImageTitle('   '), '');
  assert.equal(utils.normalizeImageTitle(null), '');
  assert.equal(utils.clampTitleSize(9), 10);
  assert.equal(utils.clampTitleSize(16), 16);
  assert.equal(utils.clampTitleSize(40), 32);
  assert.equal(utils.clampTitleSize('not-a-number'), 16);
  assert.equal(utils.isFullscreenShortcut({ key: 'F', shiftKey: true }), true);
  assert.equal(utils.isFullscreenShortcut({ key: 'f', shiftKey: false }), false);
  assert.equal(utils.isFullscreenShortcut({ key: 'F', shiftKey: true, metaKey: true }), false);
  assert.equal(utils.isFullscreenShortcut({ key: 'F', shiftKey: true, repeat: true }), false);
  assert.equal(utils.isFitShortcut({ key: 'f' }), true);
  assert.equal(utils.isFitShortcut({ key: 'F', shiftKey: true }), false);
  assert.equal(utils.isFitShortcut({ key: 'f', ctrlKey: true }), false);

  const split25 = utils.calculateSliderTitlePositions(25);
  closeTo(split25.left, 12.5);
  closeTo(split25.right, 62.5);
  closeTo(split25.leftWidth, 25);
  closeTo(split25.rightWidth, 75);
  assert.equal(split25.showLeft, true);
  assert.equal(split25.showRight, true);
  const split0 = utils.calculateSliderTitlePositions(0);
  closeTo(split0.left, 0);
  closeTo(split0.right, 50);
  assert.equal(split0.showLeft, false);
  assert.equal(split0.showRight, true);
  const split100 = utils.calculateSliderTitlePositions(100);
  closeTo(split100.left, 50);
  closeTo(split100.right, 100);
  assert.equal(split100.showLeft, true);
  assert.equal(split100.showRight, false);
  const splitFallback = utils.calculateSliderTitlePositions('invalid');
  closeTo(splitFallback.left, 25);
  closeTo(splitFallback.right, 75);

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
  testAnalyzeCreatorSlots();
  testAnalyzeCoordinates();
  process.stdout.write('frontend utility tests passed\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
