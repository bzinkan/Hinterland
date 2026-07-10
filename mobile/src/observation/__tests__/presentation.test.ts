import {
  flowStepState,
  photoStatusLabel,
  photoStatusTone,
  rewardLabel,
} from "@/src/observation/presentation";

describe("observation presentation helpers", () => {
  it("marks flow steps relative to the current step", () => {
    expect(flowStepState("upload", "photo")).toBe("complete");
    expect(flowStepState("upload", "upload")).toBe("active");
    expect(flowStepState("upload", "saved")).toBe("upcoming");
  });

  it("maps photo moderation states to kid-facing labels and tones", () => {
    expect(photoStatusLabel("pending")).toBe("Review pending");
    expect(photoStatusTone("pending")).toBe("neutral");
    expect(photoStatusLabel("clean")).toBe("Approved");
    expect(photoStatusTone("clean")).toBe("success");
    expect(photoStatusLabel("quarantine")).toBe("Needs review");
    expect(photoStatusTone("quarantine")).toBe("warning");
  });

  it("keeps reward labels short enough for badges", () => {
    expect(rewardLabel("first_find")).toBe("Dex");
    expect(rewardLabel("world_unlock")).toBe("Sanctuary");
    expect(rewardLabel("mission_complete")).toBe("Mission");
  });
});
