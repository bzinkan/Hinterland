/**
 * Applies the dressing rules to the current ScenePlan: shore rocks are
 * always present (geology), zone vegetation appears once its zone is
 * awake and deep enough (manifest tierMin), and everything tints to the
 * sleeping grey in the invitation state.
 */

import React from "react";

import { getSceneryAsset } from "@/src/sanctuary3d/assets/manifest";
import type { ScenePlan } from "@/src/sanctuary3d/scenePlan";
import { DRESSING_RULES, DRESSING_TRANSFORMS } from "@/src/sanctuary3d/scene/dressing";
import { InstancedGLB } from "@/src/sanctuary3d/scene/InstancedGLB";

export function IslandDressing({ plan }: { plan: ScenePlan }) {
  const zoneByid = new Map(plan.zones.map((z) => [z.zoneId, z] as const));

  return (
    <group>
      {DRESSING_RULES.map((rule) => {
        const asset = getSceneryAsset(rule.key);
        if (!asset) return null;
        const transforms = DRESSING_TRANSFORMS.get(rule.key) ?? [];

        let visible: boolean;
        let dormant: boolean;
        if (rule.zone === "shore") {
          visible = true;
          dormant = plan.isInvitationState;
        } else {
          const zone = zoneByid.get(rule.zone);
          visible =
            !!zone && zone.unlocked && zone.depthTier >= asset.tierMin;
          dormant = false;
        }
        if (!visible) return null;

        return (
          <InstancedGLB
            key={rule.key}
            moduleId={asset.module}
            transforms={transforms}
            dormant={dormant}
          />
        );
      })}
    </group>
  );
}
