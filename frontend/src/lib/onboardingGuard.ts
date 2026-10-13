/**
 * First-run onboarding guard.
 *
 * Decides whether a logged-in user should land on the `/setup` wizard:
 * a user whose default agent profile has **no model configured** can't
 * actually use the agent yet, so we route them to setup once. Skipping
 * (or finishing) sets a per-session flag so we don't keep re-trapping
 * them; once a model is actually configured the check returns false on
 * its own (next session), so the redirect is self-clearing.
 *
 * Pure read-only: fetches profiles + the default profile's model config.
 * Fails OPEN — any error → `needsSetup=false` so a flaky probe never
 * traps the user out of the app.
 */
import { useCallback, useEffect, useState } from "react";
import {
  getModelConfig, listAgentProfiles,
  type CurrentUser, type ModelConfigResponse,
} from "@/lib/api";

const SKIP_KEY = "kacrm.onboarding.skipped";

/** A model counts as configured when there's a non-empty default model. */
export function isModelConfigured(cfg: ModelConfigResponse | null | undefined): boolean {
  if (!cfg) return false;
  if (typeof cfg.model === "string" && cfg.model.trim()) return true;
  const raw = cfg.providers_json;
  if (!raw) return false;
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    const dm = (parsed as { default_model?: unknown })?.default_model;
    return typeof dm === "string" && dm.trim().length > 0;
  } catch {
    return false;
  }
}

export interface OnboardingGuard {
  /** Still probing profiles/model config — caller should not redirect yet. */
  loading: boolean;
  /** Default profile exists but has no model → first-run setup needed. */
  needsSetup: boolean;
  /** User dismissed/finished setup this session — suppress auto-redirect. */
  dismissed: boolean;
  /** Default profile id (== username) to drive the wizard, or null. */
  profileId: string | null;
  /** Mark setup dismissed for this session (used by "enter chat" / skip). */
  dismiss: () => void;
}

export function useOnboardingGuard(
  user: CurrentUser | null,
  authLoading: boolean,
): OnboardingGuard {
  const [loading, setLoading] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try { return sessionStorage.getItem(SKIP_KEY) === "1"; } catch { return false; }
  });

  const dismiss = useCallback(() => {
    try { sessionStorage.setItem(SKIP_KEY, "1"); } catch { /* private mode */ }
    setDismissed(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (authLoading || !user) {
      setLoading(false);
      setNeedsSetup(false);
      setProfileId(null);
      return;
    }
    setLoading(true);
    (async () => {
      try {
        const { profiles } = await listAgentProfiles();
        const def = profiles.find((p) => p.is_default) ?? profiles[0] ?? null;
        if (!def) {
          if (!cancelled) { setNeedsSetup(false); setProfileId(null); }
          return;
        }
        const cfg = await getModelConfig(def.id);
        if (cancelled) return;
        setProfileId(def.id);
        setNeedsSetup(!isModelConfigured(cfg));
      } catch {
        // Fail open: never trap the user behind a flaky probe.
        if (!cancelled) { setNeedsSetup(false); setProfileId(null); }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.user_id, authLoading]);

  return { loading, needsSetup, dismissed, profileId, dismiss };
}
