-- scripts/verify-coach-cards.sql
-- Verification SQL for Sales Methodology Coach rollout.
-- Parameters via psql -v WINDOW='10 minutes'.
--
-- Usage:
--   psql -h <host> -U <user> -d <db> -v WINDOW="'10 minutes'" \
--        -f scripts/verify-coach-cards.sql

\set ON_ERROR_STOP on

-- 0. Confirm gateway_actions table + params column shape
\echo '== Schema confirm =='
\d+ gateway_actions

\echo ''
\echo '== Recent coach.proposal cards (within' :WINDOW ') =='

SELECT
  id,
  params->>'severity' AS severity,
  params->>'summary' AS summary,
  params->'action'->>'methodology_skill' AS skill,
  LEFT(params->>'reasoning', 80) AS reasoning_preview,
  created_at
FROM gateway_actions
WHERE kind = 'coach.proposal'
  AND created_at > NOW() - INTERVAL :WINDOW
ORDER BY created_at DESC;

\echo ''
\echo '== methodology_skill distribution =='

SELECT
  COALESCE(params->'action'->>'methodology_skill', '<NULL>') AS skill,
  COUNT(*) AS cards,
  COUNT(*) FILTER (WHERE params->>'severity' = 'high') AS high,
  COUNT(*) FILTER (WHERE params->>'severity' = 'medium') AS medium,
  COUNT(*) FILTER (WHERE params->>'severity' = 'low') AS low
FROM gateway_actions
WHERE kind = 'coach.proposal'
  AND created_at > NOW() - INTERVAL :WINDOW
GROUP BY 1
ORDER BY cards DESC;

\echo ''
\echo '== Pass criterion 1: cards with NULL methodology_skill (target: 0) =='

SELECT
  id,
  params->>'summary' AS summary,
  created_at
FROM gateway_actions
WHERE kind = 'coach.proposal'
  AND created_at > NOW() - INTERVAL :WINDOW
  AND (params->'action'->>'methodology_skill') IS NULL;

\echo ''
\echo '== Pass criterion 2: cards with unknown methodology_skill (target: 0) =='

SELECT
  id,
  params->'action'->>'methodology_skill' AS bad_skill,
  params->>'summary' AS summary
FROM gateway_actions
WHERE kind = 'coach.proposal'
  AND created_at > NOW() - INTERVAL :WINDOW
  AND params->'action'->>'methodology_skill' IS NOT NULL
  AND params->'action'->>'methodology_skill' NOT IN (
    'crm-sales-discovery',
    'crm-sales-meddpicc',
    'crm-sales-challenger',
    'crm-sales-pipeline',
    'crm-sales-cadence',
    'crm-sales-account',
    'crm-sales-proposal'
  );

\echo ''
\echo '== Sample 5 cards for human review (draft + reasoning) =='

SELECT
  id,
  params->'action'->>'methodology_skill' AS skill,
  params->>'reasoning' AS reasoning,
  params->'action'->>'draft' AS draft
FROM gateway_actions
WHERE kind = 'coach.proposal'
  AND created_at > NOW() - INTERVAL :WINDOW
ORDER BY created_at DESC
LIMIT 5;
