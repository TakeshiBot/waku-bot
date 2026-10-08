from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any

from kmua.i18n import t


class GiftRarity(IntEnum):
    COMMON = 1
    ENCHANTED = 2
    RARE = 3
    EPIC = 4
    LEGENDARY = 5


RARETY_DISPLAY_NAMES: dict[GiftRarity, str] = {
    GiftRarity.COMMON: "bot.hardcoded.gift.rarity.common",
    GiftRarity.ENCHANTED: "bot.hardcoded.gift.rarity.enchanted",
    GiftRarity.RARE: "bot.hardcoded.gift.rarity.rare",
    GiftRarity.EPIC: "bot.hardcoded.gift.rarity.epic",
    GiftRarity.LEGENDARY: "bot.hardcoded.gift.rarity.legendary",
}


def get_rarity_display_name(rarity: int, locale: str = "") -> str:
    try:
        rarity_enum = GiftRarity(rarity)
    except ValueError:
        return t("bot.hardcoded.unknown", locale=locale)
    return t(
        RARETY_DISPLAY_NAMES.get(rarity_enum, "bot.hardcoded.unknown"), locale=locale
    )


class GiftID(StrEnum):
    SEVERED_GRASS_SILENCE = "severed_grass_silence"
    VOW_LOTUS_SEAL = "vow_lotus_seal"
    AMARANTH_HEART_LAMP = "amaranth_heart_lamp"
    FROST_FLOWER_WHISPER = "frost_flower_whisper"
    DAWN_BELL_HERB = "dawn_bell_herb"
    OTHERWORLDLY_FLOWER = "otherworldly_flower"


GIFT_DISPLAY_NAMES: dict[GiftID, str] = {
    GiftID.SEVERED_GRASS_SILENCE: "bot.hardcoded.gift.items.severed_grass_silence.name",
    GiftID.VOW_LOTUS_SEAL: "bot.hardcoded.gift.items.vow_lotus_seal.name",
    GiftID.AMARANTH_HEART_LAMP: "bot.hardcoded.gift.items.amaranth_heart_lamp.name",
    GiftID.FROST_FLOWER_WHISPER: "bot.hardcoded.gift.items.frost_flower_whisper.name",
    GiftID.DAWN_BELL_HERB: "bot.hardcoded.gift.items.dawn_bell_herb.name",
    GiftID.OTHERWORLDLY_FLOWER: "bot.hardcoded.gift.items.otherworldly_flower.name",
}


def get_display_name(gift_id: GiftID, locale: str = "") -> str:
    key = GIFT_DISPLAY_NAMES.get(
        gift_id, GIFT_DISPLAY_NAMES[GiftID.OTHERWORLDLY_FLOWER]
    )
    return t(key, locale=locale)


@dataclass(frozen=True)
class Gift:
    id: GiftID
    description_key: str
    price: int
    effects: dict[str, Any]
    consumable: bool = True
    comment_key: str = ""

    def get_description(self, locale: str = "") -> str:
        return t(self.description_key, locale=locale)

    def get_comment(self, locale: str = "") -> str:
        return t(self.comment_key, locale=locale) if self.comment_key else ""

    @property
    def description(self) -> str:
        return self.get_description()

    @property
    def comment(self) -> str:
        return self.get_comment()


ALL_GIFTS: dict[GiftID, Gift] = {
    GiftID.SEVERED_GRASS_SILENCE: Gift(
        id=GiftID.SEVERED_GRASS_SILENCE,
        description_key="bot.hardcoded.gift.items.severed_grass_silence.description",
        price=4721,
        effects={},
        consumable=True,
        comment_key="bot.hardcoded.gift.items.severed_grass_silence.comment",
    ),
    GiftID.VOW_LOTUS_SEAL: Gift(
        id=GiftID.VOW_LOTUS_SEAL,
        description_key="bot.hardcoded.gift.items.vow_lotus_seal.description",
        price=2473,
        effects={"duration": 7200, "passivation": 3.7},
        consumable=True,
        comment_key="bot.hardcoded.gift.items.vow_lotus_seal.comment",
    ),
    GiftID.AMARANTH_HEART_LAMP: Gift(
        id=GiftID.AMARANTH_HEART_LAMP,
        description_key="bot.hardcoded.gift.items.amaranth_heart_lamp.description",
        price=983,
        effects={"add_affection": 263, "duration": 1800},
        consumable=True,
        comment_key="bot.hardcoded.gift.items.amaranth_heart_lamp.comment",
    ),
    GiftID.FROST_FLOWER_WHISPER: Gift(
        id=GiftID.FROST_FLOWER_WHISPER,
        description_key="bot.hardcoded.gift.items.frost_flower_whisper.description",
        price=3701,
        effects={},
        consumable=True,
        comment_key="bot.hardcoded.gift.items.frost_flower_whisper.comment",
    ),
    GiftID.DAWN_BELL_HERB: Gift(
        id=GiftID.DAWN_BELL_HERB,
        description_key="bot.hardcoded.gift.items.dawn_bell_herb.description",
        price=4549,
        effects={"unblock": True, "immune_duration": 1800},
        consumable=True,
        comment_key="bot.hardcoded.gift.items.dawn_bell_herb.comment",
    ),
}

OTHERWORLDLY_FLOWER = Gift(
    id=GiftID.OTHERWORLDLY_FLOWER,
    description_key="bot.hardcoded.gift.items.otherworldly_flower.description",
    price=9973,
    effects={},
    consumable=True,
    comment_key="bot.hardcoded.gift.items.otherworldly_flower.comment",
)


def get_gift_by_id(gift_id: GiftID) -> Gift:
    gift = ALL_GIFTS.get(gift_id)
    if gift is None:
        return OTHERWORLDLY_FLOWER
    return gift


def list_all_gifts() -> list[Gift]:
    return list(ALL_GIFTS.values())


def list_affordable_gifts(coins: int) -> list[Gift]:
    return [gift for gift in ALL_GIFTS.values() if gift.price <= coins]
