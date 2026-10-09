import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi as vitest } from "vitest";
import { nextTick } from "vue";

import StateBlock from "@/components/StateBlock.vue";
import DefinitionList from "@/components/DefinitionList.vue";
import { formatDate, formatDateTime, formatNumber, formatPercent } from "@/utils/format";
import { languageOptions, localeName } from "@/utils/locale";
import en from "./en.json";
import zhCN from "./zh-CN.json";
import vietnamese from "./vi.json";
import { DEFAULT_LOCALE, locale, setLocale, t, tError, tOptional } from "./index";

function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, node] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof node === "string") result[path] = node;
    else {
      expect(node, path).not.toBeNull();
      expect(typeof node, path).toBe("object");
      Object.assign(result, flatten(node as Record<string, unknown>, path));
    }
  }
  return result;
}

const catalogues = { vi: flatten(vietnamese), en: flatten(en), "zh-CN": flatten(zhCN) };

afterEach(() => setLocale(DEFAULT_LOCALE));

describe("catalogue integrity", () => {
  it("ships the same nonempty keys and placeholders in every locale", () => {
    const expectedKeys = Object.keys(catalogues.vi).sort();
    const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    for (const [language, catalogue] of Object.entries(catalogues)) {
      expect(Object.keys(catalogue).sort(), language).toEqual(expectedKeys);
      for (const key of expectedKeys) {
        expect(catalogue[key]?.trim(), `${language}:${key}`).toBeTruthy();
        expect(placeholders(catalogue[key]!), `${language}:${key}`).toEqual(
          placeholders(catalogues.vi[key]!),
        );
      }
    }
  });

  it("has no Chinese text or replacement characters in Vietnamese messages", () => {
    for (const [key, text] of Object.entries(catalogues.vi)) {
      expect(text, key).not.toMatch(/[\p{Script=Han}\uFFFD]/u);
    }
  });

  it("resolves every literal translation key used by frontend source", () => {
    const sources = import.meta.glob("../**/*.{vue,ts}", {
      query: "?raw",
      import: "default",
      eager: true,
    });
    for (const [path, raw] of Object.entries(sources)) {
      if (path.endsWith(".spec.ts")) continue;
      for (const match of String(raw).matchAll(/\bt(?:Optional)?\(\s*["']([^"']+)["']/g)) {
        expect(catalogues.vi[match[1]!], `${path}:${match[1]}`).toBeDefined();
      }
    }
  });
});

describe("locale selection and display", () => {
  it("starts in Vietnamese before any profile is loaded", async () => {
    vitest.resetModules();
    const fresh = await import("./index");
    expect(fresh.locale.value).toBe("vi");
    expect(fresh.t("app.save")).toBe("Lưu");
    expect(document.documentElement.lang).toBe("vi");
  });

  it.each(["vi", "vi-VN", "vi_VN", "VI-vn", " vi "])("normalizes %s to Vietnamese", (tag) => {
    setLocale(tag);
    expect(locale.value).toBe("vi");
    expect(t("me.lang")).toBe("Ngôn ngữ");
    expect(document.documentElement.lang).toBe("vi");
  });

  it("preserves explicitly selected supported languages and their regional aliases", () => {
    setLocale("en-US");
    expect(t("app.save")).toBe("Save");
    expect(document.documentElement.lang).toBe("en");
    setLocale("zh-Hant");
    expect(t("app.save")).toBe("保存");
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it.each(["", "ja-JP", "invalid", "__proto__", "english", "vietnamese"])(
    "falls back safely for %s",
    (tag) => {
      setLocale(tag);
      expect(locale.value).toBe("vi");
    },
  );

  it("uses Vietnamese for unknown errors and keeps unknown keys visible", () => {
    setLocale("vi");
    expect(tError("NEW_ERROR")).toBe("Máy chủ gặp lỗi");
    expect(tOptional("missing.key")).toBeUndefined();
    expect(t("missing.key")).toBe("missing.key");
  });

  it("interpolates values without translating content or losing template tokens", () => {
    setLocale("vi");
    expect(t("app.saveCount", { count: 3 })).toBe("Lưu 3 thay đổi");
    expect(t("app.saveCount")).toBe("Lưu {count} thay đổi");
    expect(t("chats.greetingHint")).toContain("${user} và ${chat}");
    expect(t("gifts.effect", { comment: "中文 {count}" })).toBe("Hiệu ứng: 中文 {count}");
  });

  it("renders Vietnamese and reacts when the user changes language", async () => {
    setLocale("vi");
    const wrapper = mount(StateBlock, { props: { error: "Failure" } });
    expect(wrapper.get("button").text()).toBe("Thử lại");
    setLocale("en");
    await nextTick();
    expect(wrapper.get("button").text()).toBe("Retry");
    wrapper.unmount();
  });

  it("uses Vietnamese number, percentage and date formatting", () => {
    setLocale("vi-VN");
    expect(formatNumber(12345.6)).toBe("12.345,6");
    expect(formatPercent(0.125)).toBe(
      new Intl.NumberFormat("vi", { style: "percent", maximumFractionDigits: 2 }).format(0.125),
    );
    const date = "2026-10-09T10:30:00Z";
    expect(formatDate(date)).toBe(
      new Intl.DateTimeFormat("vi", {
        dateStyle: "medium",
        timeZone: "Asia/Ho_Chi_Minh",
      }).format(new Date(date)),
    );
    expect(formatDateTime(date)).toBe(
      new Intl.DateTimeFormat("vi", {
        dateStyle: "short",
        timeStyle: "short",
        timeZone: "Asia/Ho_Chi_Minh",
      }).format(new Date(date)),
    );
    expect(localeName("vi")).toBe("Tiếng Việt");
    expect(localeName("vi-VN")).toBe("Tiếng Việt");
  });

  it("localizes displayed counters while keeping identifiers exact", () => {
    setLocale("vi");
    const wrapper = mount(DefinitionList, {
      props: {
        items: [
          { label: "Members", value: 12345 },
          { label: "ID", value: -10012345, mono: true },
        ],
      },
    });
    expect(wrapper.findAll("dd").map((node) => node.text())).toEqual(["12.345", "-10012345"]);
    wrapper.unmount();
  });

  it("keeps a saved Vietnamese regional alias selected in canonical language lists", () => {
    expect(languageOptions(["vi", "en"], "vi-VN")).toEqual([
      { value: "vi", text: "Tiếng Việt" },
      { value: "en", text: "English" },
      { value: "vi-VN", text: "Tiếng Việt" },
    ]);
    expect(languageOptions(null, "vi_VN")).toEqual([{ value: "vi_VN", text: "Tiếng Việt" }]);
  });
});
