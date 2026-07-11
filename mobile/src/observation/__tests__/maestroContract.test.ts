import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

describe("Play Internal Maestro contract", () => {
  const root = process.cwd();
  const flow = readFileSync(
    join(root, ".maestro", "flows", "observation_play_internal.yaml"),
    "utf8",
  );
  const config = readFileSync(join(root, ".maestro", "config.yaml"), "utf8");

  it("targets the store package and the committed media fixture", () => {
    expect(flow).toContain("appId: app.thehinterlandguide");
    expect(flow).toContain("../../assets/images/icon.png");
    expect(existsSync(join(root, "assets", "images", "icon.png"))).toBe(true);
    expect(config).toContain('"flows/*.yaml"');
    expect(config).toContain("observation_play_internal");
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
