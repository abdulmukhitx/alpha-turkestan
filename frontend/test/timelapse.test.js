import test from 'node:test'
import assert from 'node:assert/strict'
import { geometryBounds, lonLatToTile, nextFrameIndex, padBounds, previewTileUrl, tileGridForBounds } from '../src/timelapse.js'

test('creates a concrete preview URL for the map center', () => {
  const coords = lonLatToTile([43.3, 68.25], 8)
  assert.deepEqual(coords, { x: 176, y: 93, z: 8 })
  assert.equal(
    previewTileUrl('/tiles/rgb/{z}/{x}/{y}.png?period=2025_summer', [43.3, 68.25], 8),
    '/tiles/rgb/8/176/93.png?period=2025_summer',
  )
})

test('frame stepping loops or stops at the final frame', () => {
  assert.equal(nextFrameIndex(0, 3, true), 1)
  assert.equal(nextFrameIndex(2, 3, true), 0)
  assert.equal(nextFrameIndex(2, 3, false), -1)
})

test('derives a padded AOI extent from a polygon', () => {
  const geometry = {
    type: 'Polygon',
    coordinates: [[[68.2, 42.5], [68.4, 42.5], [68.4, 42.7], [68.2, 42.5]]],
  }
  assert.deepEqual(geometryBounds(geometry), [42.5, 68.2, 42.7, 68.4])
  const padded = padBounds(geometryBounds(geometry))
  assert.ok(padded[0] < 42.5)
  assert.ok(padded[1] < 68.2)
  assert.ok(padded[2] > 42.7)
  assert.ok(padded[3] > 68.4)
})

test('selects the highest tile grid that stays within the preload budget', () => {
  const grid = tileGridForBounds([42.5, 68.2, 42.7, 68.4], { maxTiles: 16 })
  assert.ok(grid.tiles.length > 0)
  assert.ok(grid.tiles.length <= 16)
  assert.ok(grid.zoom >= 5 && grid.zoom <= 18)
})
