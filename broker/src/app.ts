import { Hono } from 'hono';

import {
  BROKER_SERVICE_NAME,
  FOUNDATION_RESPONSE,
  type BrokerEnv,
} from './contract';

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

export default app;
