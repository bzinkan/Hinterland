import { CameraView, useCameraPermissions } from "expo-camera";
import * as ImageManipulator from "expo-image-manipulator";
import { router } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Pressable,
  StyleSheet,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { useDraftStore } from "@/src/observation/draftStore";

const MAX_EDGE_PX = 1600;
const JPEG_QUALITY = 0.8;

type Captured = {
  uri: string;
  width: number;
  height: number;
};

export default function ObserveScreen() {
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Captured | null>(null);
  const setDraftPhoto = useDraftStore((s) => s.setPhoto);

  if (!permission) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.heading}>Camera access</Text>
        <Text style={styles.body}>
          Dragonfly uses your camera to take photos of plants and animals you
          find.
        </Text>
        <Pressable
          style={[styles.button, styles.buttonPrimary]}
          onPress={() => void requestPermission()}
        >
          <Text style={styles.buttonText}>
            {permission.canAskAgain ? "Allow camera" : "Open settings"}
          </Text>
        </Pressable>
      </View>
    );
  }

  if (preview) {
    return (
      <View style={styles.previewContainer}>
        <Image
          source={{ uri: preview.uri }}
          style={styles.previewImage}
          resizeMode="contain"
        />
        <Text style={styles.previewMeta}>
          {preview.width} × {preview.height}px
        </Text>
        <View style={styles.row}>
          <Pressable
            style={[styles.button, styles.buttonGhost]}
            onPress={() => setPreview(null)}
          >
            <Text style={styles.buttonText}>Retake</Text>
          </Pressable>
          <Pressable
            style={[styles.button, styles.buttonPrimary]}
            onPress={() => {
              setDraftPhoto({
                localUri: preview.uri,
                width: preview.width,
                height: preview.height,
              });
              setPreview(null);
              router.push("/observe-submit");
            }}
          >
            <Text style={styles.buttonText}>Use photo</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <CameraView ref={cameraRef} style={styles.camera} facing="back" />
      <Pressable
        style={[styles.shutter, busy && styles.shutterBusy]}
        disabled={busy}
        onPress={async () => {
          if (!cameraRef.current) return;
          setBusy(true);
          try {
            const shot = await cameraRef.current.takePictureAsync({
              quality: 1,
              skipProcessing: false,
            });
            if (!shot) return;
            const longEdge = Math.max(shot.width, shot.height);
            const resize =
              longEdge > MAX_EDGE_PX
                ? shot.width >= shot.height
                  ? { width: MAX_EDGE_PX }
                  : { height: MAX_EDGE_PX }
                : undefined;
            const out = await ImageManipulator.manipulateAsync(
              shot.uri,
              resize ? [{ resize }] : [],
              {
                compress: JPEG_QUALITY,
                format: ImageManipulator.SaveFormat.JPEG,
              },
            );
            setPreview({ uri: out.uri, width: out.width, height: out.height });
          } catch (err) {
            Alert.alert("Capture failed", String(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? (
          <ActivityIndicator color="#000" />
        ) : (
          <View style={styles.shutterInner} />
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#000",
  },
  camera: {
    flex: 1,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: {
    fontSize: 22,
    fontWeight: "600",
    marginBottom: 8,
  },
  body: {
    fontSize: 14,
    opacity: 0.7,
    textAlign: "center",
    marginBottom: 16,
  },
  shutter: {
    position: "absolute",
    bottom: 40,
    alignSelf: "center",
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: "#fff",
    alignItems: "center",
    justifyContent: "center",
  },
  shutterBusy: {
    opacity: 0.6,
  },
  shutterInner: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: "#fff",
    borderWidth: 3,
    borderColor: "#000",
  },
  previewContainer: {
    flex: 1,
    alignItems: "center",
    padding: 16,
  },
  previewImage: {
    flex: 1,
    width: "100%",
    marginBottom: 12,
  },
  previewMeta: {
    fontSize: 12,
    opacity: 0.6,
    marginBottom: 12,
  },
  row: {
    flexDirection: "row",
    gap: 12,
  },
  button: {
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 6,
  },
  buttonPrimary: {
    backgroundColor: "#2f6feb",
  },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonText: {
    fontSize: 14,
    color: "#fff",
  },
});
