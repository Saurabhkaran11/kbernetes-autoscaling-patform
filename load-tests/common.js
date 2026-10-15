// Shared traffic mix for every k6 script.
//
// Why a weighted mix: real users don't hit one endpoint. Mixing reads, a parameterised
// read, a stats call and occasional writes exercises routing, validation and the store,
// so the latency/error numbers describe the whole API, not one cheap route.
//
// Example: with 100 iterations you get roughly
//   40 x GET /api/v1/items, 30 x GET /api/v1/items/{id}, 20 x GET /api/v1/stats, 10 x POST /api/v1/items
import http from 'k6/http';
import { check } from 'k6';

// BASE_URL defaults to the in-cluster Service DNS name (the k6 Job runs inside kind).
// From your Mac via port-forward:  k6 run -e BASE_URL=http://localhost:8080 load-tests/smoke.js
export const BASE_URL = __ENV.BASE_URL || 'http://items-api.autoscale-app.svc.cluster.local';

const JSON_HEADERS = { headers: { 'Content-Type': 'application/json' } };

export function userJourney() {
  const roll = Math.random();

  if (roll < 0.4) {
    const res = http.get(`${BASE_URL}/api/v1/items?limit=20`, { tags: { name: 'GET /api/v1/items' } });
    check(res, { 'list: status 200': (r) => r.status === 200 });
  } else if (roll < 0.7) {
    // Ids 1-3 are the seed items every pod creates at startup, so they exist on every replica
    // (the store is per-pod and in-memory; an id created on another pod would 404 here).
    // The `name` tag groups /items/1, /items/2... into one series, the same idea as the
    // route-template label in app/metrics.py.
    const id = 1 + Math.floor(Math.random() * 3);
    const res = http.get(`${BASE_URL}/api/v1/items/${id}`, { tags: { name: 'GET /api/v1/items/{id}' } });
    check(res, { 'get: status 200': (r) => r.status === 200 });
  } else if (roll < 0.9) {
    const res = http.get(`${BASE_URL}/api/v1/stats`, { tags: { name: 'GET /api/v1/stats' } });
    check(res, { 'stats: status 200': (r) => r.status === 200 });
  } else {
    const body = JSON.stringify({ name: `k6-item-${__VU}-${__ITER}`, price: 9.99, quantity: 1 });
    const res = http.post(`${BASE_URL}/api/v1/items`, body, { ...JSON_HEADERS, tags: { name: 'POST /api/v1/items' } });
    check(res, { 'create: status 201': (r) => r.status === 201 });
  }
}
