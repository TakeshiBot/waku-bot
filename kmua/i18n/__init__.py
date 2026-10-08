from pathlib import Path
from random import choice
from typing import Any

import yaml

DEFAULT_LOCALE = "vi"


class _CatalogueLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently losing a translation."""


def _unique_mapping(loader: _CatalogueLoader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"Duplicate translation key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_CatalogueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def normalize_locale(locale: str | None) -> str:
    """Normalize common language tags without discarding custom joke locales."""
    if not locale:
        return DEFAULT_LOCALE
    tag = locale.strip().replace("_", "-")
    language = tag.split("-", 1)[0].lower()
    if language in {"vi", "en"}:
        return language
    if language == "zh":
        return (
            "zh-Hant"
            if tag.lower().startswith("zh-hant")
            or tag.lower() in {"zh-tw", "zh-hk", "zh-mo"}
            else "zh-CN"
        )
    if language == "ja":
        return "ja-JP"
    if language == "ko":
        return "ko-KR"
    return tag


class I18n:
    def __init__(
        self,
        locales_dir: Path = Path(__file__).parent / "locales",
        default_locale: str = DEFAULT_LOCALE,
    ):
        self.locales_dir = locales_dir
        self.default_locale = normalize_locale(default_locale)
        self.translations: dict[str, dict[str, Any]] = {}
        self.available_locales = set()

        self.load_translations()

    def load_translations(self):
        if not self.locales_dir.exists():
            raise FileNotFoundError(
                f"Translation directory '{self.locales_dir}' does not exist"
            )

        for locale_dir in sorted(self.locales_dir.iterdir()):
            if locale_dir.is_dir():
                locale_name = locale_dir.name
                self.available_locales.add(locale_name)
                self.translations[locale_name] = {}
                self._load_locale_files(locale_dir, locale_name)

    def _load_locale_files(self, locale_dir: Path, locale_name: str):
        yaml_files = sorted(
            [*locale_dir.glob("**/*.yaml"), *locale_dir.glob("**/*.yml")]
        )

        for yaml_file in yaml_files:
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    content = yaml.load(f, Loader=_CatalogueLoader)
                    if content:
                        if not isinstance(content, dict):
                            raise ValueError("Catalogue root must be a mapping")
                        self._merge_translations(
                            self.translations[locale_name], content
                        )
            except Exception as e:
                raise ValueError(
                    f"Could not load translation file {yaml_file}: {e}"
                ) from e

    def _merge_translations(self, target: dict[str, Any], source: dict[str, Any]):
        for key, value in source.items():
            if (
                isinstance(value, dict)
                and key in target
                and isinstance(target[key], dict)
            ):
                self._merge_translations(target[key], value)
            else:
                target[key] = value

    def _get_nested_value(self, data: dict[str, Any], key: str) -> Any:
        keys = key.split(".")
        current = data

        try:
            for k in keys:
                current = current[k]
            return current
        except (KeyError, TypeError):
            return None

    def t(self, key: str, locale: str | None = "") -> str:
        """
        翻译指定的键

        Args:
            key: 翻译键, 使用点分隔的字符串表示嵌套
            locale: 目标语言

        Returns:
            翻译后的字符串，如果找不到则返回原键
        """
        locale = normalize_locale(locale) if locale else self.default_locale

        if locale not in self.translations:
            if self.default_locale in self.translations:
                locale = self.default_locale
            else:
                return key

        translation = self._get_nested_value(self.translations[locale], key)

        if translation is None and locale != self.default_locale:
            if self.default_locale in self.translations:
                translation = self._get_nested_value(
                    self.translations[self.default_locale], key
                )

        return translation if isinstance(translation, str) else key

    def trl(self, key: str, locale: str | None = "") -> str:
        """
        translate a list and return a random value from the list
        """
        locale = normalize_locale(locale) if locale else self.default_locale

        if locale not in self.translations:
            if self.default_locale in self.translations:
                locale = self.default_locale
            else:
                return key

        translation = self._get_nested_value(self.translations[locale], key)

        if (
            isinstance(translation, list)
            and translation
            and all(isinstance(item, str) for item in translation)
        ):
            return choice(translation)
        elif translation is None and locale != self.default_locale:
            if self.default_locale in self.translations:
                translation = self._get_nested_value(
                    self.translations[self.default_locale], key
                )
                if (
                    isinstance(translation, list)
                    and translation
                    and all(isinstance(item, str) for item in translation)
                ):
                    return choice(translation)

        return translation if isinstance(translation, str) else key

    def get_raw(self, key: str, locale: str | None = "") -> Any:
        """获取任意类型的翻译值(dict/list), 解析规则同 t()。"""
        locale = normalize_locale(locale) if locale else self.default_locale
        if locale not in self.translations:
            if self.default_locale in self.translations:
                locale = self.default_locale
            else:
                return None
        value = self._get_nested_value(self.translations[locale], key)
        if value is None and locale != self.default_locale:
            if self.default_locale in self.translations:
                value = self._get_nested_value(
                    self.translations[self.default_locale], key
                )
        return value

    def get_available_locales(self) -> list[str]:
        return sorted(
            self.available_locales, key=lambda tag: (tag != DEFAULT_LOCALE, tag)
        )

    def set_default_locale(self, locale: str):
        locale = normalize_locale(locale)
        if locale in self.available_locales:
            self.default_locale = locale
        else:
            raise ValueError(f"Language '{locale}' is unavailable")

    def reload(self):
        self.translations.clear()
        self.available_locales.clear()
        self.load_translations()


i18n = I18n()
t = i18n.t
trl = i18n.trl
