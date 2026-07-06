import FontAwesome from '@expo/vector-icons/FontAwesome';
import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { QueryClientProvider } from '@tanstack/react-query';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect } from 'react';
import { Platform } from 'react-native';
import 'react-native-reanimated';

import { useColorScheme } from '@/components/useColorScheme';
import { queryClient } from '@/src/api/queryClient';
import { ensureDevSession } from '@/src/auth/devSession';
import { ensureTokenSync as ensureFirebaseTokenSync } from '@/src/auth/firebase';
import { ensureTokenSync as ensureMsalTokenSync } from '@/src/auth/msal';
import { env } from '@/src/config/env';

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
    // ADR 0010 production/play-internal auth is Entra for adults and
    // Hinterland-issued session JWTs for kids. Firebase token sync is
    // development-only now; running it in play-internal would clear a
    // stored kid session when Firebase has no signed-in user.
    if (
      Platform.OS !== 'web' &&
      (env.appEnv === 'development' || env.appEnv === 'preview')
    ) {
      ensureFirebaseTokenSync();
    }
    ensureMsalTokenSync();
    // Silent dev auto-login: mint a sandbox kid session when this
    // pre-production build boots with no stored bearer token. Fire and
    // forget -- failures leave the normal signed-out UX untouched, and
    // the helper re-checks the full gate matrix (appEnv, stored token,
    // baked-in key) internally. Store builds carry devLoginKey=null so
    // this is unreachable there.
    if (env.appEnv === 'development' || env.appEnv === 'preview') {
      void ensureDevSession();
    }
  }, []);

  if (!loaded) {
    return null;
  }

  return <RootLayoutNav />;
}

function RootLayoutNav() {
  const colorScheme = useColorScheme();

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
        <Stack>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="modal" options={{ presentation: 'modal' }} />
        </Stack>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
