import uuid

from pyrogram import types
from pyrogram.client import Client

from waku import database
from waku.common.memory_store import memttlcache
from waku.i18n import t

from . import drawer as manodrawer
from . import utils


async def handle_manomeme(
    client: Client,
    query: types.InlineQuery,
    datas: list[str],
):
    lang = (await database.get_user_config(query.from_user)).lang
    if not datas:
        await query.answer(
            results=[
                utils.result_anan_tips(lang),
                utils.result_trial_tips(manodrawer.Character.EMA, lang),
                utils.result_trial_tips(manodrawer.Character.HIRO, lang),
            ],
        )
        return
    meme_type = datas[0]
    match meme_type:
        case "anan":
            # anan face text.
            if len(datas) < 3:
                await query.answer(
                    results=[utils.result_anan_tips(lang)],
                )
                return
            face = utils.resolve_face(datas[1])
            if face is None:
                await query.answer(
                    results=[utils.result_anan_tips(lang)],
                )
                return
            text = " ".join(datas[2:])
            if len(text) > 233:
                text = text[:233]
            data = {
                "type": "anan",
                "face": face,
                "text": text,
            }
            dataid = uuid.uuid4().hex
            await memttlcache.set(f"manomeme_inline:{dataid}", data, 300)
            await query.answer(
                results=[
                    types.InlineQueryResultArticle(
                        title=t("bot.hardcoded.meme.anan_result", locale=lang).format(
                            face=utils.face_display(face, lang)
                        ),
                        description=t(
                            "bot.hardcoded.meme.generated_later", locale=lang
                        ),
                        id=f"ms_{dataid}",
                        input_message_content=types.InputTextMessageContent(
                            message_text=t(
                                "bot.hardcoded.meme.anan_writing", locale=lang
                            ),
                        ),
                        thumb_url="https://kmua.unv.app/assets/manosaba/anan_example.webp",
                        reply_markup=utils.markup_anan_tips(lang),
                    )
                ]
            )
            return
        case "trial":
            # trial character (statement text)...
            # Both full-width and ASCII square brackets are supported.
            if len(datas) < 2:
                await query.answer(
                    results=[
                        utils.result_trial_tips(manodrawer.Character.EMA, lang),
                        utils.result_trial_tips(manodrawer.Character.HIRO, lang),
                    ],
                )
                return
            character = manodrawer.get_character(datas[1])
            if len(datas) < 4:
                await query.answer(
                    results=[
                        utils.result_trial_tips(manodrawer.Character.EMA, lang)
                        if character == manodrawer.Character.EMA
                        else utils.result_trial_tips(manodrawer.Character.HIRO, lang),
                    ],
                )
                return
            options = utils.parse_options(" ".join(datas[2:]))
            if not options:
                await query.answer(
                    results=[
                        utils.result_trial_tips(manodrawer.Character.EMA, lang),
                        utils.result_trial_tips(manodrawer.Character.HIRO, lang),
                    ],
                )
                return
            if len(options) > 4:
                options = options[:4]
            for opt in options:
                if len(opt.text) > 100:
                    opt.text = opt.text[:100]
            data = {
                "type": "trial",
                "character": character,
                "options": options,
            }
            dataid = uuid.uuid4().hex
            await memttlcache.set(f"manomeme_inline:{dataid}", data, 300)
            is_hiro = character == manodrawer.Character.HIRO
            title_char = character.get_display(lang)
            await query.answer(
                results=[
                    types.InlineQueryResultArticle(
                        title=f"{title_char} [{'|'.join([opt.statement.get_display(lang) for opt in options])}]",
                        description=t(
                            "bot.hardcoded.meme.generated_later", locale=lang
                        ),
                        id=f"ms_{dataid}",
                        input_message_content=types.InputTextMessageContent(
                            message_text=t(
                                "bot.hardcoded.meme.ema_busy", locale=lang
                            ).format(name=title_char)
                            if not is_hiro
                            else t("bot.hardcoded.meme.hiro_busy", locale=lang).format(
                                name=title_char
                            ),
                        ),
                        reply_markup=(
                            utils.markup_trial_tips(manodrawer.Character.EMA, lang)
                            if not is_hiro
                            else utils.markup_trial_tips(
                                manodrawer.Character.HIRO, lang
                            )
                        ),
                        thumb_url=f"https://kmua.unv.app/assets/manosaba/{'emadog' if not is_hiro else 'hirocat'}.webp",
                    )
                ]
            )
            return
