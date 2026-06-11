/**
 * One placed Sanctuary element in the 3D scene: the manifest GLB when
 * modeled, the typed FallbackShape otherwise. Tap -> onInspect(element)
 * (the shared ElementInspectModal renders the authored content; this
 * component never invents copy).
 */

import React, { useMemo } from "react";

import type { SanctuaryElementDto } from "@/src/api/sanctuary";
import { useSanctuaryGLTF } from "@/src/sanctuary3d/assets/useSanctuaryGLTF";
import type { SanctuaryElementAsset } from "@/src/sanctuary3d/assets/manifest";
import type { PlacedElement } from "@/src/sanctuary3d/scenePlan";
import { FallbackShape } from "@/src/sanctuary3d/scene/FallbackShape";
import { ZONE_ACCENT_COLOR } from "@/src/sanctuary3d/scene/zoneColors";

export function ElementModel({
  placed,
  onInspect,
}: {
  placed: PlacedElement;
  onInspect: (element: SanctuaryElementDto) => void;
}) {
  const { element, asset, transform } = placed;
  return (
    <group
      position={[
        transform.position[0],
        transform.position[1],
        transform.position[2],
      ]}
      rotation={[0, transform.rotationY, 0]}
      scale={transform.scale}
      onClick={(event) => {
        event.stopPropagation();
        onInspect(element);
      }}
    >
      {asset ? (
        <ManifestModel asset={asset} element={element} />
      ) : (
        <FallbackShape
          elementType={element.element_type}
          color={ZONE_ACCENT_COLOR[element.zone_id]}
        />
      )}
    </group>
  );
}

function ManifestModel({
  asset,
  element,
}: {
  asset: SanctuaryElementAsset;
  element: SanctuaryElementDto;
}) {
  const { status, gltf } = useSanctuaryGLTF(asset.module);

  // Each placement needs its own scene-graph instance; GLTF.scene is shared
  // via the loader cache, so clone per mount (cheap for our model sizes).
  const instance = useMemo(
    () => (gltf ? gltf.scene.clone(true) : null),
    [gltf],
  );

  if (status === "error" || !instance) {
    // Loading or failed: hold the slot with the typed fallback so the
    // diorama never pops a hole mid-render.
    return (
      <FallbackShape
        elementType={element.element_type}
        color={ZONE_ACCENT_COLOR[element.zone_id]}
      />
    );
  }

  return (
    <primitive
      object={instance}
      scale={asset.scale}
      rotation={[0, asset.rotationY, 0]}
    />
  );
}
