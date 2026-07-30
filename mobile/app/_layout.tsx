import FontAwesome from '@expo/vector-icons/FontAwesome';
import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { QueryClientProvider } from '@tanstack/react-query';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import 'react-native-reanimated';

import { useColorScheme } from '@/components/useColorScheme';
import { queryClient } from '@/src/api/queryClient';
import { ensureDevSession } from '@/src/auth/devSession';
import { ensureTokenSync as ensureMsalTokenSync } from '@/src/auth/msal';
import {
  AuthSessionCoordinator,
  ObservationQueueCoordinator,
} from '@/src/auth/AuthSessionCoordinator';
import { env } from '@/src/config/env';
import {
  probeReleaseLane,
  ReleaseLaneConfigurationError,
  type ReleaseLaneProbe,
} from '@/src/config/releaseLane';

export {
  // Catch any errors thrown by the Layout component.
  ErrorBoundary,
} from 'expo-router';

export const unstable_settings = {
  // Ensure that reloading on `/modal` keeps a back button present.
  initialRouteName: '(tabs)',
};

// Prevent the splash screen from auto-hiding before asset loading is complete.
SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [loaded, error] = useFonts({
    SpaceMono: require('../assets/fonts/SpaceMono-Regular.ttf'),
    ...FontAwesome.font,
  });
  const [gateAttempt, setGateAttempt] = useState(0);
  const [laneGate, setLaneGate] = useState<
    | { kind: 'checking' }
    | { kind: 'ready'; probe: ReleaseLaneProbe }
    | { kind: 'blocked'; error: ReleaseLaneConfigurationError }
  >({ kind: 'checking' });

  // Expo Router uses Error Boundaries to catch errors in the navigation tree.
  useEffect(() => {
    if (error) throw error;
  }, [error]);

  useEffect(() => {
    if (loaded) {
      SplashScreen.hideAsync();
    }
  }, [loaded]);

  useEffect(() => {
    const controller = new AbortController();
    setLaneGate({ kind: 'checking' });
    void probeReleaseLane(controller.signal).then(
      (probe) => setLaneGate({ kind: 'ready', probe }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setLaneGate({
          kind: 'blocked',
          error:
            error instanceof ReleaseLaneConfigurationError
              ? error
              : new ReleaseLaneConfigurationError(
                  'The server identity could not be verified.',
                  env.expectedApiLane,
                  null,
                ),
        });
      },
    );
    return () => controller.abort();
  }, [gateAttempt]);

  const retryLaneGate = useCallback(() => {
    setGateAttempt((attempt) => attempt + 1);
  }, []);

  if (!loaded) {
    return null;
  }

  if (laneGate.kind !== 'ready') {
    return (
      <ReleaseLaneGateScreen
        state={laneGate}
        onRetry={retryLaneGate}
      />
    );
  }

  return <RootLayoutNav probe={laneGate.probe} />;
}

function RuntimeBootstrap() {
  useEffect(() => {
    // No token, queue, or authenticated API work starts until the release
    // lane handshake above has succeeded.
    ensureMsalTokenSync();
    if (env.appEnv === 'development' || env.appEnv === 'preview') {
      void ensureDevSession();
    }
  }, []);
  return null;
}

function RootLayoutNav({ probe }: { probe: ReleaseLaneProbe }) {
  const colorScheme = useColorScheme();

  return (
    <QueryClientProvider client={queryClient}>
      <RuntimeBootstrap />
      <AuthSessionCoordinator />
      <ObservationQueueCoordinator />
      <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
        <View style={styles.appFrame}>
          {env.appEnv === 'adult-qa' && (
            <View
              accessibilityRole="text"
              accessibilityLabel="Adult QA, test data only"
              testID="adult-qa-banner"
              style={styles.qaBanner}
            >
              <Text style={styles.qaBannerText}>ADULT QA · TEST DATA ONLY</Text>
              <Text style={styles.qaBannerMeta}>
                {probe.reported} · {probe.apiVersion}
              </Text>
            </View>
          )}
          <Stack>
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="modal" options={{ presentation: 'modal' }} />
          </Stack>
        </View>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

function ReleaseLaneGateScreen({
  state,
  onRetry,
}: {
  state:
    | { kind: 'checking' }
    | { kind: 'blocked'; error: ReleaseLaneConfigurationError };
  onRetry: () => void;
}) {
  return (
    <View style={styles.gatePage}>
      <View style={styles.gateSeal}>
        <Text style={styles.gateEyebrow}>ADULT QA SAFETY CHECK</Text>
        <Text style={styles.gateTitle}>
          {state.kind === 'checking' ? 'Verifying test lane' : 'Connection blocked'}
        </Text>
        {state.kind === 'checking' ? (
          <View style={styles.gateProgress}>
            <ActivityIndicator color="#f2b544" />
            <Text style={styles.gateBody}>
              Capture and submission will unlock after the QA server confirms
              its identity.
            </Text>
          </View>
        ) : (
          <>
            <Text accessibilityRole="alert" style={styles.gateBody}>
              {state.error.message}
            </Text>
            <View style={styles.gateFacts}>
              <Text style={styles.gateFact}>
                Expected: {state.error.expected}
              </Text>
              <Text style={styles.gateFact}>
                Reported: {state.error.reported ?? 'not verified'}
              </Text>
            </View>
            <Text style={styles.gateHelp}>
              No sign-in, photo, or queue request was started. Check the
              connection or contact the adult QA operator.
            </Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Retry Adult QA server verification"
              onPress={onRetry}
              style={styles.gateButton}
            >
              <Text style={styles.gateButtonText}>Retry verification</Text>
            </Pressable>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  appFrame: {
    flex: 1,
  },
  qaBanner: {
    minHeight: 36,
    paddingHorizontal: 14,
    paddingVertical: 7,
    backgroundColor: '#17372c',
    borderBottomColor: '#f2b544',
    borderBottomWidth: 2,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 12,
  },
  qaBannerText: {
    color: '#fff8df',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 0.8,
  },
  qaBannerMeta: {
    color: '#c9dfd5',
    fontSize: 10,
  },
  gatePage: {
    flex: 1,
    backgroundColor: '#10271f',
    justifyContent: 'center',
    padding: 24,
  },
  gateSeal: {
    width: '100%',
    maxWidth: 520,
    alignSelf: 'center',
    backgroundColor: '#f5f0df',
    borderLeftColor: '#f2b544',
    borderLeftWidth: 8,
    padding: 24,
  },
  gateEyebrow: {
    color: '#6b5427',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.2,
    marginBottom: 8,
  },
  gateTitle: {
    color: '#142d24',
    fontSize: 28,
    fontWeight: '800',
    marginBottom: 14,
  },
  gateProgress: {
    gap: 12,
    alignItems: 'flex-start',
  },
  gateBody: {
    color: '#29483c',
    fontSize: 16,
    lineHeight: 23,
  },
  gateFacts: {
    borderColor: '#b9b39f',
    borderWidth: StyleSheet.hairlineWidth,
    marginTop: 18,
    padding: 12,
    gap: 4,
  },
  gateFact: {
    color: '#29483c',
    fontFamily: 'SpaceMono',
    fontSize: 12,
  },
  gateHelp: {
    color: '#53675f',
    fontSize: 13,
    lineHeight: 19,
    marginTop: 16,
  },
  gateButton: {
    minHeight: 48,
    backgroundColor: '#17372c',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 20,
    paddingHorizontal: 18,
  },
  gateButtonText: {
    color: '#fff8df',
    fontSize: 15,
    fontWeight: '700',
  },
});
