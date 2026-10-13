-- scripts/measure-coach-feedback.sql
-- 14-day rollout metric kit for Sales Methodology Coach.
--
-- Parameters via psql -v:
--   WINDOW_START='2026-05-31 00:00'    (inclusive)
--   WINDOW_END='2026-06-14 00:00'      (exclusive)
--
-- Usage:
--   psql ... \
--     -v WINDOW_START="'2026-05-31'" \
--     -v WINDOW_END="'2026-06-14'" \
--     -f scripts/measure-coach-feedback.sql

\set ON_ERROR_STOP on

\echo '== Outcome table discovery =='
-- The exact outcome table name may be gateway_action_outcomes, interactions, or
-- a derived view. Confirm here (per [Plan/reality drift] memory).
\dt *outcome*

\echo ''
\echo '== Overall feedback rate (window:' :WINDOW_START 'to' :WINDOW_END ') =='
-- ADAPT JOIN below to actual outcome table once \dt above reveals it.
-- Assumed shape: outcome table has action_id FK to gateway_actions.id +
-- a state/outcome column with values like 'copied'/'dismissed'/'no_action'/NULL.

WITH window_cards AS (
  SELECT id, params, params->>'severity' AS severity, created_at,
         params->'action'->>'methodology_skill' AS skill
  FROM gateway_actions
  WHERE kind = 'coach.proposal'
    AND created_at >= :WINDOW_START::timestamp
    AND created_at <  :WINDOW_END::timestamp
)
SELECT
  COUNT(*) AS total_cards,
  COUNT(DISTINCT skill) AS distinct_methodology_skills,
  COUNT(*) FILTER (WHERE skill IS NULL) AS legacy_no_skill_cards,
  COUNT(*) FILTER (WHERE params->>'severity' = 'high') AS high_severity,
  COUNT(*) FILTER (WHERE params->>'severity' = 'medium') AS medium_severity,
  COUNT(*) FILTER (WHERE params->>'severity' = 'low') AS low_severity
FROM window_cards;

\echo ''
\echo '== Cards per methodology skill =='
SELECT
  COALESCE(params->'action'->>'methodology_skill', '<legacy/NULL>') AS skill,
  COUNT(*) AS cards,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM gateway_actions
WHERE kind = 'coach.proposal'
  AND created_at >= :WINDOW_START::timestamp
  AND created_at <  :WINDOW_END::timestamp
GROUP BY 1
ORDER BY cards DESC;

\echo ''
\echo '== Per-profile per-day average =='
-- Profile column may be profile_id, user_id, or in payload — confirm via \d
-- gateway_actions if column doesn't resolve.
SELECT
  profile_id,
  COUNT(*) AS total_cards,
  COUNT(DISTINCT date_trunc('day', created_at)) AS active_days,
  ROUND(COUNT(*)::numeric /
        GREATEST(COUNT(DISTINCT date_trunc('day', created_at)), 1), 2)
    AS avg_cards_per_active_day
FROM gateway_actions
WHERE kind = 'coach.proposal'
  AND created_at >= :WINDOW_START::timestamp
  AND created_at <  :WINDOW_END::timestamp
GROUP BY 1
ORDER BY total_cards DESC;

\echo ''
\echo '== Outcome distribution (REQUIRES joining outcome table) =='
\echo 'Adapt this to actual outcome table name discovered above:'

-- Template — fill in actual JOIN once table name confirmed.
-- Expected output rows: copied / dismissed / no_action / NULL — counts and rates.
--
-- Example assuming gateway_action_outcomes(action_id, outcome):
--
-- SELECT
--   COALESCE(o.outcome, '<no_action>') AS outcome,
--   COUNT(*) AS count,
--   ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
-- FROM gateway_actions ga
-- LEFT JOIN gateway_action_outcomes o ON o.action_id = ga.id
-- WHERE ga.kind = 'coach.proposal'
--   AND ga.created_at >= :WINDOW_START::timestamp
--   AND ga.created_at <  :WINDOW_END::timestamp
-- GROUP BY 1
-- ORDER BY count DESC;

\echo ''
\echo '== Per-skill outcome — adapt JOIN, run after table name confirmed =='
\echo '(Skipping until outcome table identified by operator)'
