// Smoke test: a light, short run that answers "does the API work under a little load?"
// Run it first and after every deploy. If smoke fails, a bigger test is pointless.
//
// Run in the cluster:   ./scripts/run-load-test.sh smoke
// Run from your Mac:    kubectl -n autoscale-app port-forward svc/items-api 8080:80
//                       k6 run -e BASE_URL=http://localhost:8080 load-tests/smoke.js
//   (port-forward pins ONE pod, so from the Mac this tests a single replica.)
//
// Example result line: "http_req_failed....: 0.00%  ✓ 0  ✗ 1450", thresholds all ✓
import { sleep } from 'k6';
import { userJourney } from './common.js';

export const options = {
  vus: 5,
  duration: '30s',
  // Thresholds turn the test into pass/fail: k6 exits non-zero if any is crossed.
  thresholds: {
    http_req_failed: ['rate<0.01'],     // fewer than 1% of requests fail
    http_req_duration: ['p(95)<300'],   // 95% of requests faster than 300 ms
    checks: ['rate>0.99'],              // response bodies/statuses as expected
  },
};

export default function () {
  userJourney();
  sleep(0.1); // think time: a real client doesn't fire back-to-back with zero gap
}
