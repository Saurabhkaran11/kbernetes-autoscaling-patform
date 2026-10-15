// Ramp test: normal -> increasing -> peak -> back to zero, to watch the HPA react.
//
// Stage plan (~10 min) and what each part shows:
//   0:00-1:00  ramp to 10 VUs     normal traffic; 2 replicas should cope
//   1:00-3:00  hold 10 VUs        baseline latency and CPU
//   3:00-5:00  ramp to PEAK_VUS   increasing traffic; CPU crosses 60%, HPA adds pods
//   5:00-8:00  hold PEAK_VUS      peak; does latency stay under the SLO once scaled?
//   8:00-10:00 ramp to 0          load decreases; HPA scales down after its 120 s window
//
// Run:  ./scripts/run-load-test.sh ramp                  (PEAK_VUS defaults to 60)
//       PEAK_VUS=100 ./scripts/run-load-test.sh ramp     (harder)
import { sleep } from 'k6';
import { userJourney } from './common.js';

const PEAK = parseInt(__ENV.PEAK_VUS || '60', 10);

export const options = {
  stages: [
    { duration: '1m', target: 10 },
    { duration: '2m', target: 10 },
    { duration: '2m', target: PEAK },
    { duration: '3m', target: PEAK },
    { duration: '2m', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],     // < 1% errors over the whole run
    http_req_duration: ['p(95)<500'],   // matches the ItemsApiHighLatency alert threshold
    checks: ['rate>0.99'],
  },
};

export default function () {
  userJourney();
  sleep(0.1);
}
