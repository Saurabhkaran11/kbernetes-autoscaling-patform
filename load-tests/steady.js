// Steady background traffic for failure drills (Scenarios B, C, D).
//
// Unlike smoke/ramp, this has NO thresholds: during a drill we EXPECT errors or slowness,
// and the point is to watch alerts, dashboards and logs react, not to pass or fail k6.
// Error-rate and latency alerts are computed from request rates, so they need constant
// traffic for several minutes to fire. That's this script's job.
//
// Run:  DURATION=8m VUS=10 ./scripts/run-load-test.sh steady
//
// Example: 10 VUs with 0.1 s think time -> roughly 80-90 requests/second.
import { sleep } from 'k6';
import { userJourney } from './common.js';

export const options = {
  vus: parseInt(__ENV.VUS || '10', 10),
  duration: __ENV.DURATION || '8m',
};

export default function () {
  userJourney();
  sleep(0.1);
}
