import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "@/i18n";
import { parseBotMoment } from "./botTime";
import { formatDate, formatDateTime } from "./format";

describe("bot UTC+7 time", () => {
  beforeEach(() => setLocale("en"));
  afterEach(() => setLocale("vi"));

  it("converts whole-day filters to UTC, including the final millisecond", () => {
    expect(parseBotMoment("2026-10-01", false)).toBe("2026-09-30T17:00:00.000Z");
    expect(parseBotMoment("2026-10-01", true)).toBe("2026-10-01T16:59:59.999Z");
  });

  it("uses UTC+7 for bare times and respects explicit offsets", () => {
    expect(parseBotMoment(" 2026-10-01 01:30 ", false)).toBe("2026-09-30T18:30:00.000Z");
    expect(parseBotMoment("2026-10-01T01:30:00Z", false)).toBe("2026-10-01T01:30:00.000Z");
    expect(parseBotMoment("2026-10-01T01:30:00-04:00", false)).toBe("2026-10-01T05:30:00.000Z");
  });

  it("rejects invalid dates and times", () => {
    for (const value of ["", "invalid", "2026-02-30", "2026-10-01 24:00", "2026-10-01 01:60"]) {
      expect(parseBotMoment(value, false)).toBeUndefined();
    }
  });

  it("shows the correct Vietnam date across UTC midnight boundaries", () => {
    expect(formatDate("2026-09-30T18:00:00Z")).toBe("Oct 1, 2026");
    expect(formatDateTime("2026-09-30T18:00:00Z")).toContain("1:00 AM");
    expect(formatDateTime("2026-09-30T18:00:00")).toBe(formatDateTime("2026-09-30T18:00:00Z"));
  });
});
