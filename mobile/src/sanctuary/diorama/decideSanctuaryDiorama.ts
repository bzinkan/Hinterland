/**
 * Pure decision logic for whether the Sanctuary diorama renders. Kept free
 * of React/Expo imports so it is trivially unit-testable.
 *
 * The classic text-first Sanctuary screen is the permanent fallback
 * (ADR 0011/0012): any "no" here lands on the classic screen, never on a
 * broken canvas.
 */

/** Renderer crashes tolerated before the session pins to the classic screen until an app update. */
export const MAX_RENDER_CRASHES = 3;

export type SanctuaryDioramaDecisionInput = {
  /** Build-time SANCTUARY_DIORAMA flag (eas.json env -> extra.sanctuaryDiorama). */
  buildFlagEnabled: boolean;
  /** TalkBack/VoiceOver active: the text-first classic screen is strictly better. */
  screenReaderEnabled: boolean;
  /** Kid-facing "Simple view" escape hatch in Settings. */
  simpleViewPreferred: boolean;
  /** Persisted count of renderer crashes / mount-watchdog trips. */
  crashCount: number;
};

export function decideSanctuaryDiorama(
  input: SanctuaryDioramaDecisionInput,
): boolean {
  return (
    input.buildFlagEnabled &&
    !input.screenReaderEnabled &&
    !input.simpleViewPreferred &&
    input.crashCount < MAX_RENDER_CRASHES
  );
}
