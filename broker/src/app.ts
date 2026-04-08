import { Hono } from 'hono';

import {
  BROKER_SERVICE_NAME,
  FOUNDATION_RESPONSE,
  type BrokerEnv,
} from './contract';
import {
  handleTrialChallenge,
  handleTrialChallengeVerify,
  handleTrialStatus,
} from './trial-handshake';

export const app = new Hono<BrokerEnv>();

app.get('/healthz', (c) => {
  return c.json({
    ok: true,
    service: BROKER_SERVICE_NAME,
  });
});

app.get('/v1/foundation', (c) => {
  return c.json(FOUNDATION_RESPONSE);
});

app.post('/v1/trial/challenge', handleTrialChallenge);
app.post('/v1/trial/challenge/verify', handleTrialChallengeVerify);
app.get('/v1/trial/status', handleTrialStatus);

export default app;
