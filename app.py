import os
import uuid
from dotenv import load_dotenv

load_dotenv()

from datetime import datetime, timezone, timedelta
from io import BytesIO
import time
import mimetypes

import pandas as pd
import requests
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
    session,
    g,
    abort,
    jsonify,
)
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client
from flask_caching import Cache

# --- Flask-Babel ---
from flask_babel import Babel, _  # <-- get_locale не импортируем!
from urllib.parse import urlencode

# --- Email супер-админа ---
SUPERADMIN_EMAIL = "musatovnikita13@gmail.com"

# --- Настройки Supabase и Flask ---
USE_LOCAL_UPLOADS = os.getenv("USE_LOCAL_UPLOADS", "False").lower() in (
    "1",
    "true",
    "yes",
)
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret")

# --- КЭШ + долгое кэширование статики ---
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = timedelta(days=30)  # 30 дней для /static


@app.context_processor
def asset_tools():
    def asset_url(filename):
        filepath = os.path.join(app.static_folder, filename)
        try:
            v = int(os.path.getmtime(filepath))  # время последнего изменения файла
        except OSError:
            v = int(time.time())  # fallback
        return url_for("static", filename=filename, v=v)

    return dict(asset_url=asset_url)


# Инициализация кэша (в проде можно RedisCache)
cache = Cache(
    app,
    config={
        "CACHE_TYPE": "SimpleCache",
        "CACHE_DEFAULT_TIMEOUT": 600,  # по умолчанию 10 мин
    },
)


def clear_index_cache():
    try:
        cache.clear()
    except Exception:
        pass


# Ключ кэша для главной: учитываем пользователя, роль, язык и фильтры в URL
def _index_cache_key():
    uid = (g.user or {}).get("id", "anon")
    role = (g.user or {}).get("role", "viewer")
    lang = str(get_locale())
    # ключ берём из session и приводим к нижнему регистру
    wh_code = (session.get("warehouse_code") or "tuusula").lower()
    args = urlencode(sorted(request.args.items()))
    return f"idx:{uid}:{role}:{lang}:{wh_code}:{args}"


# --- Настройки Flask-Mail ---
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = "artkivivarasto.noreply@gmail.com"
app.config["MAIL_PASSWORD"] = "zcpz tbdu zsau dqcw"
app.config["MAIL_DEFAULT_SENDER"] = "artkivivarasto.noreply@gmail.com"

mail = Mail(app)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Soft delete support (через колонку products.deleted_at) ---
_SOFT_DELETE_SUPPORTED = None


def soft_delete_supported() -> bool:
    """
    Проверяем один раз: есть ли колонка deleted_at у products.
    Если нет — откаты и корзина недоступны, работаем как раньше.
    """
    global _SOFT_DELETE_SUPPORTED
    if _SOFT_DELETE_SUPPORTED is not None:
        return _SOFT_DELETE_SUPPORTED
    try:
        # пробуем выбрать колонку; если её нет — PostgREST вернёт ошибку
        supabase.table("products").select("id,deleted_at").limit(1).execute()
        _SOFT_DELETE_SUPPORTED = True
    except Exception:
        _SOFT_DELETE_SUPPORTED = False
    return _SOFT_DELETE_SUPPORTED


def get_deleted_product_ids() -> set:
    """
    Вернёт id товаров с deleted_at != NULL. Если колонка отсутствует — пустое множество.
    """
    if not soft_delete_supported():
        return set()
    resp = supabase.table("products").select("id,deleted_at").execute()
    rows = resp.data or []
    return {r["id"] for r in rows if r.get("deleted_at")}


# --- Flask-Babel config ---
ALL_LANGUAGES = {"fi": "Suomi", "en": "English", "ru": "Русский"}
DEFAULT_LANGUAGES = {"fi": "Suomi", "en": "English"}
app.config["BABEL_DEFAULT_LOCALE"] = "fi"
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"


# Flask-Babel init с dynamic locale_selector
def get_locale():
    user = getattr(g, "user", None)
    # Только для супер-админа разрешаем русский язык
    languages = (
        ALL_LANGUAGES
        if user and user.get("username") == SUPERADMIN_EMAIL
        else DEFAULT_LANGUAGES
    )
    lang = session.get("lang")
    if lang and lang in languages:
        return lang
    return request.accept_languages.best_match(languages.keys())


babel = Babel()
babel.init_app(app, locale_selector=get_locale)


@app.context_processor
def inject_languages():
    user = getattr(g, "user", None)
    languages = (
        ALL_LANGUAGES
        if user and user.get("username") == SUPERADMIN_EMAIL
        else DEFAULT_LANGUAGES
    )
    return dict(LANGUAGES=languages, get_locale=get_locale)


@app.context_processor
def unit_helpers():
    def unit_label(code: str) -> str:
        """Отдаёт подпись единицы с учётом языка интерфейса."""
        loc = str(get_locale())
        c = (code or "").strip().lower()

        # нормализация старых вариантов
        if c in ("m2", "m²", "м2", "м²"):
            return "m²"  # везде одинаково
        if c in ("kg", "кг"):
            return "kg"  # везде одинаково

        if c in ("pcs", "шт", "kpl"):
            # локализуем "штуки"
            return "kpl" if loc == "fi" else ("pcs" if loc == "en" else "шт")

        # неизвестное — показываем как есть
        return code or ""

    return dict(unit_label=unit_label)


@app.route("/set_language/<lang>")
def set_language(lang):
    user = getattr(g, "user", None)
    languages = (
        ALL_LANGUAGES
        if user and user.get("username") == SUPERADMIN_EMAIL
        else DEFAULT_LANGUAGES
    )
    if lang in languages:
        session["lang"] = lang
    return redirect(request.referrer or url_for("index"))


@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="img/artkivi-logo.png"))


# =======================
#   Склады: выбор и загрузка
# =======================


def fetch_warehouses():
    """Активные склады для дропдауна в шапке (в нужном порядке)."""
    resp = (
        supabase.table("warehouses")
        .select("id,code,name_en,name_fi,name_ru,is_active,sort_order")
        .eq("is_active", True)
        .order("sort_order")  # <-- порядок по sort_order
        .order("code")  # (доп. стабильная сортировка)
        .execute()
    )
    return resp.data or []


def current_wh_id():
    """ID текущего склада из g.current_warehouse."""
    return (getattr(g, "current_warehouse", {}) or {}).get("id")


@app.before_request
def load_current_warehouse():
    if request.endpoint in ("static",):
        return

    # код склада храним в session и нормализуем
    code = (session.get("warehouse_code") or "tuusula").lower()

    # грузим список активных складов в g
    g.warehouses = fetch_warehouses()

    # ищем текущий склад по коду (в нижнем регистре с обеих сторон)
    g.current_warehouse = next(
        (w for w in g.warehouses if (w.get("code", "").lower() == code)), None
    )

    # если не нашли — берём первый активный и кладём его код в session (тоже lower)
    if not g.current_warehouse and g.warehouses:
        g.current_warehouse = g.warehouses[0]
        session["warehouse_code"] = (g.current_warehouse.get("code") or "").lower()


@app.route("/set-warehouse/<code>")
def set_warehouse(code):
    session["warehouse_code"] = (code or "").lower()
    # В разработке, если хотите, можно разкомментировать очистку кэша:
    cache.clear()
    return redirect(request.referrer or url_for("index"))


# =======================
#   Логирование действий
# =======================
def log_action(user_id, action, object_type, object_id, details=""):
    log_data = {
        "user_id": str(user_id) if user_id else None,
        "action": action,
        "object_type": object_type,
        "object_id": str(object_id) if object_id else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": details,
    }
    supabase.table("logs").insert(log_data).execute()


# =======================
#   Функция загрузки файла в Supabase Storage
# =======================
def upload_to_supabase_storage(file, filename):
    SUPABASE_PROJECT_ID = SUPABASE_URL.split("//")[-1].split(".")[0]
    bucket = "upload"
    storage_url = f"https://{SUPABASE_PROJECT_ID}.supabase.co/storage/v1/object"
    file.seek(0)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    headers = {"Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": mime_type}
    storage_path = f"{uuid.uuid4()}_{filename}"
    resp = requests.post(
        f"{storage_url}/{bucket}/{storage_path}", data=file.read(), headers=headers
    )
    if resp.status_code in (200, 201):
        public_url = f"https://{SUPABASE_PROJECT_ID}.supabase.co/storage/v1/object/public/{bucket}/{storage_path}"
        return public_url
    else:
        print("Ошибка загрузки в Supabase Storage:", resp.text)
        return None

    # =======================


#   Вспомогательные функции для фото товаров
# =======================
def add_product_images(product_id: int, files):
    """Загрузить список файлов в Storage и записать их в product_images.
    Первое добавленное фото станет главным, если главного ещё нет.
    """
    if not files:
        return []

    # проверяем, есть ли уже главное фото
    has_primary = False
    try:
        resp = (
            supabase.table("product_images")
            .select("id")
            .eq("product_id", product_id)
            .eq("is_primary", True)
            .limit(1)
            .execute()
        )
        has_primary = bool(resp.data)
    except Exception:
        has_primary = False

    inserted = []
    for i, f in enumerate(files):
        if not f or not getattr(f, "filename", ""):
            continue
        url = upload_to_supabase_storage(f, f.filename)
        if not url:
            continue
        is_primary = False
        # если у товара ещё нет главного фото — делаем первым добавленным главным
        if not has_primary and i == 0:
            is_primary = True
            has_primary = True
        row = {"product_id": product_id, "url": url, "is_primary": is_primary}
        res = supabase.table("product_images").insert(row).execute()
        if res.data:
            inserted.append(res.data[0])
    return inserted


def get_product_images(product_id: int):
    """Вернёт список фото товара (главное будет первым)."""
    try:
        resp = (
            supabase.table("product_images")
            .select("*")
            .eq("product_id", product_id)
            .order("is_primary", desc=True)
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []


def get_primary_image_url(product_id: int):
    """URL главного фото товара, если есть."""
    try:
        resp = (
            supabase.table("product_images")
            .select("url")
            .eq("product_id", product_id)
            .eq("is_primary", True)
            .single()
            .execute()
        )
        if resp.data:
            return resp.data.get("url")
    except Exception:
        pass
    return None


def get_primary_images_map(product_ids):
    """Вернёт словарь {product_id: url} для всех главных фото разом."""
    if not product_ids:
        return {}
    resp = (
        supabase.table("product_images")
        .select("product_id,url")
        .in_("product_id", product_ids)
        .eq("is_primary", True)
        .execute()
    )
    rows = resp.data or []
    return {r["product_id"]: r["url"] for r in rows}


def set_primary_image(image_id: int):
    """Сделать фото главным (сбрасывает флаг у других фото товара)."""
    # сначала узнаем product_id
    img = (
        supabase.table("product_images")
        .select("product_id")
        .eq("id", image_id)
        .single()
        .execute()
        .data
    )
    if not img:
        return False
    pid = img["product_id"]
    # сброс флага у всех фото товара
    supabase.table("product_images").update({"is_primary": False}).eq(
        "product_id", pid
    ).execute()
    # установить главного
    supabase.table("product_images").update({"is_primary": True}).eq(
        "id", image_id
    ).execute()
    return True


def delete_image(image_id: int):
    """Удалить фото. Если удалили главное, назначим другое фото главным (если осталось)."""
    # получить картинку и товар
    resp = (
        supabase.table("product_images")
        .select("*")
        .eq("id", image_id)
        .single()
        .execute()
    )
    img = resp.data
    if not img:
        return False
    pid = img["product_id"]
    was_primary = bool(img.get("is_primary"))
    # удалить
    supabase.table("product_images").delete().eq("id", image_id).execute()
    # если удалили главное — назначить новое главное, если есть
    if was_primary:
        left = (
            supabase.table("product_images")
            .select("id")
            .eq("product_id", pid)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if left:
            supabase.table("product_images").update({"is_primary": True}).eq(
                "id", left[0]["id"]
            ).execute()
    return True


# =======================
#   Вспомогательные функции для пользователей
# =======================
def get_user_by_username(username):
    resp = supabase.table("users").select("*").eq("username", username).execute()
    users = resp.data or []
    return users[0] if users else None


def get_user_by_id(user_id):
    if not user_id:
        return None
    resp = supabase.table("users").select("*").eq("id", user_id).execute()
    users = resp.data or []
    return users[0] if users else None


def create_user(username, password, role="viewer"):
    password_hash = generate_password_hash(password)
    activation_token = str(uuid.uuid4())
    resp = (
        supabase.table("users")
        .insert(
            {
                "username": username,
                "password_hash": password_hash,
                "role": role,
                "is_active": False,
                "activation_token": activation_token,
            }
        )
        .execute()
    )
    return resp.data, activation_token


# =======================
#   Декораторы для доступа
# =======================
def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


def editor_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if g.user is None or g.user.get("role") != "editor":
            flash(_("Требуются права редактора."), "danger")
            return redirect(url_for("index"))
        return view_func(*args, **kwargs)

    return wrapped_view


def superadmin_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not g.user or g.user.get("username") != SUPERADMIN_EMAIL:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped_view


# =======================
#   Flask hooks
# =======================


@app.before_request
def load_logged_in_user():
    # не трогаем запросы статики (css, картинки, favicon)
    if request.endpoint in ("static",):
        return

    user_id = session.get("user_id")
    try:
        g.user = get_user_by_id(user_id) if user_id else None
    except Exception:
        # если Supabase отвалился — не валим ответ пользователю
        g.user = None


# =======================
#   Работа с товарами и категориями через Supabase
# =======================
@cache.cached(timeout=600, key_prefix="categories_all")
def get_categories():
    resp = supabase.table("categories").select("*").execute()
    return resp.data or []


def get_products():
    resp = (
        supabase.table("products").select("*").order("created_at", desc=True).execute()
    )
    rows = resp.data if resp.data else []
    # скрываем «удалённые», если поддерживается soft delete
    if soft_delete_supported():
        rows = [r for r in rows if not r.get("deleted_at")]
    return rows


def get_category_by_id(category_id):
    if not category_id:
        return None
    resp = (
        supabase.table("categories")
        .select("*")
        .eq("id", category_id)
        .single()
        .execute()
    )
    return resp.data


def get_product_by_id(product_id):
    try:
        resp = (
            supabase.table("products")
            .select("*")
            .eq("id", product_id)
            .single()
            .execute()
        )
        return resp.data
    except Exception:
        return None


def get_total_products_count():
    """Возвращает общее число товаров быстро (без выборки всех строк)."""
    try:
        # Быстрый путь: PostgREST вернёт только заголовки и точный count
        res = (
            supabase.table("products").select("id", count="exact", head=True).execute()
        )
        return res.count or 0
    except Exception:
        # Фолбэк, если head=True недоступен
        res = (
            supabase.table("products")
            .select("id", count="exact")
            .range(0, 0)  # 0 строк данных, но count вернётся
            .execute()
        )
        return res.count or 0


def change_inventory(product_id: int, delta: float):
    """Изменяет остаток по паре (product_id, warehouse_id) и возвращает новое значение."""
    whid = current_wh_id()

    cur = (
        supabase.table("inventory")
        .select("id,quantity")
        .eq("product_id", product_id)
        .eq("warehouse_id", whid)
        .limit(1)
        .execute()
    ).data

    if cur:
        new_q = float(cur[0].get("quantity") or 0) + float(delta)
        supabase.table("inventory").update({"quantity": new_q}).eq(
            "id", cur[0]["id"]
        ).execute()
        return new_q
    else:
        new_q = max(float(delta), 0.0)
        supabase.table("inventory").insert(
            {"product_id": product_id, "warehouse_id": whid, "quantity": new_q}
        ).execute()
        return new_q


# =======================
#   Главная страница с фильтрацией (ОБНОВЛЁННЫЙ КОД!)
# =======================


@app.route("/")
@login_required
@cache.cached(timeout=15, key_prefix=_index_cache_key)
def index():
    # Категории и язык
    categories = get_categories()
    current_lang = str(get_locale())

    # Текущий склад
    wh_id = current_wh_id()

    # Флаги/фильтры из querystring
    zero_only = request.args.get("zero_only") == "1"
    search = (request.args.get("search") or "").strip()
    category_id = request.args.get("category_id", type=int)
    size_filter = (request.args.get("size") or "").strip()
    price_filter = (request.args.get("price") or "").replace(",", ".").strip()
    quantity_filter = (request.args.get("quantity") or "").replace(",", ".").strip()

    # Базовый запрос
    q = (
        supabase.table("v_products_by_warehouse")
        .select(
            "id,name,description,unit,size,price,category_id,image_url,created_at,warehouse_id,wh_quantity"
        )
        .eq("warehouse_id", wh_id)
    )

    # Нули / ненули
    if zero_only:
        q = q.eq("wh_quantity", 0)
    else:
        q = q.gt("wh_quantity", 0)

    # Фильтры — СРАЗУ в запрос
    if search:
        q = q.ilike("name", f"%{search}%")
    if size_filter:
        q = q.ilike("size", f"%{size_filter}%")
    if category_id:
        q = q.eq("category_id", category_id)
    if price_filter:
        try:
            q = q.lte("price", float(price_filter))
        except Exception:
            pass
    if quantity_filter and not zero_only:
        try:
            q = q.gte("wh_quantity", float(quantity_filter))
        except Exception:
            pass

    # Заберём все подходящие строки (без пагинации)
    resp = q.order("created_at", desc=True).range(0, 999).execute()
    products_all = resp.data or []

    # скрыть товары из «корзины» (если включён soft delete)
    if soft_delete_supported():
        deleted_ids = get_deleted_product_ids()
        if deleted_ids:
            products_all = [p for p in products_all if p["id"] not in deleted_ids]

    # Посчитаем derived-поля
    for p in products_all:
        qty = float(p.get("wh_quantity") or 0)
        unit_price = float(p.get("price") or 0)
        p["quantity"] = qty
        p["total_price"] = round(qty * unit_price, 2)

    # Локализация названий категорий
    cat_dict = {}
    for c in categories:
        if current_lang == "fi":
            cat_dict[c["id"]] = c.get("name_fi") or c.get("name") or ""
        elif current_lang == "en":
            cat_dict[c["id"]] = c.get("name_en") or c.get("name") or ""
        elif current_lang == "ru":
            cat_dict[c["id"]] = c.get("name_ru") or c.get("name") or ""
        else:
            cat_dict[c["id"]] = c.get("name") or ""

    # Главные фото разом
    ids = [p["id"] for p in products_all]
    prim_map = get_primary_images_map(ids)

    # Дополняем поля для шаблона
    for p in products_all:
        p["category_name"] = cat_dict.get(p.get("category_id"), "")
        p["primary_image_url"] = prim_map.get(p["id"]) or p.get("image_url")

    # Статистика/правый блок
    total_products = len(products_all)
    total_categories = len(categories)
    recent_products = products_all[:5]

    return render_template(
        "index.html",
        products=products_all,
        categories=categories,
        total_products=total_products,
        total_categories=total_categories,
        recent_products=recent_products,
        current_lang=current_lang,
    )


# =======================
#   Остальные view-функции (без изменений)
# =======================
"""
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if not username or not password:
            flash(_("Заполните все поля!"), "warning")
            return redirect(url_for("register"))
        if get_user_by_username(username):
            flash(_("Пользователь уже существует!"), "warning")
            return redirect(url_for("register"))
        user_data, activation_token = create_user(username, password, role="viewer")
        try:
            base_url = os.getenv("BASE_URL")
            if not base_url:
                base_url = request.url_root.strip("/")
            activation_link = f"{base_url}/activate/{activation_token}"
            msg = Message(
                subject=_("Активация аккаунта в системе учёта товаров"),
                recipients=[username],
                html=f'''
                    <h3>{_('Здравствуйте!')}</h3>
                    <p>{_('Для завершения регистрации нажмите на кнопку ниже:')}</p>
                    <p><a href="{activation_link}" style="display:inline-block;padding:8px 14px;background:#4CAF50;color:white;text-decoration:none;border-radius:3px">{_('Активировать аккаунт')}</a></p>
                    <p>{_('Или перейдите по ссылке:')}<br>{activation_link}</p>
                    <hr>
                    <small>{_('Если вы не регистрировались, просто проигнорируйте это письмо.')}</small>
                '''
            )
            mail.send(msg)
            flash(_("На ваш email отправлено письмо для активации аккаунта."), "success")
        except Exception as e:
            print("Ошибка при отправке письма:", e)
            flash(_("Регистрация прошла, но письмо не отправлено. Обратитесь к администратору."), "warning")
        return redirect(url_for("login"))
    return render_template("register.html")
"""


@app.route("/activate/<token>")
def activate_account(token):
    resp = supabase.table("users").select("*").eq("activation_token", token).execute()
    users = resp.data or []
    if not users:
        flash(_("Ссылка недействительна или уже использована."), "danger")
        return redirect(url_for("login"))
    user = users[0]
    if user.get("is_active"):
        flash(_("Аккаунт уже активирован."), "info")
        return redirect(url_for("login"))
    supabase.table("users").update({"is_active": True, "activation_token": None}).eq(
        "id", user["id"]
    ).execute()
    flash(_("Аккаунт успешно активирован! Теперь вы можете войти."), "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        user = get_user_by_username(username)
        if not user or not check_password_hash(user["password_hash"], password):
            flash(_("Неверный логин или пароль!"), "danger")
            return redirect(url_for("login"))
        if not user.get("is_active"):
            flash(
                _(
                    "Аккаунт не активирован! Проверьте email и перейдите по ссылке для активации."
                ),
                "warning",
            )
            return redirect(url_for("login"))
        session.clear()
        session["user_id"] = user["id"]
        flash(_("Вход выполнен!"), "success")
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash(_("Вы вышли из аккаунта."), "success")
    return redirect(url_for("login"))


@app.route("/logs")
@login_required
@superadmin_required
def logs():
    resp = (
        supabase.table("logs")
        .select("*")
        .order("timestamp", desc=True)
        .limit(200)
        .execute()
    )
    logs = resp.data or []

    # подтянем имена пользователей разом
    user_ids = {l["user_id"] for l in logs if l.get("user_id")}
    users_dict = {}
    if user_ids:
        user_resp = (
            supabase.table("users")
            .select("id,username")
            .in_("id", list(user_ids))
            .execute()
        )
        for u in user_resp.data or []:
            users_dict[str(u["id"])] = u["username"]

    # usernames в каждую запись
    for l in logs:
        l["username"] = users_dict.get(str(l.get("user_id")), "")

    # какие delete можно откатить (если включён soft delete)
    deleted_set = get_deleted_product_ids() if soft_delete_supported() else set()
    for l in logs:
        l["can_undo"] = (
            soft_delete_supported()
            and l.get("action") == "delete"
            and l.get("object_type") == "product"
            and l.get("object_id")
            and int(l["object_id"]) in deleted_set
        )

    return render_template("logs.html", logs=logs)


@app.post("/logs/<int:log_id>/undo")
@login_required
@superadmin_required
def undo_log(log_id):
    # получаем запись журнала
    lresp = supabase.table("logs").select("*").eq("id", log_id).single().execute()
    log = lresp.data
    if not log:
        flash(_("Запись журнала не найдена."), "danger")
        return redirect(url_for("logs"))

    # поддерживаем откат только для delete product
    if (
        log.get("action") != "delete"
        or log.get("object_type") != "product"
        or not log.get("object_id")
    ):
        flash(_("Эту операцию нельзя отменить."), "warning")
        return redirect(url_for("logs"))

    if not soft_delete_supported():
        flash(_("Восстановление недоступно: корзина не включена."), "warning")
        return redirect(url_for("logs"))

    pid = int(log["object_id"])

    # состояние товара сейчас
    presp = (
        supabase.table("products")
        .select("id,deleted_at,name")
        .eq("id", pid)
        .single()
        .execute()
    )
    prod = presp.data
    if not prod:
        flash(_("Товар не найден — восстановление невозможно."), "danger")
        return redirect(url_for("logs"))

    if not prod.get("deleted_at"):
        flash(_("Товар уже активен."), "info")
        return redirect(url_for("logs"))

    # собственно восстановление
    supabase.table("products").update({"deleted_at": None}).eq("id", pid).execute()

    log_action(
        g.user["id"],
        "restore",
        "product",
        pid,
        _("Отмена удаления по записи журнала #%(id)s", id=log_id),
    )

    flash(
        _(
            'Удаление отменено: товар "%(name)s" восстановлен.',
            name=(prod.get("name") or f"#{pid}"),
        ),
        "success",
    )

    clear_index_cache()  # сбросили кэш главной

    # 👇 если в форме передали next — уважаем его, иначе остаёмся на /logs
    next_url = request.form.get("next") or request.args.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("logs"))


@app.route("/export_logs")
@login_required
@superadmin_required
def export_logs():
    resp = (
        supabase.table("logs")
        .select("*")
        .order("timestamp", desc=True)
        .limit(1000)
        .execute()
    )
    logs = resp.data or []
    filtered_logs = []
    for l in logs:
        filtered_logs.append(
            {
                _("Пользователь"): l.get("user_id"),
                _("Действие"): l.get("action"),
                _("Тип объекта"): l.get("object_type"),
                _("ID объекта"): l.get("object_id"),
                _("Дата и время"): l.get("timestamp"),
                _("Детали"): l.get("details"),
            }
        )
    df = pd.DataFrame(filtered_logs)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Logs")
    output.seek(0)
    return send_file(
        output,
        download_name="logs.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/create", methods=["GET", "POST"])
@login_required
@editor_required
def create():
    categories = get_categories()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        quantity = request.form.get("quantity", "0").replace(",", ".").strip()
        unit = request.form.get("unit", "шт")
        size = request.form.get("size", "").strip()
        price = request.form.get("price", "").strip()
        category_id = request.form.get("category_id") or None
        image_url = None

        if "image" in request.files:
            image = request.files["image"]
            if image and image.filename:
                image_url = upload_to_supabase_storage(image, image.filename)

        if not name:
            flash(_("Введите название товара"), "warning")
            return redirect(url_for("create"))
        try:
            quantity = float(quantity)
            price = float(price)
        except ValueError:
            flash(_("Некорректное число! Введите, например, 10 или 10.5"), "warning")
            return redirect(url_for("create"))

        # --- несколько фото: files из <input name="images" multiple> ---
        new_images = request.files.getlist("images")  # список файлов
        # создаём товар; image_url пока пусто (заполним главным фото ниже)
        prod = {
            "name": name,
            "description": description,
            "quantity": quantity,
            "unit": unit,
            "size": size,
            "price": price,
            "category_id": int(category_id) if category_id else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "image_url": None,
        }
        result = supabase.table("products").insert(prod).execute()
        product_id = (
            result.data[0]["id"] if result.data and "id" in result.data[0] else None
        )

        # добавляем фото в product_images
        added = add_product_images(product_id, new_images)
        if added:
            # в products.image_url храним главное фото (для обратной совместимости)
            primary = next((x for x in added if x.get("is_primary")), added[0])
            supabase.table("products").update({"image_url": primary.get("url")}).eq(
                "id", product_id
            ).execute()

        log_action(
            g.user["id"], "create", "product", product_id, _("Добавлен товар: ") + name
        )
        flash(_('Товар "%(name)s" добавлен!', name=name), "success")
        return redirect(url_for("index"))

    # GET-запрос — просто показать форму
    return render_template("create.html", categories=categories)


@app.route("/edit/<int:product_id>", methods=["GET", "POST"])
@login_required
@editor_required
def edit(product_id):
    product = get_product_by_id(product_id)
    categories = get_categories()
    if not product:
        flash(_("Товар не найден!"), "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        # поля формы
        name = request.form.get("name", product["name"]).strip()
        description = request.form.get(
            "description", product.get("description", "")
        ).strip()
        quantity = request.form.get(
            "quantity", str(product.get("quantity", "0"))
        ).replace(",", ".")
        unit = request.form.get("unit", product.get("unit", "шт"))
        size = request.form.get("size", product.get("size", ""))
        price = request.form.get("price", str(product.get("price", "")))
        category_id = request.form.get("category_id") or None

        # одиночное «старое» главное фото (если выбрали новый файл)
        image_url = product.get("image_url")
        if "image" in request.files:
            image = request.files["image"]
            if image and image.filename:
                image_url = upload_to_supabase_storage(image, image.filename)

        # валидация чисел
        try:
            quantity = float(quantity)
            price = float(price)
        except ValueError:
            flash(_("Некорректное число! Введите, например, 10 или 10.5"), "warning")
            return redirect(url_for("edit", product_id=product_id))

        # обновляем товар
        updated = {
            "name": name,
            "description": description,
            "quantity": quantity,
            "unit": unit,
            "size": size,
            "price": price,
            "category_id": int(category_id) if category_id else None,
            "image_url": image_url,
        }
        supabase.table("products").update(updated).eq("id", product_id).execute()

        # --- добавить несколько фото из этой же формы ---
        files_all = request.files.getlist("images")  # name="images" в edit.html
        new_images = [f for f in files_all if getattr(f, "filename", "").strip()]
        if new_images:
            added = add_product_images(product_id, new_images)
            if added:
                # синхронизируем главное фото
                primary_url = get_primary_image_url(product_id)
                if primary_url:
                    supabase.table("products").update({"image_url": primary_url}).eq(
                        "id", product_id
                    ).execute()

        # лог/уведомление и редирект — всегда после обновления
        log_action(
            g.user["id"], "edit", "product", product_id, _("Обновлён товар: ") + name
        )
        flash(_('Товар "%(name)s" обновлён!', name=name), "success")
        return redirect(url_for("index"))

    # ------ GET: подготовка галереи ------
    images = get_product_images(product_id)
    if not images and product.get("image_url"):
        images = [{"id": None, "url": product["image_url"], "is_primary": True}]

    return render_template(
        "edit.html", product=product, categories=categories, images=images
    )


@app.post("/product/<int:product_id>/consume")
@login_required
@editor_required
def consume_stock(product_id):
    amount_raw = (request.form.get("amount") or "").replace(",", ".").strip()
    note = (request.form.get("note") or "").strip()

    try:
        amount = float(amount_raw)
    except Exception:
        flash(_("Введите корректное число."), "warning")
        return redirect(url_for("view", product_id=product_id))
    if amount <= 0:
        flash(_("Количество должно быть больше нуля."), "warning")
        return redirect(url_for("view", product_id=product_id))

    product = get_product_by_id(product_id)
    if not product:
        flash(_("Товар не найден!"), "danger")
        return redirect(url_for("index"))

    whid = current_wh_id()
    # текущий остаток по складу
    cur = (
        supabase.table("inventory")
        .select("quantity")
        .eq("product_id", product_id)
        .eq("warehouse_id", whid)
        .limit(1)
        .execute()
    ).data
    current_qty = float(cur[0]["quantity"]) if cur else 0.0

    if amount > current_qty:
        unit = product.get("unit") or ""
        flash(
            _(
                "Нельзя списать %(a).2f — на складе только %(q).2f.",
                a=amount,
                q=current_qty,
            ),
            "danger",
        )
        return redirect(url_for("view", product_id=product_id))

    new_qty = change_inventory(product_id, -amount)

    # движение
    supabase.table("stock_movements").insert(
        {
            "product_id": product_id,
            "user_id": g.user.get("id"),
            "warehouse_id": whid,
            "delta": -amount,
            "note": note,
        }
    ).execute()

    unit = product.get("unit") or ""
    log_action(
        g.user["id"],
        "consume",
        "product",
        product_id,
        _("Списано со склада: -%(a).2f %(u)s", a=amount, u=unit),
    )
    flash(
        _(
            "Списано %(a).2f %(u)s. Остаток: %(r).2f %(u)s.",
            a=amount,
            u=unit,
            r=new_qty,
        ),
        "success",
    )
    return redirect(url_for("view", product_id=product_id))


@app.post("/product/<int:product_id>/add")
@login_required
@editor_required
def add_stock(product_id):
    amount_raw = (request.form.get("amount") or "").replace(",", ".").strip()
    note = (request.form.get("note") or "").strip()

    try:
        amount = float(amount_raw)
    except Exception:
        flash(_("Введите корректное число."), "warning")
        return redirect(url_for("view", product_id=product_id))
    if amount <= 0:
        flash(_("Количество должно быть больше нуля."), "warning")
        return redirect(url_for("view", product_id=product_id))

    product = get_product_by_id(product_id)
    if not product:
        flash(_("Товар не найден!"), "danger")
        return redirect(url_for("index"))

    new_qty = change_inventory(product_id, +amount)

    supabase.table("stock_movements").insert(
        {
            "product_id": product_id,
            "user_id": g.user.get("id"),
            "warehouse_id": current_wh_id(),
            "delta": amount,
            "note": note,
        }
    ).execute()

    unit = product.get("unit") or ""
    log_action(
        g.user["id"],
        "restock",
        "product",
        product_id,
        _("Поступление на склад: +%(a).2f %(u)s", a=amount, u=unit),
    )
    flash(
        _(
            "Добавлено %(a).2f %(u)s. Теперь на складе: %(r).2f %(u)s.",
            a=amount,
            u=unit,
            r=new_qty,
        ),
        "success",
    )
    return redirect(url_for("view", product_id=product_id))


@app.route("/delete/<int:product_id>", methods=["POST"])
@login_required
@editor_required
def delete(product_id):
    product = get_product_by_id(product_id)
    if not product:
        flash(_("Товар не найден! Возможно, он уже был удалён."), "warning")
        return redirect(url_for("index"))

    now_iso = datetime.now(timezone.utc).isoformat()

    if soft_delete_supported():
        # мягкое удаление — помечаем deleted_at
        supabase.table("products").update({"deleted_at": now_iso}).eq(
            "id", product_id
        ).execute()
        msg = _("Товар удалён!")
    else:
        # жёсткое удаление (как было)
        supabase.table("products").delete().eq("id", product_id).execute()
        msg = _("Товар удалён!")

    log_action(
        g.user["id"],
        "delete",
        "product",
        product_id,
        _("Удалён товар: ") + (product["name"] if product else str(product_id)),
    )

    flash(msg, "success")
    clear_index_cache()

    # куда вернуть после удаления
    next_url = request.form.get("next") or request.args.get("next")
    if next_url and next_url.startswith("/"):  # защита от внешних ссылок
        return redirect(next_url)

    # по умолчанию — на главную таблицу
    return redirect(url_for("index"))


@app.post("/product/<int:product_id>/images/add")
@login_required
@editor_required
def add_images_route(product_id):
    files = request.files.getlist("images")
    if not get_product_by_id(product_id):
        flash(_("Товар не найден!"), "danger")
        return redirect(url_for("index"))
    add_product_images(product_id, files)
    # синхронизируем поле image_url в products
    prim = get_primary_image_url(product_id)
    if prim:
        supabase.table("products").update({"image_url": prim}).eq(
            "id", product_id
        ).execute()
    flash(_("Фото добавлены"), "success")
    return redirect(url_for("edit", product_id=product_id))


@app.post("/product_images/<int:image_id>/delete")
@login_required
@editor_required
def delete_image_route(image_id):
    # узнаем товар, чтобы вернуться на его страницу редактирования
    resp = (
        supabase.table("product_images")
        .select("product_id")
        .eq("id", image_id)
        .single()
        .execute()
    )
    row = resp.data
    if not row:
        flash(_("Фото не найдено"), "warning")
        return redirect(url_for("index"))
    product_id = row["product_id"]
    delete_image(image_id)
    # обновим products.image_url
    prim = get_primary_image_url(product_id)
    supabase.table("products").update({"image_url": prim}).eq(
        "id", product_id
    ).execute()
    flash(_("Фото удалено"), "success")
    return redirect(url_for("edit", product_id=product_id))


@app.post("/product_images/<int:image_id>/set_primary")
@login_required
@editor_required
def set_primary_image_route(image_id):
    # узнаем товар, чтобы вернуться
    resp = (
        supabase.table("product_images")
        .select("product_id")
        .eq("id", image_id)
        .single()
        .execute()
    )
    row = resp.data
    if not row:
        flash(_("Фото не найдено"), "warning")
        return redirect(url_for("index"))
    product_id = row["product_id"]
    if set_primary_image(image_id):
        prim = get_primary_image_url(product_id)
        supabase.table("products").update({"image_url": prim}).eq(
            "id", product_id
        ).execute()
        flash(_("Главное фото обновлено"), "success")
    return redirect(url_for("edit", product_id=product_id))


@app.post("/product/<int:product_id>/image/clear")
@login_required
@editor_required
def clear_legacy_image(product_id):
    """
    Удаляет единственное «наследное» фото, которое хранится в products.image_url,
    когда в таблице product_images нет записей. Если записи есть, просто
    синхронизирует products.image_url с главным фото.
    """
    product = get_product_by_id(product_id)
    if not product:
        flash(_("Товар не найден!"), "danger")
        return redirect(url_for("index"))

    # Есть ли фото в product_images?
    rows = (
        supabase.table("product_images")
        .select("id")
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    ).data or []

    if rows:
        # В галерее уже есть фото — ставим в products.image_url «главное»
        prim = get_primary_image_url(product_id)
        supabase.table("products").update({"image_url": prim}).eq(
            "id", product_id
        ).execute()
    else:
        # ГАЛЕРЕЯ ПУСТА — просто очищаем поле у товара
        supabase.table("products").update({"image_url": None}).eq(
            "id", product_id
        ).execute()

    flash(_("Фото удалено"), "success")
    return redirect(url_for("edit", product_id=product_id))


@app.route("/add_category", methods=["GET", "POST"])
@login_required
@editor_required
def add_category():
    # GET — оставляем старую страницу как fallback (если JS выключен)
    if request.method == "GET":
        return render_template("add_category.html")

    # POST — поддержим и обычную форму, и AJAX (JSON)
    name = ""
    if request.is_json:
        name = (request.json.get("name") or "").strip()
    else:
        name = (request.form.get("name") or "").strip()

    if not name:
        if request.is_json:
            return jsonify(success=False, message=_("Введите название категории.")), 400
        flash(_("Введите название категории."), "warning")
        return redirect(url_for("add_category"))

    # проверка дубликатов (без учёта регистра/пробелов)
    exists = any(
        (c.get("name") or "").strip().lower() == name.lower() for c in get_categories()
    )
    if exists:
        if request.is_json:
            return jsonify(success=False, message=_("Такая категория уже есть.")), 409
        flash(_("Такая категория уже есть."), "warning")
        return redirect(url_for("add_category"))

    # создаём
    result = supabase.table("categories").insert({"name": name}).execute()
    cat_id = result.data[0]["id"] if result.data and "id" in result.data[0] else None

    # сбрасываем кэш категорий
    cache.delete("categories_all")

    log_action(
        g.user["id"], "create", "category", cat_id, _("Добавлена категория: ") + name
    )

    # Ответы
    if request.is_json:
        return jsonify(success=True, id=cat_id, name=name)

    flash(_('Категория "%(name)s" создана!', name=name), "success")
    return redirect(url_for("index"))


@app.route("/view/<int:product_id>")
@login_required
def view(product_id):
    product = get_product_by_id(product_id)
    if not product:
        flash(_("Товар не найден!"), "danger")
        return redirect(url_for("index"))
    category = get_category_by_id(product.get("category_id"))

    # форматирование даты
    created_at_str = product.get("created_at")
    if isinstance(created_at_str, str):
        try:
            dt = datetime.fromisoformat(created_at_str.replace("Z", ""))
            product["created_at_fmt"] = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            product["created_at_fmt"] = created_at_str[:16].replace("T", " ")
    else:
        product["created_at_fmt"] = ""

    # Кол-во на выбранном складе
    whid = current_wh_id()
    iq = (
        supabase.table("inventory")
        .select("quantity")
        .eq("product_id", product_id)
        .eq("warehouse_id", whid)
        .limit(1)
        .execute()
    )
    product["wh_quantity"] = iq.data[0]["quantity"] if iq.data else 0

    # фото
    images = get_product_images(product_id)
    if not images and product.get("image_url"):
        images = [{"id": None, "url": product["image_url"], "is_primary": True}]

    # История движений по выбранному складу
    limit = 20
    if request.args.get("all") == "1":
        limit = 200

    mov_q = (
        supabase.table("stock_movements")
        .select("id,product_id,user_id,delta,note,created_at")
        .eq("product_id", product_id)
        .eq("warehouse_id", whid)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    movements = mov_q.data or []

    # подтянем имена пользователей
    user_ids = {m.get("user_id") for m in movements if m.get("user_id")}
    users_map = {}
    if user_ids:
        uresp = (
            supabase.table("users")
            .select("id,username")
            .in_("id", list(user_ids))
            .execute()
        )
        for u in uresp.data or []:
            users_map[str(u["id"])] = u["username"]

    # формат даты + username
    for m in movements:
        m["username"] = users_map.get(str(m.get("user_id")), "")
        ts = m.get("created_at")
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", ""))
                m["created_at_fmt"] = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                m["created_at_fmt"] = ts[:16].replace("T", " ")
        else:
            m["created_at_fmt"] = ""

    return render_template(
        "view.html",
        product=product,
        category=category,
        images=images,
        movements=movements,
        movements_limit=limit,
    )


@app.route("/export_excel")
@login_required
@editor_required
def export_excel():
    products = get_products()
    filtered_products = []
    for p in products:
        filtered_products.append(
            {
                _("Название"): p.get("name"),
                _("Описание"): p.get("description"),
                _("Количество"): p.get("quantity"),
                _("Ед. изм."): p.get("unit"),
                _("Размер"): p.get("size"),
                _("Цена (€)"): p.get("price"),
            }
        )
    df = pd.DataFrame(filtered_products)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Products")
    output.seek(0)
    return send_file(
        output,
        download_name="products.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/edit_category_name", methods=["POST"])
@login_required
@editor_required
def edit_category_name():
    data = request.get_json()
    cat_id = data.get("id")
    new_name = data.get("name", "").strip()
    if not cat_id or not new_name:
        return jsonify(success=False, message="Нет данных")
    supabase.table("categories").update({"name": new_name}).eq("id", cat_id).execute()

    # <-- сбрасываем кэш категорий
    cache.delete("categories_all")

    log_action(
        g.user["id"], "edit", "category", cat_id, f"Переименована категория: {new_name}"
    )
    return jsonify(success=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5111))
    app.run(debug=True, host="0.0.0.0", port=port)
