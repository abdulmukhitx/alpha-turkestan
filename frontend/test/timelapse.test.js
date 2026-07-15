import test from 'node:test'
import assert from 'node:assert/strict'
import { lonLatToTile, nextFrameIndex, previewTileUrl } from '../src/timelapse.js'

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
