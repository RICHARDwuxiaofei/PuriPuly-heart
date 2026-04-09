import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';

import { applyBrokerMigrations } from './test-support/migrations';

const renderWranglerConfigScript = new URL(
  '../scripts/render-production-wrangler-config.mjs',
  import.meta.url,
);
const renderFingerprintBootstrapScript = new URL(
  '../scripts/render-fingerprint-bootstrap-sql.mjs',
  import.meta.url,
);
const checkedInWranglerConfig = new URL('../wrangler.jsonc', import.meta.url);
const deployWorkflow = new URL(
  '../../.github/workflows/deploy-broker-direct.yml',
  import.meta.url,
);
const deploySmokeSpec = new URL(
  './deploy-smoke/canonical-production.spec.ts',
  import.meta.url,
);

const tempDirs: string[] = [];

afterEach(() => {
  for (const tempDir of tempDirs.splice(0)) {
    rmSync(tempDir, { force: true, recursive: true });
  }
});

describe('broker direct deploy automation', () => {
  it('renders a deploy-time wrangler config with the production database_id while preserving the canonical worker name', () => {
    const tempDir = createTempDir();
    const outputPath = join(tempDir, 'wrangler.production.jsonc');

    runNodeScript(renderWranglerConfigScript, [
      '--source',
      fileURLToPath(checkedInWranglerConfig),
      '--out',
      outputPath,
      '--database-id',
      'production-d1-database-id',
    ]);

    const renderedConfig = readFileSync(outputPath, 'utf8');
    expect(renderedConfig).toContain('"name": "puripuly-heart-broker"');
    expect(renderedConfig).toContain('"database_id": "production-d1-database-id"');
    expect(renderedConfig).not.toContain('REQUIRED_AT_DEPLOY_TIME');
  });

  it('fails config rendering if the checked-in worker name stops being canonical', () => {
    const tempDir = createTempDir();
    const sourcePath = join(tempDir, 'wrangler.noncanonical.jsonc');
    const outputPath = join(tempDir, 'wrangler.production.jsonc');

    writeFileSync(
      sourcePath,
      readFileSync(checkedInWranglerConfig, 'utf8').replace(
        '"name": "puripuly-heart-broker"',
        '"name": "puripuly-heart-broker-preview"',
      ),
      'utf8',
    );

    expect(() =>
      runNodeScript(renderWranglerConfigScript, [
        '--source',
        sourcePath,
        '--out',
        outputPath,
        '--database-id',
        'production-d1-database-id',
      ]),
    ).toThrow(/canonical worker name/i);
  });

  it('renders guarded fingerprint bootstrap SQL that replaces only the placeholder salt', () => {
    const tempDir = createTempDir();
    const outputPath = join(tempDir, 'fingerprint-bootstrap.sql');
    const bootstrapSalt = 'deploy-bootstrap-salt-01';

    runNodeScript(renderFingerprintBootstrapScript, [
      '--out',
      outputPath,
      '--salt',
      bootstrapSalt,
    ]);

    const renderedSql = readFileSync(outputPath, 'utf8');
    const db = new DatabaseSync(':memory:');

    try {
      expect(renderedSql).not.toContain('__BOOTSTRAP_REQUIRED__');
      expect(renderedSql).toContain(bootstrapSalt);

      applyBrokerMigrations(db);
      db.exec(renderedSql);

      const row = db
        .prepare('SELECT value FROM broker_config WHERE key = ?')
        .get('fingerprint_salt') as { value: string };

      expect(JSON.parse(row.value)).toEqual({
        current: {
          version: 1,
          salt: bootstrapSalt,
        },
        previous: null,
        rotated_at: null,
      });
    } finally {
      db.close();
    }
  });

  it('fails guarded fingerprint bootstrap SQL when the placeholder has already been replaced', () => {
    const tempDir = createTempDir();
    const outputPath = join(tempDir, 'fingerprint-bootstrap.sql');

    runNodeScript(renderFingerprintBootstrapScript, [
      '--out',
      outputPath,
      '--salt',
      'deploy-bootstrap-salt-02',
    ]);

    const renderedSql = readFileSync(outputPath, 'utf8');
    const db = new DatabaseSync(':memory:');

    try {
      applyBrokerMigrations(db);
      db.prepare('UPDATE broker_config SET value = ? WHERE key = ?').run(
        JSON.stringify({
          current: {
            version: 1,
            salt: 'already-bootstrapped',
          },
          previous: null,
          rotated_at: null,
        }),
        'fingerprint_salt',
      );

      expect(() => db.exec(renderedSql)).toThrow(/constraint/i);
    } finally {
      db.close();
    }
  });

  it('ships a manual direct-deploy workflow that renders config, applies remote D1 changes, syncs the worker secret, deploys the canonical worker, and runs smoke', () => {
    const workflow = readFileSync(deployWorkflow, 'utf8');
    const smokeSpec = readFileSync(deploySmokeSpec, 'utf8');

    expect(workflow).toContain('workflow_dispatch:');
    expect(workflow).not.toContain('\npush:');
    expect(workflow).toContain('confirm_production_deploy');
    expect(workflow).toContain('environment: production');
    expect(workflow).toContain('BROKER_D1_DATABASE_ID_PRODUCTION');
    expect(workflow).toContain('OPENROUTER_MANAGED_API_KEY_PRODUCTION');
    expect(workflow).toContain('BROKER_CANONICAL_WORKERS_DEV_URL');
    expect(workflow).toContain('ref: refs/heads/dev');
    expect(workflow).toContain('render-production-wrangler-config.mjs');
    expect(workflow).toContain('render-fingerprint-bootstrap-sql.mjs');
    expect(workflow).toContain("working-directory: broker");
    expect(workflow).toContain("deploy_dir='.deploy-direct'");
    expect(workflow).toContain('wrangler.production.jsonc');
    expect(workflow).toContain('fingerprint-bootstrap.sql');
    expect(workflow).toMatch(/wrangler types --config/u);
    expect(workflow).toContain('BROKER_CANONICAL_WORKERS_DEV_URL is required');
    expect(workflow).toContain('refs/heads/dev');
    expect(workflow).toMatch(
      /wrangler d1 migrations apply\s+puripuly-heart-broker\s+--remote\s+--config/u,
    );
    expect(workflow).toMatch(
      /wrangler d1 execute\s+puripuly-heart-broker\s+--remote\s+--config/u,
    );
    expect(workflow).toMatch(
      /wrangler secret put OPENROUTER_MANAGED_API_KEY --config/u,
    );
    expect(workflow).toMatch(/wrangler deploy --config/u);
    expect(workflow).toContain(
      'broker/tests/deploy-smoke/canonical-production.spec.ts',
    );
    expect(workflow).toContain('curl --fail');
    expect(workflow).toContain('timeout-minutes: 10');
    expect(workflow).toContain('app / public traffic');
    expect(smokeSpec).toContain("process.env.CI === 'true'");
  });
});

function createTempDir(): string {
  const tempDir = mkdtempSync(join(tmpdir(), 'broker-direct-deploy-'));
  tempDirs.push(tempDir);
  return tempDir;
}

function runNodeScript(scriptUrl: URL, args: string[]): string {
  return execFileSync(process.execPath, [fileURLToPath(scriptUrl), ...args], {
    encoding: 'utf8',
  });
}
