import { afterEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
  retrieveLaunchParams: vi.fn(() => ({}) as { tgWebAppStartParam?: string }),
  retrieveRawInitData: vi.fn(() => "signed-init-data"),
}));
vi.mock("@telegram-apps/sdk", () => sdk);

import { launchContext } from "./index";

afterEach(() => {
  window.history.replaceState({}, "", "/");
  sdk.retrieveLaunchParams.mockReturnValue({});
});

describe("group Mini App navigation", () => {
  it("uses the private launch hint without modifying signed initData", () => {
    window.history.replaceState({}, "", "/?waku_chat=c100123");
    expect(launchContext()).toEqual({ initDataRaw: "signed-init-data", startChatId: -100123 });
  });

  it("prefers Telegram's start parameter", () => {
    window.history.replaceState({}, "", "/?waku_chat=c100123");
    sdk.retrieveLaunchParams.mockReturnValue({ tgWebAppStartParam: "c100456" });
    expect(launchContext().startChatId).toBe(-100456);
  });

  it.each(["c0", "c9007199254740992", "c-100123", "garbage", "c123admin"])(
    "rejects an invalid navigation hint: %s",
    (hint) => {
      window.history.replaceState({}, "", `/?waku_chat=${hint}`);
      expect(launchContext().startChatId).toBeNull();
    },
  );
});
