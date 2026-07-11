import FontAwesome from "@expo/vector-icons/FontAwesome";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as ImageManipulator from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";
import { router } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useFocusEffect, useIsFocused } from "@react-navigation/native";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Platform,
  GestureResponderEvent,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ObservationFlowStepper } from "@/src/observation/ObservationFlowStepper";
import { useAuthSession } from "@/src/auth/session";
import { type DraftPhoto, useDraftStore } from "@/src/observation/draftStore";
import {
  discardQueuedObservation,
  getQueuedObservation,
  ObservationQueueFullError,
  persistObservationDraft,
} from "@/src/observation/observationQueue";
import { createSubmissionUlid } from "@/src/observation/ulid";
import { useObservationQueue } from "@/src/observation/useObservationQueue";
import { mayRenderLocalPhoto } from "@/src/observation/queuePolicy";
import {
  beginPickerRequest,
  clearPickerRequest,
  pickerMarkerMatches,
  readPickerRequestMarker,
  type PickerRequestMarker,
} from "@/src/observation/pickerRecovery";
import { listMyExpeditions, type ProgressItem } from "@/src/api/expeditions";
import {
  activeProgress,
  nextObjective,
  progressLabel,
} from "@/src/expeditions/logic";

const MAX_EDGE_PX = 1600;
const JPEG_QUALITY = 0.8;
const MIN_CAMERA_ZOOM = 0;
const MAX_CAMERA_ZOOM = 0.8;
const PINCH_ZOOM_SENSITIVITY = 0.25;
const CAMERA_RESTART_DELAY_MS = 180;

type Captured = {
  uri: string;
  width: number;
  height: number;
  source: "camera" | "library";
};

type Mode = "intro" | "camera" | "preview";

function clampZoom(value: number) {
  return Math.min(MAX_CAMERA_ZOOM, Math.max(MIN_CAMERA_ZOOM, value));
}

function formatZoom(value: number) {
  return `${(1 + value * 4).toFixed(1)}x`;
}

function touchDistance(event: GestureResponderEvent) {
  const touches = event.nativeEvent.touches;
  if (touches.length < 2) return null;
  const [first, second] = touches;
  return Math.hypot(first.pageX - second.pageX, first.pageY - second.pageY);
}

export default function ObserveScreen() {
  const session = useAuthSession();
  const ownerUserId = session.status === "authenticated" ? session.user.id : null;
  const [permission, requestPermission] = useCameraPermissions();
  const isFocused = useIsFocused();
  const cameraRef = useRef<CameraView>(null);
  const zoomRef = useRef(0);
  const pinchStartDistanceRef = useRef<number | null>(null);
  const pinchStartZoomRef = useRef(0);
  const [mode, setMode] = useState<Mode>("intro");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<DraftPhoto | null>(null);
  const [cameraMounted, setCameraMounted] = useState(false);
  const [cameraSession, setCameraSession] = useState(0);
  const [zoom, setZoom] = useState(0);
  const setDraftPhoto = useDraftStore((s) => s.setPhoto);
  const clearDraft = useDraftStore((s) => s.clear);
  const queue = useObservationQueue(ownerUserId);
  const mission = useQuery({
    queryKey: ["expeditions", ownerUserId ?? "anonymous", "me"],
    queryFn: ({ signal }) => listMyExpeditions(signal),
    retry: false,
    enabled: ownerUserId != null,
  });

  const setCameraZoom = useCallback((value: number) => {
    const next = clampZoom(value);
    zoomRef.current = next;
    setZoom(next);
  }, []);

  const resetPinch = useCallback(() => {
    pinchStartDistanceRef.current = null;
    pinchStartZoomRef.current = zoomRef.current;
  }, []);

  const updatePinchZoom = useCallback(
    (event: GestureResponderEvent) => {
      const distance = touchDistance(event);
      if (distance === null) {
        resetPinch();
        return;
      }
      if (pinchStartDistanceRef.current === null) {
        pinchStartDistanceRef.current = distance;
        pinchStartZoomRef.current = zoomRef.current;
        return;
      }
      const safeScale = Math.max(distance / pinchStartDistanceRef.current, 0.01);
      setCameraZoom(
        pinchStartZoomRef.current +
          Math.log2(safeScale) * PINCH_ZOOM_SENSITIVITY,
      );
    },
    [resetPinch, setCameraZoom],
  );

  useEffect(() => {
    if (mode !== "camera" || !isFocused) {
      setCameraMounted(false);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      if (cancelled) return;
      setCameraSession((sessionNumber) => sessionNumber + 1);
      setCameraMounted(true);
    }, CAMERA_RESTART_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      setCameraMounted(false);
    };
  }, [isFocused, mode]);

  useFocusEffect(
    useCallback(() => {
      if (!useDraftStore.getState().photo) {
        setPreview((current) => {
          if (current) setMode("intro");
          return null;
        });
      }
      setCameraZoom(0);
      resetPinch();
      return () => {
        setBusy(false);
        setCameraZoom(0);
        resetPinch();
      };
    }, [resetPinch, setCameraZoom]),
  );

  useEffect(() => {
    if (preview && preview.ownerUserId !== ownerUserId) {
      setPreview(null);
      setMode("intro");
    }
    const draft = useDraftStore.getState().photo;
    if (draft && draft.ownerUserId !== ownerUserId) clearDraft();
  }, [clearDraft, ownerUserId, preview]);

  useEffect(() => {
    if (Platform.OS !== "android" || !ownerUserId) return;
    let cancelled = false;
    void (async () => {
      const marker = await readPickerRequestMarker();
      // Never consume Android's pending result for a different account.
      if (cancelled || marker?.ownerUserId !== ownerUserId) return;
      const result = await ImagePicker.getPendingResultAsync();
      if (cancelled || !result) return;
      if ("code" in result || result.canceled) {
        await clearPickerRequest(marker);
        return;
      }
      const asset = result.assets[0];
      if (!asset) {
        await clearPickerRequest(marker);
        return;
      }
      try {
        await preparePhoto(
          asset.uri,
          asset.width,
          asset.height,
          "library",
          marker,
        );
      } catch (error) {
        Alert.alert("Photo recovery failed", messageFromError(error));
      } finally {
        await clearPickerRequest(marker);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ownerUserId]);

  async function preparePhoto(
    uri: string,
    width: number,
    height: number,
    source: "camera" | "library",
    marker?: PickerRequestMarker,
  ): Promise<void> {
    const expectedOwnerUserId = marker?.ownerUserId ?? ownerUserId;
    if (!expectedOwnerUserId || !ownerIsActive(expectedOwnerUserId)) {
      throw new Error("The active account changed. Start this observation again.");
    }
    if (marker && !(await pickerRequestIsCurrent(marker))) return;
    if (marker) {
      const existing = await getQueuedObservation(
        expectedOwnerUserId,
        marker.requestId,
      );
      if (existing) {
        if (!ownerIsActive(expectedOwnerUserId) || !(await pickerRequestIsCurrent(marker))) {
          return;
        }
        const recoveredDraft: DraftPhoto = {
          localUri: existing.localUri,
          width: existing.width,
          height: existing.height,
          submissionKey: existing.submissionKey,
          ownerUserId: existing.ownerUserId,
          observedAt: existing.observedAt,
          source: existing.source,
        };
        setDraftPhoto(recoveredDraft);
        if (mayRenderLocalPhoto(existing)) {
          setPreview(recoveredDraft);
          setMode("preview");
        } else {
          router.push("/observe-submit");
        }
        return;
      }
    }
    const observedAt = new Date().toISOString();
    const normalized = await normalizePhoto(uri, width, height);
    if (!ownerIsActive(expectedOwnerUserId)) return;
    if (marker && !(await pickerRequestIsCurrent(marker))) return;
    const submissionKey = marker?.requestId ?? (await createSubmissionUlid());
    const record = await persistObservationDraft({
      submissionKey,
      ownerUserId: expectedOwnerUserId,
      sourceUri: normalized.uri,
      width: normalized.width,
      height: normalized.height,
      source,
      observedAt,
    });
    if (!ownerIsActive(expectedOwnerUserId)) return;
    setPreview({
      localUri: record.localUri,
      width: record.width,
      height: record.height,
      submissionKey,
      ownerUserId: expectedOwnerUserId,
      observedAt,
      source,
    });
    setMode("preview");
  }

  async function handleStartCamera() {
    if (!permission?.granted) {
      const next = await requestPermission();
      if (!next.granted) {
        Alert.alert(
          "Camera not available",
          "You can still choose a photo from the library.",
        );
        return;
      }
    }
    setMode("camera");
  }

  async function handlePickFromLibrary() {
    if (!ownerUserId) return;
    setBusy(true);
    const initiatingOwnerUserId = ownerUserId;
    let marker: PickerRequestMarker | null = null;
    try {
      marker = await beginPickerRequest(initiatingOwnerUserId);
      if (!ownerIsActive(initiatingOwnerUserId)) {
        await clearPickerRequest(marker);
        return;
      }

      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ["images"],
        quality: 1,
        allowsEditing: false,
      });
      const asset = result.canceled ? null : result.assets[0];
      if (!asset) {
        await clearPickerRequest(marker);
        return;
      }
      if (!ownerIsActive(initiatingOwnerUserId)) {
        await clearPickerRequest(marker);
        return;
      }
      await preparePhoto(
        asset.uri,
        asset.width,
        asset.height,
        "library",
        marker,
      );
      await clearPickerRequest(marker);
    } catch (err) {
      if (marker) await clearPickerRequest(marker);
      Alert.alert("Photo pick failed", messageFromError(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleCapture() {
    if (!cameraRef.current) return;
    setBusy(true);
    try {
      const shot = await cameraRef.current.takePictureAsync({
        quality: 1,
        skipProcessing: false,
      });
      if (!shot) return;
      await preparePhoto(shot.uri, shot.width, shot.height, "camera");
    } catch (err) {
      Alert.alert("Capture failed", messageFromError(err));
    } finally {
      setBusy(false);
    }
  }

  if (session.status === "initializing") {
    return (
      <View style={styles.authGate}>
        <ActivityIndicator color="#f0b44c" />
      </View>
    );
  }

  if (session.status === "anonymous") {
    return (
      <View style={styles.authGate}>
        <Text style={styles.title}>Sign in to observe</Text>
        <Text style={styles.body}>
          A signed-in account keeps saved drafts and photos separate on this device.
        </Text>
        <Pressable
          style={[styles.button, styles.buttonPrimary, styles.authButton]}
          onPress={() => router.push("/sign-in")}
        >
          <Text style={styles.buttonText}>Sign in</Text>
        </Pressable>
      </View>
    );
  }

  if (mode === "camera") {
    const activeMission = activeProgress(
      mission.data?.items ?? [],
      mission.data?.active_expedition_id,
    );
    const objective = nextObjective(activeMission);
    const cameraActive = isFocused && cameraMounted;
    return (
      <View style={styles.cameraShell}>
        {cameraActive ? (
          <CameraView
            key={cameraSession}
            ref={cameraRef}
            style={styles.camera}
            facing="back"
            zoom={zoom}
          />
        ) : (
          <View style={[styles.camera, styles.cameraPlaceholder]}>
            <ActivityIndicator color="#fff" />
          </View>
        )}
        {cameraActive ? (
          <View
            style={styles.cameraTouchLayer}
            onStartShouldSetResponder={(event) =>
              event.nativeEvent.touches.length >= 2
            }
            onMoveShouldSetResponder={(event) =>
              event.nativeEvent.touches.length >= 2
            }
            onResponderGrant={updatePinchZoom}
            onResponderMove={updatePinchZoom}
            onResponderRelease={resetPinch}
            onResponderTerminate={resetPinch}
          />
        ) : null}
        <View style={styles.cameraOverlayTop}>
          <Pressable
            style={styles.iconButton}
            onPress={() => setMode(preview ? "preview" : "intro")}
          >
            <FontAwesome name="chevron-left" size={18} color="#fff" />
          </Pressable>
          <Text style={styles.cameraTitle}>Line up the find</Text>
          <View style={styles.iconButtonPlaceholder} />
        </View>
        {cameraActive ? (
          <Pressable style={styles.zoomBadge} onPress={() => setCameraZoom(0)}>
            <Text style={styles.zoomBadgeText}>{formatZoom(zoom)}</Text>
          </Pressable>
        ) : null}
        {cameraActive && activeMission && objective ? (
          <MissionBanner mission={activeMission} objective={objective.description} />
        ) : null}
        <Pressable
          style={[styles.shutter, (busy || !cameraActive) && styles.shutterBusy]}
          disabled={busy || !cameraActive}
          onPress={() => void handleCapture()}
        >
          {busy ? (
            <ActivityIndicator color="#07130f" />
          ) : (
            <View style={styles.shutterInner} />
          )}
        </Pressable>
      </View>
    );
  }

  if (mode === "preview" && preview && preview.ownerUserId === ownerUserId) {
    return (
      <ScrollView
        testID="observation-confirm-screen"
        contentContainerStyle={styles.container}
      >
        <ObservationFlowStepper current="photo" />
        <Image
          testID="observation-confirm-image"
          source={{ uri: preview.localUri }}
          style={styles.previewImage}
          resizeMode="contain"
        />
        <View style={styles.panel}>
          <Text style={styles.eyebrow}>Observation draft</Text>
          <Text style={styles.title}>Use this photo?</Text>
          <Text style={styles.body}>
            {preview.source === "camera" ? "Camera" : "Photo library"} image,
            resized to {preview.width} by {preview.height}px for upload.
          </Text>
          <View style={styles.nextList}>
            <FlowBullet icon="map-marker" label="Confirm place" />
            <FlowBullet icon="upload" label="Upload photo" />
            <FlowBullet icon="search" label="Pick an ID" />
            <FlowBullet icon="star" label="See rewards" />
          </View>
        </View>
        <View style={styles.actions}>
          <Pressable
            style={[styles.button, styles.buttonGhost]}
            onPress={() => {
              void discardQueuedObservation(
                preview.ownerUserId,
                preview.submissionKey,
              ).then(() => {
                setPreview(null);
                setMode("intro");
              });
            }}
          >
            <Text style={styles.buttonText}>Retake</Text>
          </Pressable>
          <Pressable
            testID="observation-confirm-button"
            style={[styles.button, styles.buttonPrimary]}
            onPress={() => {
              setDraftPhoto({
                ...preview,
              });
              router.push("/observe-submit");
            }}
          >
            <Text style={styles.buttonText}>Continue</Text>
          </Pressable>
        </View>
      </ScrollView>
    );
  }

  return (
    <ScrollView testID="observe-screen" contentContainerStyle={styles.container}>
      <View style={styles.hero}>
        <View style={styles.heroPhoto}>
          <FontAwesome name="leaf" size={44} color="#66a182" />
          <View style={styles.focusCornerTopLeft} />
          <View style={styles.focusCornerBottomRight} />
        </View>
        <Text style={styles.eyebrow}>Observe</Text>
        <Text style={styles.title}>Log a real outdoor find</Text>
        <Text style={styles.body}>
          Take or choose one photo. The app saves the find first, then handles
          ID help, rewards, and review around it.
        </Text>
      </View>

      <ObservationFlowStepper current="photo" />

      <View style={styles.panel}>
        <FlowBullet icon="camera" label="Photo becomes the observation draft" />
        <FlowBullet icon="map-marker" label="Coarse place is attached once" />
        <FlowBullet icon="upload" label="Upload lands in pending review" />
        <FlowBullet icon="trophy" label="Dispatcher returns rewards" />
      </View>

      {queue.items.length > 0 && (
        <View style={styles.panel}>
          <Text style={styles.eyebrow}>Saved on this device</Text>
          <Text style={styles.body}>
            {queue.items.length} {queue.items.length === 1 ? "observation is" : "observations are"} waiting for you.
          </Text>
          {queue.items.slice(0, 3).map((item) => (
            <Pressable
              testID="observation-queue-item"
              key={item.submissionKey}
              style={styles.queueRow}
              onPress={() => {
                setDraftPhoto({
                  localUri: item.localUri,
                  width: item.width,
                  height: item.height,
                  submissionKey: item.submissionKey,
                  ownerUserId: item.ownerUserId,
                  observedAt: item.observedAt,
                  source: item.source,
                });
                router.push("/observe-submit");
              }}
            >
              {mayRenderLocalPhoto(item) ? (
                <Image
                  source={{ uri: item.localUri }}
                  style={styles.queueThumb}
                  resizeMode="cover"
                />
              ) : (
                <View style={[styles.queueThumb, styles.queueThumbPrivate]}>
                  <FontAwesome name="lock" size={18} color="#f0b44c" />
                </View>
              )}
              <View style={styles.queueCopy}>
                <Text style={styles.flowLabel}>{queueLabel(item.stage)}</Text>
                <Text style={styles.queueMeta}>
                  {new Date(item.observedAt).toLocaleString()}
                </Text>
              </View>
              <FontAwesome name="chevron-right" size={14} color="#fff" />
            </Pressable>
          ))}
        </View>
      )}

      <View style={styles.actionsStack}>
        <Pressable
          testID="observation-camera-button"
          style={[styles.button, styles.buttonPrimary, busy && styles.buttonDisabled]}
          disabled={busy}
          onPress={() => void handleStartCamera()}
        >
          <FontAwesome name="camera" size={16} color="#fff" />
          <Text style={styles.buttonText}>Take photo</Text>
        </Pressable>
        <Pressable
          testID="observation-library-button"
          style={[styles.button, styles.buttonGhost, busy && styles.buttonDisabled]}
          disabled={busy}
          onPress={() => void handlePickFromLibrary()}
        >
          {busy ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <FontAwesome name="image" size={16} color="#fff" />
          )}
          <Text style={styles.buttonText}>Choose photo</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

function ownerIsActive(ownerUserId: string): boolean {
  const state = useAuthSession.getState();
  return state.status === "authenticated" && state.user.id === ownerUserId;
}

async function pickerRequestIsCurrent(
  marker: PickerRequestMarker,
): Promise<boolean> {
  const persisted = await readPickerRequestMarker();
  const state = useAuthSession.getState();
  const currentOwnerUserId =
    state.status === "authenticated" ? state.user.id : null;
  return pickerMarkerMatches(persisted, currentOwnerUserId, marker.requestId);
}

function MissionBanner({
  mission,
  objective,
}: {
  mission: ProgressItem;
  objective: string;
}) {
  return (
    <Pressable
      style={styles.missionBanner}
      onPress={() => router.push(`/expedition/${mission.expedition_id}`)}
    >
      <View style={styles.missionIcon}>
        <FontAwesome name="flag" size={14} color="#0f172a" />
      </View>
      <View style={styles.missionBody}>
        <Text style={styles.missionKicker}>{mission.title}</Text>
        <Text style={styles.missionObjective} numberOfLines={2}>
          {objective}
        </Text>
        <Text style={styles.missionProgress}>{progressLabel(mission)}</Text>
      </View>
      <FontAwesome name="chevron-right" size={13} color="#dbeafe" />
    </Pressable>
  );
}

function FlowBullet({ icon, label }: { icon: string; label: string }) {
  return (
    <View style={styles.flowBullet}>
      <View style={styles.flowIcon}>
        <FontAwesome name={icon as never} size={13} color="#f0b44c" />
      </View>
      <Text style={styles.flowLabel}>{label}</Text>
    </View>
  );
}

async function normalizePhoto(
  uri: string,
  width: number,
  height: number,
): Promise<Omit<Captured, "source">> {
  const longEdge = Math.max(width, height);
  const resize =
    longEdge > MAX_EDGE_PX
      ? width >= height
        ? { width: MAX_EDGE_PX }
        : { height: MAX_EDGE_PX }
      : undefined;
  const out = await ImageManipulator.manipulateAsync(
    uri,
    resize ? [{ resize }] : [],
    {
      compress: JPEG_QUALITY,
      format: ImageManipulator.SaveFormat.JPEG,
    },
  );
  return { uri: out.uri, width: out.width, height: out.height };
}

function messageFromError(err: unknown): string {
  if (err instanceof ObservationQueueFullError) return err.message;
  if (err instanceof Error && /too large|dimensions|no longer available/i.test(err.message)) {
    return err.message;
  }
  return "The photo could not be prepared. Try it again or choose another photo.";
}

function queueLabel(stage: string): string {
  switch (stage) {
    case "complete":
      return "Saved — see rewards";
    case "needs_attention":
      return "Needs attention";
    case "ready":
      return "Finish this observation";
    default:
      return "Saving — tap to check";
  }
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    padding: 18,
    paddingBottom: 32,
    backgroundColor: "#07130f",
  },
  authGate: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 28,
    backgroundColor: "#07130f",
  },
  authButton: {
    flex: 0,
    minWidth: 150,
    marginTop: 14,
  },
  hero: {
    backgroundColor: "transparent",
    marginTop: 6,
    marginBottom: 18,
  },
  heroPhoto: {
    height: 220,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 18,
    backgroundColor: "#18241f",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
  },
  focusCornerTopLeft: {
    position: "absolute",
    top: 18,
    left: 18,
    width: 34,
    height: 34,
    borderTopWidth: 3,
    borderLeftWidth: 3,
    borderColor: "#f0b44c",
  },
  focusCornerBottomRight: {
    position: "absolute",
    right: 18,
    bottom: 18,
    width: 34,
    height: 34,
    borderRightWidth: 3,
    borderBottomWidth: 3,
    borderColor: "#f0b44c",
  },
  eyebrow: {
    fontSize: 12,
    fontWeight: "800",
    color: "#66a182",
    textTransform: "uppercase",
  },
  title: {
    fontSize: 28,
    fontWeight: "900",
    marginTop: 4,
    color: "#fff",
  },
  body: {
    fontSize: 14,
    lineHeight: 20,
    opacity: 0.76,
    marginTop: 8,
    color: "#fff",
  },
  panel: {
    marginTop: 16,
    padding: 14,
    borderRadius: 8,
    backgroundColor: "#111916",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
  },
  nextList: {
    marginTop: 12,
    backgroundColor: "transparent",
  },
  flowBullet: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 7,
    backgroundColor: "transparent",
  },
  flowIcon: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(240,180,76,0.14)",
  },
  flowLabel: {
    flex: 1,
    fontSize: 14,
    fontWeight: "700",
    color: "#fff",
  },
  actionsStack: {
    gap: 10,
    marginTop: 18,
    backgroundColor: "transparent",
  },
  queueRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "rgba(255,255,255,0.15)",
    backgroundColor: "transparent",
  },
  queueThumb: {
    width: 48,
    height: 48,
    borderRadius: 6,
  },
  queueThumbPrivate: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#18241f",
  },
  queueCopy: {
    flex: 1,
    backgroundColor: "transparent",
  },
  queueMeta: {
    color: "#fff",
    opacity: 0.65,
    fontSize: 11,
    marginTop: 2,
  },
  actions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 16,
    backgroundColor: "transparent",
  },
  button: {
    minHeight: 48,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    flex: 1,
  },
  buttonPrimary: {
    backgroundColor: "#2f6feb",
  },
  buttonGhost: {
    borderColor: "rgba(255,255,255,0.32)",
    borderWidth: 1,
    backgroundColor: "transparent",
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  buttonText: {
    fontSize: 15,
    color: "#fff",
    fontWeight: "800",
  },
  cameraShell: {
    flex: 1,
    backgroundColor: "#000",
  },
  camera: {
    flex: 1,
  },
  cameraPlaceholder: {
    alignItems: "center",
    justifyContent: "center",
  },
  cameraTouchLayer: {
    ...StyleSheet.absoluteFillObject,
  },
  cameraOverlayTop: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    paddingTop: 54,
    paddingHorizontal: 16,
    paddingBottom: 18,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: "rgba(0,0,0,0.34)",
  },
  iconButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(0,0,0,0.45)",
  },
  iconButtonPlaceholder: {
    width: 40,
    height: 40,
    backgroundColor: "transparent",
  },
  cameraTitle: {
    fontSize: 16,
    fontWeight: "900",
    color: "#fff",
  },
  shutter: {
    position: "absolute",
    bottom: 40,
    alignSelf: "center",
    width: 76,
    height: 76,
    borderRadius: 38,
    backgroundColor: "#fff",
    alignItems: "center",
    justifyContent: "center",
  },
  shutterBusy: {
    opacity: 0.72,
  },
  zoomBadge: {
    position: "absolute",
    bottom: 130,
    alignSelf: "center",
    minWidth: 58,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: "rgba(0,0,0,0.56)",
    borderColor: "rgba(255,255,255,0.32)",
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: "center",
  },
  zoomBadgeText: {
    color: "#fff",
    fontSize: 13,
    fontWeight: "700",
  },
  missionBanner: {
    position: "absolute",
    top: 112,
    left: 14,
    right: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: "rgba(15,23,42,0.88)",
    borderColor: "rgba(139,220,182,0.55)",
    borderWidth: StyleSheet.hairlineWidth,
  },
  missionIcon: {
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: "#8bdcb6",
    alignItems: "center",
    justifyContent: "center",
  },
  missionBody: {
    flex: 1,
    backgroundColor: "transparent",
  },
  missionKicker: {
    color: "#8bdcb6",
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  missionObjective: {
    color: "#fff",
    fontSize: 14,
    fontWeight: "700",
    lineHeight: 18,
    marginTop: 1,
  },
  missionProgress: {
    color: "#cbd5e1",
    fontSize: 11,
    marginTop: 1,
  },
  shutterInner: {
    width: 58,
    height: 58,
    borderRadius: 29,
    backgroundColor: "#fff",
    borderWidth: 3,
    borderColor: "#07130f",
  },
  previewImage: {
    width: "100%",
    aspectRatio: 1,
    borderRadius: 8,
    marginTop: 12,
    backgroundColor: "#18241f",
  },
});
