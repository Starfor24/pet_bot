# -*- coding: utf-8 -*-
"""
Бот «Потерялся / Нашёлся» для канала @malakhovka_in_the_lens.

Как это работает:
  1. Пользователь пишет /lost или /found.
  2. Бот пошагово собирает данные: фото → вид → порода/окрас → пол →
     возраст → район → место → дата → контакт.
  3. Карточка уходит модератору с кнопками «Опубликовать» / «Отклонить».
  4. После одобрения бот постит карточку в канал.
  5. Кнопка «Найдено / Вернулся домой» закрывает объявление.
  6. /search — поиск по доске.

Запуск:
  pip install -q aiogram aiosqlite
  python bot.py
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

KIND_LABELS = {"lost": "ПОТЕРЯЛСЯ", "found": "НАШЁЛСЯ"}
KIND_EMOJI = {"lost": "🔴", "found": "🟢"}
SEX_LABELS = {"male": "мальчик", "female": "девочка", "unknown": "не знаю"}
ANIMAL_EMOJI = {"cat": "🐱", "dog": "🐶", "other": "🐾"}

# --------------------------------------------------------------------------- #
# Состояния сбора заявки
# --------------------------------------------------------------------------- #


class AdForm(StatesGroup):
    kind = State()
    photos = State()
    animal_type = State()
    breed_color = State()
    sex = State()
    age = State()
    district = State()
    location_detail = State()
    event_date = State()
    contact = State()


class SearchForm(StatesGroup):
    kind = State()
    animal_type = State()
    district = State()
    query = State()


# --------------------------------------------------------------------------- #
# Клавиатуры
# --------------------------------------------------------------------------- #

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔴 Потерялся"), KeyboardButton(text="🟢 Нашёлся")],
        [KeyboardButton(text="🔍 Поиск по доске")],
    ],
    resize_keyboard=True,
)

ANIMAL_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🐱 Кошка", callback_data="type:cat"),
            InlineKeyboardButton(text="🐶 Собака", callback_data="type:dog"),
            InlineKeyboardButton(text="🐾 Другое", callback_data="type:other"),
        ]
    ]
)

SEX_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="♂ Мальчик", callback_data="sex:male"),
            InlineKeyboardButton(text="♀ Девочка", callback_data="sex:female"),
            InlineKeyboardButton(text="Не знаю", callback_data="sex:unknown"),
        ]
    ]
)

SEARCH_KIND_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Потерялся", callback_data="skind:lost"),
            InlineKeyboardButton(text="🟢 Нашёлся", callback_data="skind:found"),
            InlineKeyboardButton(text="Все", callback_data="skind:all"),
        ]
    ]
)


def district_kb():
    b = InlineKeyboardBuilder()
    for i in range(0, len(config.DISTRICTS), 2):
        row = config.DISTRICTS[i : i + 2]
        for d in row:
            b.button(text=d, callback_data=f"district:{d}")
        b.adjust(2)
    return b.as_markup()


def contact_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📱 Телефон", callback_data="contact:phone")
    b.button(text="💬 Телеграм", callback_data="contact:tg")
    b.button(text="Оба", callback_data="contact:both")
    b.adjust(2)
    return b.as_markup()


def build_card_text(ad: dict) -> str:
    kind = KIND_EMOJI[ad["kind"]]
    label = KIND_LABELS[ad["kind"]]
    animal = ANIMAL_EMOJI.get(ad["animal_type"], "🐾")

    lines = [
        f"{kind} <b>{label}: {animal} {ad['breed_color'] or '—'}</b>",
        "",
    ]
    lines.append(f"📍 <b>Район:</b> {ad['district']}")
    if ad.get("location_detail"):
        lines.append(f"🏷 <b>Место:</b> {ad['location_detail']}")
    lines.append(f"🗓 <b>Когда:</b> {ad['event_date']}")
    lines.append(f"⚤ <b>Пол:</b> {SEX_LABELS.get(ad.get('sex'), '—')}")
    if ad.get("age"):
        lines.append(f"🎂 <b>Возраст:</b> {ad['age']}")
    if ad.get("pet_name"):
        lines.append(f"📛 <b>Кличка:</b> {ad['pet_name']}")
    if ad.get("comment"):
        lines.append(f"📝 <b>Комментарий:</b> {ad['comment']}")
    lines.append("")
    lines.append(f"👤 <b>Контакт:</b> {ad['contact']}")

    return "\n".join(lines)


def build_admin_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Опубликовать", callback_data=f"approve:{ad_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"reject:{ad_id}"
                ),
            ]
        ]
    )


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


# --------------------------------------------------------------------------- #
# Старт и меню
# --------------------------------------------------------------------------- #


@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "Привет! Я бот доски объявлений «Потерялся / Нашёлся». 🐾\n\n"
        "Хотите сообщить о пропавшем или найденном животном? "
        "Нажмите кнопку ниже или отправьте команду:\n"
        "• /lost — животное потерялось\n"
        "• /found — животное нашлось\n\n"
        "Все объявления проходят проверку и появляются в канале "
        f"{config.CHANNEL_ID}."
    )
    await message.answer(text, reply_markup=MAIN_KB)


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("lost"))
async def cmd_lost(message: Message, state: FSMContext):
    await state.set_state(AdForm.kind)
    await state.update_data(kind="lost")
    await ask_photos(message, state)


@router.message(Command("found"))
async def cmd_found(message: Message, state: FSMContext):
    await state.set_state(AdForm.kind)
    await state.update_data(kind="found")
    await ask_photos(message, state)


@router.message(F.text.in_(["🔴 Потерялся", "🟢 Нашёлся"]))
async def main_kb_handler(message: Message, state: FSMContext):
    kind = "lost" if message.text.startswith("🔴") else "found"
    await state.set_state(AdForm.kind)
    await state.update_data(kind=kind)
    await ask_photos(message, state)


async def ask_photos(message: Message, state: FSMContext):
    await state.set_state(AdForm.photos)
    await message.answer(
        "Пришлите, пожалуйста, <b>фото животного</b> (можно несколько, "
        "до 3 штук).\n\n"
        "Если фото нет — отправьте команду /skip.",
        reply_markup=ReplyKeyboardRemove(),
    )


# --------------------------------------------------------------------------- #
# Шаги сбора
# --------------------------------------------------------------------------- #


@router.message(AdForm.photos, F.photo)
async def step_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)

    if len(photos) >= 3:
        await proceed_to_type(message, state)
    else:
        await message.answer(
            f"Фото принято ({len(photos)}/3). "
            "Пришлите ещё фото или напишите /done, если достаточно."
        )


@router.message(AdForm.photos, Command("done"))
async def step_photos_done(message: Message, state: FSMContext):
    await proceed_to_type(message, state)


@router.message(AdForm.photos, Command("skip"))
async def step_photos_skip(message: Message, state: FSMContext):
    await state.update_data(photos=[])
    await proceed_to_type(message, state)


async def proceed_to_type(message: Message, state: FSMContext):
    await state.set_state(AdForm.animal_type)
    await message.answer("Кто потерялся / нашёлся?", reply_markup=ANIMAL_KB)


@router.callback_query(AdForm.animal_type, F.data.startswith("type:"))
async def step_animal_type(cq: CallbackQuery, state: FSMContext):
    await state.update_data(animal_type=cq.data.split(":")[1])
    await state.set_state(AdForm.breed_color)
    await cq.answer()
    await cq.message.answer(
        "Опишите животное: <b>порода и окрас</b> (например: "
        "«британская кошка, серый», «дворняга, рыжий с белым»)."
    )


@router.message(AdForm.breed_color)
async def step_breed_color(message: Message, state: FSMContext):
    await state.update_data(breed_color=message.text.strip())
    await state.set_state(AdForm.sex)
    await message.answer("Пол животного:", reply_markup=SEX_KB)


@router.callback_query(AdForm.sex, F.data.startswith("sex:"))
async def step_sex(cq: CallbackQuery, state: FSMContext):
    await state.update_data(sex=cq.data.split(":")[1])
    await state.set_state(AdForm.age)
    await cq.answer()
    await cq.message.answer(
        "Примерный возраст? (напишите «примерно 1 год», «котёнок», "
        "«взрослая» — или отправьте /skip, если не знаете)"
    )


@router.message(AdForm.age)
async def step_age(message: Message, state: FSMContext):
    age = message.text.strip()
    if age.lower().startswith("/"):
        age = None
    await state.update_data(age=age)
    await state.set_state(AdForm.pet_name)
    await message.answer(
        "Как зовут животное? (кличка)\n"
        "Если не знаете — отправьте /skip."
    )


@router.message(AdForm.pet_name)
async def step_pet_name(message: Message, state: FSMContext):
    pet_name = message.text.strip()
    if pet_name.lower().startswith("/"):
        pet_name = None
    await state.update_data(pet_name=pet_name)
    await state.set_state(AdForm.district)
    await message.answer("Выберите район:", reply_markup=district_kb())


@router.callback_query(AdForm.district, F.data.startswith("district:"))
async def step_district(cq: CallbackQuery, state: FSMContext):
    await state.update_data(district=cq.data.split(":", 1)[1])
    await state.set_state(AdForm.location_detail)
    await cq.answer()
    await cq.message.answer(
        "Уточните место: улица, ориентир, где именно видели/нашли "
        "(можно коротко, можно /skip)."
    )


@router.message(AdForm.location_detail)
async def step_location_detail(message: Message, state: FSMContext):
    detail = message.text.strip()
    if detail.lower().startswith("/"):
        detail = None
    await state.update_data(location_detail=detail)
    await state.set_state(AdForm.event_date)
    await message.answer(
        "Когда это произошло? Например: «утром 2 сентября» или "
        "«2.09.2026 около 18:00»."
    )


@router.message(AdForm.event_date)
async def step_event_date(message: Message, state: FSMContext):
    await state.update_data(event_date=message.text.strip())
    await state.set_state(AdForm.comment)
    await message.answer(
        "Хотите добавить комментарий? Что угодно — "
        "особые приметы, обстоятельства, награда и т.п.\n\n"
        "Отправьте /skip, если нечего добавить."
    )


@router.message(AdForm.comment)
async def step_comment(message: Message, state: FSMContext):
    comment = message.text.strip()
    if comment.lower().startswith("/"):
        comment = None
    await state.update_data(comment=comment)
    await state.set_state(AdForm.contact)
    await message.answer(
        "Как с вами связаться? Выберите, что показать в объявлении:",
        reply_markup=contact_kb(),
    )


@router.callback_query(AdForm.contact, F.data.startswith("contact:"))
async def step_contact(cq: CallbackQuery, state: FSMContext):
    contact_type = cq.data.split(":")[1]
    username = cq.from_user.username
    await cq.answer()

    if contact_type == "tg" and username:
        await state.update_data(contact=f"@{username}")
        await finish_form(cq, state)
    elif contact_type == "tg" and not username:
        await cq.message.answer(
            "У вас в профиле нет @username. Напишите, пожалуйста, "
            "номер телефона или другой способ связи:"
        )
        await state.set_state(AdForm.contact)
    else:
        await cq.message.answer(
            "Напишите номер телефона (он будет показан в объявлении).\n"
            "Если хотите добавить ещё и @username — напишите его после "
            "телефона через пробел."
        )
        await state.set_state(AdForm.contact)


@router.message(AdForm.contact)
async def step_contact_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower().startswith("/"):
        return
    await state.update_data(contact=text)
    await finish_form(message, state)


async def finish_form(source, state: FSMContext):
    data = await state.get_data()

    data["author_id"] = source.from_user.id
    data["author_username"] = source.from_user.username
    if "contact" not in data or not data["contact"]:
        if data.get("author_username"):
            data["contact"] = f"@{data['author_username']}"
        else:
            data["contact"] = "не указан"

    ad_id = await db.create_ad(dict(data))

    preview = (
        "✅ <b>Заявка принята!</b>\n\n"
        "Вот как будет выглядеть объявление:\n\n"
        + build_card_text(data)
        + "\n\nОно отправится на проверку модератору. "
        "Как только его одобрят — появится в канале."
    )

    await source.message.answer(preview, reply_markup=MAIN_KB)
    await state.clear()

    for admin_id in config.ADMIN_IDS:
        try:
            photos = data.get("photos", [])
            if photos:
                await bot.send_photo(
                    admin_id,
                    photos[0],
                    caption=build_card_text(data)
                    + "\n\n——— Модерация ———",
                    reply_markup=build_admin_keyboard(ad_id),
                )
            else:
                await bot.send_message(
                    admin_id,
                    build_card_text(data) + "\n\n——— Модерация ———",
                    reply_markup=build_admin_keyboard(ad_id),
                )
        except Exception as e:
            logging.error("Не удалось отправить на модерацию: %s", e)


# --------------------------------------------------------------------------- #
# Модерация
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("approve:"))
async def approve(cq: CallbackQuery):
    ad_id = int(cq.data.split(":")[1])
    if not is_admin(cq.from_user.id):
        await cq.answer("⛔ Только для модераторов", show_alert=True)
        return

    ad = await db.get_ad(ad_id)
    if ad is None or ad["status"] != "pending":
        await cq.answer("Объявление уже обработано", show_alert=True)
        return

    bot_suffix = f"\n\n📩 Подать объявление: @{config.BOT_USERNAME}" if config.BOT_USERNAME else ""
    text = build_card_text(ad) + bot_suffix

    close_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🐾 Найдено / Вернулся домой",
                    callback_data=f"close:{ad_id}",
                )
            ]
        ]
    )

    photos = ad["photos"] or []
    try:
        if photos:
            sent = await bot.send_photo(
                config.CHANNEL_ID, photos[0], caption=text, reply_markup=close_kb
            )
        else:
            sent = await bot.send_message(
                config.CHANNEL_ID, text, reply_markup=close_kb
            )
    except Exception as e:
        await cq.answer(f"Ошибка публикации: {e}", show_alert=True)
        return

    await db.update_status(ad_id, "published", channel_message_id=sent.message_id)
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.answer("✅ Опубликовано в канал.")


@router.callback_query(F.data.startswith("reject:"))
async def reject(cq: CallbackQuery, state: FSMContext):
    ad_id = int(cq.data.split(":")[1])
    if not is_admin(cq.from_user.id):
        await cq.answer("⛔ Только для модераторов", show_alert=True)
        return

    ad = await db.get_ad(ad_id)
    if ad is None or ad["status"] != "pending":
        await cq.answer("Объявление уже обработано", show_alert=True)
        return

    await db.update_status(ad_id, "rejected")
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.answer("❌ Отклонено.")
    try:
        await bot.send_message(ad["author_id"], "К сожалению, ваше объявление отклонено модератором.")
    except Exception as e:
        logging.error("Не удалось уведомить автора: %s", e)


# --------------------------------------------------------------------------- #
# Закрытие объявления
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("close:"))
async def close_ad(cq: CallbackQuery):
    ad_id = int(cq.data.split(":")[1])
    ad = await db.get_ad(ad_id)
    if ad is None:
        await cq.answer("Объявление не найдено", show_alert=True)
        return

    if cq.from_user.id != ad["author_id"] and not is_admin(cq.from_user.id):
        await cq.answer("⛔ Только автор или модератор", show_alert=True)
        return

    await db.update_status(ad_id, "closed")
    await cq.answer("Спасибо! Объявление закрыто.", show_alert=True)
    await cq.message.edit_reply_markup(reply_markup=None)
    try:
        await cq.message.reply("✅ <b>Объявление закрыто:</b> животное нашлось / вернулось домой.")
    except Exception as e:
        logging.error("Не удалось обновить пост: %s", e)


# --------------------------------------------------------------------------- #
# Поиск по доске
# --------------------------------------------------------------------------- #


@router.message(F.text == "🔍 Поиск по доске")
async def search_start_btn(message: Message, state: FSMContext):
    await search_start(message, state)


@router.message(Command("search"))
async def search_cmd(message: Message, state: FSMContext):
    await search_start(message, state)


async def search_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(SearchForm.kind)
    await message.answer(
        "Что ищем?", reply_markup=SEARCH_KIND_KB
    )


@router.callback_query(SearchForm.kind, F.data.startswith("skind:"))
async def search_kind(cq: CallbackQuery, state: FSMContext):
    kind = cq.data.split(":")[1]
    await state.update_data(kind=None if kind == "all" else kind)
    await state.set_state(SearchForm.animal_type)
    await cq.answer()
    await cq.message.answer("Кто это?", reply_markup=ANIMAL_KB)


@router.callback_query(SearchForm.animal_type, F.data.startswith("type:"))
async def search_animal_type(cq: CallbackQuery, state: FSMContext):
    animal_type = cq.data.split(":")[1]
    await state.update_data(animal_type=animal_type)
    await state.set_state(SearchForm.district)
    await cq.answer()
    await cq.message.answer("Выберите район:", reply_markup=district_kb())


@router.callback_query(SearchForm.district, F.data.startswith("district:"))
async def search_district(cq: CallbackQuery, state: FSMContext):
    await state.update_data(district=cq.data.split(":", 1)[1])
    await state.set_state(SearchForm.query)
    await cq.answer()
    await cq.message.answer(
        "Введите ключевое слово для поиска (например «рыжий», «метро») — "
        "или отправьте /skip, чтобы искать без ключевого слова."
    )


@router.message(SearchForm.query)
async def search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    if query.lower().startswith("/"):
        query = None
    await state.update_data(query=query)
    await run_search(message, state)


async def run_search(message: Message, state: FSMContext):
    data = await state.get_data()
    results = await db.search_ads(
        kind=data.get("kind"),
        animal_type=data.get("animal_type"),
        district=data.get("district"),
        query=data.get("query"),
        limit=10,
    )
    await state.clear()

    if not results:
        await message.answer(
            "🔍 Ничего не найдено. Попробуйте изменить район или убрать ключевое слово.",
            reply_markup=MAIN_KB,
        )
        return

    await message.answer(f"🔍 Найдено объявлений: {len(results)}", reply_markup=MAIN_KB)
    for ad in results:
        text = build_card_text(ad)
        photos = ad["photos"]
        try:
            if photos:
                await message.answer_photo(photos[0], caption=text)
            else:
                await message.answer(text)
        except Exception as e:
            logging.error("Ошибка выдачи результата поиска: %s", e)


# --------------------------------------------------------------------------- #
# Запуск
# --------------------------------------------------------------------------- #


async def main():
    await db.init_db()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
