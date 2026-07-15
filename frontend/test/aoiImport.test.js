import test from 'node:test'
import assert from 'node:assert/strict'

import { parseGeoJsonAois, parseKmlAois, parseWktAois } from '../src/aoiImport.js'

test('imports and closes a GeoJSON polygon ring', () => {
  const [aoi] = parseGeoJsonAois(JSON.stringify({
    type: 'Feature',
    properties: { name: 'North field' },
    geometry: { type: 'Polygon', coordinates: [[[68, 43], [68.1, 43], [68.1, 43.1]]] },
  }))
  assert.equal(aoi.name, 'North field')
  assert.deepEqual(aoi.geometry.coordinates[0][0], aoi.geometry.coordinates[0].at(-1))
})

test('splits a MultiPolygon into map-compatible saved zones', () => {
  const aois = parseGeoJsonAois(JSON.stringify({
    type: 'MultiPolygon',
    coordinates: [
      [[[68, 43], [68.1, 43], [68.1, 43.1], [68, 43]]],
      [[[69, 44], [69.1, 44], [69.1, 44.1], [69, 44]]],
    ],
  }), 'Farm')
  assert.equal(aois.length, 2)
  assert.equal(aois[1].geometry.type, 'Polygon')
})

test('imports KML polygon coordinates', () => {
  const [aoi] = parseKmlAois(`
    <kml><Placemark><name>KML field</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>68,43,0 68.1,43,0 68.1,43.1,0 68,43,0</coordinates>
    </LinearRing></outerBoundaryIs></Polygon></Placemark></kml>
  `)
  assert.equal(aoi.name, 'KML field')
  assert.equal(aoi.geometry.coordinates[0].length, 4)
})

test('imports WKT Polygon', () => {
  const [aoi] = parseWktAois('POLYGON ((68 43, 68.1 43, 68.1 43.1, 68 43))', 'WKT field')
  assert.equal(aoi.name, 'WKT field')
  assert.equal(aoi.geometry.coordinates[0].length, 4)
})
