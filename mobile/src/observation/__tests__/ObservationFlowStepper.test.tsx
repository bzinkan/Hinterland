import renderer, { act } from "react-test-renderer";

import { ObservationFlowStepper } from "@/src/observation/ObservationFlowStepper";

describe("ObservationFlowStepper", () => {
  it("renders the durable upload stage in a component test", () => {
    let tree: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<ObservationFlowStepper current="upload" />);
    });
    expect(tree!.toJSON()).toBeTruthy();
  });
});
