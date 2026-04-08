import { Hono } from 'hono';

import {
  BROKER_SERVICE_NAME,
  FOUNDATION_RESPONSE,
  type BrokerEnv,
} from './contract';
import {
  handleTrialChallenge,
  handleTrialChallengeVerify,
} from './trial-handshake';
import { handleOpenRouterIssue } from './openrouter-issue';

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
app.post('/v1/providers/openrouter/issue', handleOpenRouterIssue);

export default app;
