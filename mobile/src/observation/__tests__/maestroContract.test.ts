import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

describe("Play Internal Maestro contract", () => {
  const root = process.cwd();
  const flow = readFileSync(
    join(root, ".maestro", "flows", "observation_play_internal.yaml"),
    "utf8",
  );
  const config = readFileSync(join(root, ".maestro", "config.yaml"), "utf8");
  const picker = readFileSync(
    join(root, ".maestro", "partials", "pick_first_android_image.yaml"),
    "utf8",
  );
  const normalizedFlow = flow.replace(/\r\n/g, "\n");
  const normalizedPicker = picker.replace(/\r\n/g, "\n");

  it("targets the store package and the committed media fixture", () => {
    expect(flow).toContain("appId: app.thehinterlandguide");
    expect(flow).toContain("../../assets/images/icon.png");
    expect(existsSync(join(root, "assets", "images", "icon.png"))).toBe(true);
    expect(config).toContain('"flows/*.yaml"');
    expect(config).toContain("observation_play_internal");
    expect(picker).toContain('text: "Photo taken on .*"');
    expect(picker).toContain('text: "^(Done|Add|Select)$"');
    expect(picker).toContain('id: "com.google.android.providers.media.module:id/icon_thumbnail"');
    expect(picker).toContain('id: "com.google.android.documentsui:id/thumbnail"');
  });

  it("pins every app interaction to a stable React Native testID", () => {
    const observe = readFileSync(join(root, "app", "(tabs)", "observe.tsx"), "utf8");
    const submit = readFileSync(join(root, "app", "observe-submit.tsx"), "utf8");
    const journal = readFileSync(join(root, "app", "(tabs)", "index.tsx"), "utf8");
    const detail = readFileSync(join(root, "app", "observation", "[id].tsx"), "utf8");
    const tabs = readFileSync(join(root, "app", "(tabs)", "_layout.tsx"), "utf8");
    const contracts = [
      ["tab-journal", tabs],
      ["tab-observe", tabs],
      ["observe-screen", observe],
      ["observation-library-button", observe],
      ["observation-confirm-screen", observe],
      ["observation-confirm-image", observe],
      ["observation-confirm-button", observe],
      ["observation-submit-screen", submit],
      ["observation-no-location-button", submit],
      ["observation-unknown-button", submit],
      ["observation-save-button", submit],
      ["observation-stage-complete", submit],
      ["observation-done-button", submit],
      ["field-journal-screen", journal],
      ["field-journal-observation-card", journal],
      ["field-journal-private-status", journal],
      ["field-journal-photo-image", journal],
      ["observation-detail-screen", detail],
      ["observation-detail-private-status", detail],
      ["observation-detail-photo-image", detail],
      ["observation-photo-helper-button", detail],
    ] as const;

    for (const [id, source] of contracts) {
      expect(flow).toContain(`id: "${id}"`);
      expect(source).toContain(`"${id}"`);
    }

    expect(flow).toContain("Your Field Journal is empty");
    expect(flow).toContain("This photo is private during the pilot.");
    expect(flow).toContain("assertNotVisible");
    expect(flow).toContain('takeScreenshot: "w1-pilot-private-observation"');
  });

  it("scrolls off-screen controls into view before tapping them", () => {
    const guardedTapIds = [
      "observation-library-button",
      "observation-confirm-button",
      "observation-no-location-button",
      "observation-unknown-button",
      "observation-save-button",
      "observation-done-button",
    ] as const;

    for (const id of guardedTapIds) {
      const guardedTap = [
        "- scrollUntilVisible:",
        "    element:",
        `      id: "${id}"`,
        "    direction: DOWN",
        "    timeout: 20000",
        "    speed: 40",
        "    visibilityPercentage: 100",
        "    centerElement: true",
        "- tapOn:",
        `    id: "${id}"`,
      ].join("\n");

      expect(normalizedFlow).toContain(guardedTap);
    }

    const guardedJournalCard = [
      "- scrollUntilVisible:",
      "    element:",
      '      id: "field-journal-observation-card"',
      "    direction: DOWN",
      "    timeout: 20000",
      "    speed: 40",
      "    visibilityPercentage: 100",
      "    centerElement: true",
      "- extendedWaitUntil:",
      "    visible:",
      '      id: "field-journal-private-status"',
      "    timeout: 30000",
      "- assertVisible:",
      '    id: "field-journal-observation-card"',
      '- assertVisible: "This photo is private during the pilot."',
      "- assertNotVisible:",
      '    id: "field-journal-photo-image"',
      "- tapOn:",
      '    id: "field-journal-observation-card"',
    ].join("\n");

    expect(normalizedFlow).toContain(guardedJournalCard);

    const manualInputScroll = [
      "- scrollUntilVisible:",
      "    element:",
      '      text: "Manual identification correction"',
      "    direction: DOWN",
      "    timeout: 20000",
      "    speed: 40",
      "    visibilityPercentage: 100",
      "    centerElement: true",
      "- assertNotVisible:",
      '    id: "observation-photo-helper-button"',
    ].join("\n");

    expect(normalizedFlow).toContain(manualInputScroll);
    expect(normalizedFlow.indexOf('takeScreenshot: "w1-pilot-private-observation"')).toBeLessThan(
      normalizedFlow.indexOf(manualInputScroll),
    );
  });

  it("confirms each Android picker selection before trying a fallback selector", () => {
    const confirmSelection = [
      "- tapOn:",
      '    text: "^(Done|Add|Select)$"',
      "    optional: true",
    ].join("\n");
    const selectors = [
      [
        "- tapOn:",
        '    text: "Photo taken on .*"',
        "    index: 0",
        "    optional: true",
      ].join("\n"),
      [
        "- tapOn:",
        '    id: "com.google.android.providers.media.module:id/icon_thumbnail"',
        "    index: 0",
        "    optional: true",
      ].join("\n"),
      [
        "- tapOn:",
        '    id: "com.google.android.documentsui:id/thumbnail"',
        "    index: 0",
        "    optional: true",
      ].join("\n"),
    ];

    for (const selector of selectors) {
      expect(normalizedPicker).toContain(`${selector}\n${confirmSelection}`);
    }
  });

  it("launches the system photo picker without broad-storage permission preflight", () => {
    const observe = readFileSync(join(root, "app", "(tabs)", "observe.tsx"), "utf8");

    expect(observe).toContain("ImagePicker.launchImageLibraryAsync");
    expect(observe).not.toContain("requestMediaLibraryPermissionsAsync");
  });

  it("exposes native embedded-update evidence in Settings", () => {
    const settings = readFileSync(join(root, "app", "(tabs)", "settings.tsx"), "utf8");

    expect(settings).toContain("Updates.channel");
    expect(settings).toContain("Updates.isEnabled");
    expect(settings).toContain("Updates.isEmbeddedLaunch");
    expect(settings).toContain('testID="settings-updates-channel"');
    expect(settings).toContain('testID="settings-updates-source"');
  });
});
