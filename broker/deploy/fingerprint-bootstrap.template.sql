-- Generated CI bootstrap SQL must only replace the migration placeholder value.
-- If the placeholder is already gone, this script fails before mutating broker_config.

CREATE TEMP TABLE _fingerprint_bootstrap_guard (
  ready INTEGER NOT NULL CHECK (ready = 1)
) STRICT;

INSERT INTO _fingerprint_bootstrap_guard (ready)
SELECT CASE
  WHEN (
    SELECT COUNT(*)
    FROM broker_config
    WHERE key = 'fingerprint_salt'
      AND json_extract(value, '$.current.salt') = '__BOOTSTRAP' || '_REQUIRED__'
  ) = 1 THEN 1
  ELSE 0
END;

UPDATE broker_config
SET value = json_set(value, '$.current.salt', '__BOOTSTRAP_REQUIRED__'),
    updated_at = CURRENT_TIMESTAMP
WHERE key = 'fingerprint_salt';

DROP TABLE _fingerprint_bootstrap_guard;
