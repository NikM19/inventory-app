import os
import uuid
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from io import BytesIO

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, send_file, session, g, abort
)
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from supabase import create_client
from functools import wraps
import requests
import mimetypes

# --- Flask-Babel ---
from flask_babel import Babel, _   # <-- здесь всё хорошо!

# --- Email супер-админа ---
SUPERADMIN_EMAIL = "musatovnikita13@gmail.com"

# --- Настройки Supabase и Flask ---
USE_LOCAL_UPLOADS = os.getenv("USE_LOCAL_UPLOADS", "False").lower() in ("1", "true", "yes")
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret")

# --- Настройки Flask-Mail ---
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'artkivivarasto.noreply@gmail.com'
app.config['MAIL_PASSWORD'] = 'zcpz tbdu zsau dqcw'
app.config['MAIL_DEFAULT_SENDER'] = 'artkivivarasto.noreply@gmail.com'

mail = Mail(app)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Flask-Babel config ---
ALL_LANGUAGES = {
    'fi': 'Suomi',
    'en': 'English',
    'ru': 'Русский'
}
DEFAULT_LANGUAGES = {
    'fi': 'Suomi',
    'en': 'English'
}
app.config['BABEL_DEFAULT_LOCALE'] = 'fi'
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

# Flask-Babel init с dynamic locale_selector
def get_locale():
    user = getattr(g, 'user', None)
    # Только для супер-админа разрешаем русский язык
    languages = ALL_LANGUAGES if user and user.get("username") == SUPERADMIN_EMAIL else DEFAULT_LANGUAGES
    lang = session.get('lang')
    if lang and lang in languages:
        return lang
    return request.accept_languages.best_match(languages.keys())

babel = Babel()
babel.init_app(app, locale_selector=get_locale)

@app.context_processor
def inject_languages():
    user = getattr(g, 'user', None)
    languages = ALL_LANGUAGES if user and user.get("username") == SUPERADMIN_EMAIL else DEFAULT_LANGUAGES
    return dict(LANGUAGES=languages, get_locale=get_locale)

@app.route('/set_language/<lang>')
def set_language(lang):
    user = getattr(g, 'user', None)
    languages = ALL_LANGUAGES if user and user.get("username") == SUPERADMIN_EMAIL else DEFAULT_LANGUAGES
    if lang in languages:
        session['lang'] = lang
    return redirect(request.referrer or url_for('index'))

# =======================
#   Логирование действий
# =======================
def log_action(user_id, action, object_type, object_id, details=""):
    log_data = {
        "user_id": str(user_id) if user_id else None,
        "action": action,
        "object_type": object_type,
        "object_id": str(object_id) if object_id else None,
        "timestamp": datetime.utcnow().isoformat(),
        "details": details
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
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": mime_type
    }
    storage_path = f"{uuid.uuid4()}_{filename}"
    resp = requests.post(
        f"{storage_url}/{bucket}/{storage_path}",
        data=file.read(),
        headers=headers
    )
    if resp.status_code in (200, 201):
        public_url = f"https://{SUPABASE_PROJECT_ID}.supabase.co/storage/v1/object/public/{bucket}/{storage_path}"
        return public_url
    else:
        print("Ошибка загрузки в Supabase Storage:", resp.text)
        return None

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
    resp = supabase.table("users").insert({
        "username": username,
        "password_hash": password_hash,
        "role": role,
        "is_active": False,
        "activation_token": activation_token
    }).execute()
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
    user_id = session.get("user_id")
    g.user = get_user_by_id(user_id) if user_id else None

# =======================
#   Работа с товарами и категориями через Supabase
# =======================
def get_categories():
    resp = supabase.table("categories").select("*").execute()
    return resp.data if resp.data else []

def get_products():
    resp = supabase.table("products").select("*").order("created_at", desc=True).execute()
    return resp.data if resp.data else []

def get_category_by_id(category_id):
    if not category_id:
        return None
    resp = supabase.table("categories").select("*").eq("id", category_id).single().execute()
    return resp.data

def get_product_by_id(product_id):
    try:
        resp = supabase.table("products").select("*").eq("id", product_id).single().execute()
        return resp.data
    except Exception:
        return None

# =======================
#   Главная страница с фильтрацией (ОБНОВЛЁННЫЙ КОД!)
# =======================
from flask_babel import get_locale

@app.route("/")
@login_required
def index():
    products = get_products()
    categories = get_categories()

    # Получаем текущий язык (fi, en, ru)
    current_lang = str(get_locale())  # fi / en / ru

    # --- Словарь: id -> название на нужном языке ---
    cat_dict = {}
    for c in categories:
        # Логика для мультиязычного отображения названия категории
        # Структура в БД: name, name_fi, name_en, name_ru
        # name_fi по умолчанию
        if current_lang == "fi":
            cat_dict[c["id"]] = c.get("name_fi") or c.get("name") or ""
        elif current_lang == "en":
            cat_dict[c["id"]] = c.get("name_en") or c.get("name") or ""
        elif current_lang == "ru":
            cat_dict[c["id"]] = c.get("name_ru") or c.get("name") or ""
        else:
            cat_dict[c["id"]] = c.get("name") or ""

    # --- Фильтры из request.args ---
    search = request.args.get('search', '').strip().lower()
    category_id = request.args.get('category_id', '')
    size = request.args.get('size', '').strip().lower()
    price = request.args.get('price', '').replace(',', '.').strip()
    quantity = request.args.get('quantity', '').replace(',', '.').strip()

    # --- Фильтрация по всем полям ---
    filtered = []
    for p in products:
        if search and search not in p.get('name', '').lower():
            continue
        if category_id:
            if not p.get('category_id') or str(p.get('category_id')) != str(category_id):
                continue
        if size and size not in str(p.get('size', '')).lower():
            continue
        try:
            if price:
                if p.get('price') is None or float(p.get('price')) > float(price):
                    continue
        except Exception:
            pass
        try:
            if quantity:
                if p.get('quantity') is None or float(p.get('quantity')) < float(quantity):
                    continue
        except Exception:
            pass
        # Здесь — имя категории на нужном языке
        p["category_name"] = cat_dict.get(p["category_id"], "")
        filtered.append(p)

    total_products = len(products)
    total_categories = len(categories)
    recent_products = products[:5]

    return render_template("index.html",
        products=filtered,
        categories=categories,
        total_products=total_products,
        total_categories=total_categories,
        recent_products=recent_products,
        current_lang=current_lang  # <-- добавили!
    )

# =======================
#   Остальные view-функции (без изменений)
# =======================

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
                html=f"""
                    <h3>{_('Здравствуйте!')}</h3>
                    <p>{_('Для завершения регистрации нажмите на кнопку ниже:')}</p>
                    <p><a href="{activation_link}" style="display:inline-block;padding:8px 14px;background:#4CAF50;color:white;text-decoration:none;border-radius:3px">{_('Активировать аккаунт')}</a></p>
                    <p>{_('Или перейдите по ссылке:')}<br>{activation_link}</p>
                    <hr>
                    <small>{_('Если вы не регистрировались, просто проигнорируйте это письмо.')}</small>
                """
            )
            mail.send(msg)
            flash(_("На ваш email отправлено письмо для активации аккаунта."), "success")
        except Exception as e:
            print("Ошибка при отправке письма:", e)
            flash(_("Регистрация прошла, но письмо не отправлено. Обратитесь к администратору."), "warning")
        return redirect(url_for("login"))
    return render_template("register.html")

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
    supabase.table("users").update({"is_active": True, "activation_token": None}).eq("id", user["id"]).execute()
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
            flash(_("Аккаунт не активирован! Проверьте email и перейдите по ссылке для активации."), "warning")
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
    resp = supabase.table("logs").select("*").order("timestamp", desc=True).limit(200).execute()
    logs = resp.data or []
    user_ids = {l["user_id"] for l in logs if l.get("user_id")}
    users_dict = {}
    if user_ids:
        user_resp = supabase.table("users").select("id,username").in_("id", list(user_ids)).execute()
        for u in user_resp.data or []:
            users_dict[str(u["id"])] = u["username"]
    for l in logs:
        l["username"] = users_dict.get(str(l.get("user_id")), "")
    return render_template("logs.html", logs=logs)

@app.route("/export_logs")
@login_required
@superadmin_required
def export_logs():
    resp = supabase.table("logs").select("*").order("timestamp", desc=True).limit(1000).execute()
    logs = resp.data or []
    filtered_logs = []
    for l in logs:
        filtered_logs.append({
            _("Пользователь"): l.get("user_id"),
            _("Действие"): l.get("action"),
            _("Тип объекта"): l.get("object_type"),
            _("ID объекта"): l.get("object_id"),
            _("Дата и время"): l.get("timestamp"),
            _("Детали"): l.get("details"),
        })
    df = pd.DataFrame(filtered_logs)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Logs")
    output.seek(0)
    return send_file(
        output,
        download_name="logs.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/create", methods=["GET", "POST"])
@login_required
@editor_required
def create():
    categories = get_categories()
    if request.method == "POST":
        name        = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        quantity    = request.form.get("quantity", "0").replace(",", ".").strip()
        unit        = request.form.get("unit", "шт")
        size        = request.form.get("size", "").strip()
        price       = request.form.get("price", "").strip()
        category_id = request.form.get("category_id") or None
        image_url   = None

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
        prod = {
            "name": name,
            "description": description,
            "quantity": quantity,
            "unit": unit,
            "size": size,
            "price": price,
            "category_id": int(category_id) if category_id else None,
            "created_at": datetime.utcnow().isoformat(),
            "image_url": image_url,
        }
        result = supabase.table("products").insert(prod).execute()
        product_id = result.data[0]["id"] if result.data and "id" in result.data[0] else None
        log_action(g.user["id"], "create", "product", product_id, _('Добавлен товар: ') + name)
        flash(_('Товар "%(name)s" добавлен!', name=name), "success")
        return redirect(url_for("index"))
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
        name        = request.form.get("name", product["name"]).strip()
        description = request.form.get("description", product.get("description", "")).strip()
        quantity    = request.form.get("quantity", str(product.get("quantity", "0"))).replace(",", ".")
        unit        = request.form.get("unit", product.get("unit", "шт"))
        size        = request.form.get("size", product.get("size", ""))
        price       = request.form.get("price", str(product.get("price", "")))
        category_id = request.form.get("category_id") or None

        image_url = product.get("image_url")
        if "image" in request.files:
            image = request.files["image"]
            if image and image.filename:
                image_url = upload_to_supabase_storage(image, image.filename)

        try:
            quantity = float(quantity)
            price = float(price)
        except ValueError:
            flash(_("Некорректное число! Введите, например, 10 или 10.5"), "warning")
            return redirect(url_for("edit", product_id=product_id))
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
        log_action(g.user["id"], "edit", "product", product_id, _('Обновлён товар: ') + name)
        flash(_('Товар "%(name)s" обновлён!', name=name), "success")
        return redirect(url_for("index"))
    return render_template("edit.html", product=product, categories=categories)

@app.route("/delete/<int:product_id>", methods=["POST"])
@login_required
@editor_required
def delete(product_id):
    product = get_product_by_id(product_id)
    if not product:
        flash(_("Товар не найден! Возможно, он уже был удалён."), "warning")
        return redirect(url_for("index"))
    supabase.table("products").delete().eq("id", product_id).execute()
    log_action(g.user["id"], "delete", "product", product_id, _('Удалён товар: ') + (product["name"] if product else str(product_id)))
    flash(_("Товар удалён!"), "success")
    return redirect(url_for("index"))

@app.route("/add_category", methods=["GET", "POST"])
@login_required
@editor_required
def add_category():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash(_("Введите название категории."), "warning")
            return redirect(url_for("add_category"))
        if any(c["name"] == name for c in get_categories()):
            flash(_("Такая категория уже есть."), "warning")
            return redirect(url_for("add_category"))
        result = supabase.table("categories").insert({"name": name}).execute()
        cat_id = result.data[0]["id"] if result.data and "id" in result.data[0] else None
        log_action(g.user["id"], "create", "category", cat_id, _('Добавлена категория: ') + name)
        flash(_('Категория "%(name)s" создана!', name=name), "success")
        return redirect(url_for("index"))
    return render_template("add_category.html")

@app.route("/view/<int:product_id>")
@login_required
def view(product_id):
    product = get_product_by_id(product_id)
    if not product:
        flash(_("Товар не найден!"), "danger")
        return redirect(url_for("index"))
    category = get_category_by_id(product.get("category_id"))
    created_at_str = product.get("created_at")
    if isinstance(created_at_str, str):
        try:
            dt = datetime.fromisoformat(created_at_str.replace("Z", ""))
            product["created_at_fmt"] = dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            product["created_at_fmt"] = created_at_str[:16].replace('T', ' ')
    else:
        product["created_at_fmt"] = ""
    return render_template("view.html", product=product, category=category)

@app.route("/export_excel")
@login_required
@editor_required
def export_excel():
    products = get_products()
    filtered_products = []
    for p in products:
        filtered_products.append({
            _("Название"): p.get("name"),
            _("Описание"): p.get("description"),
            _("Количество"): p.get("quantity"),
            _("Ед. изм."): p.get("unit"),
            _("Размер"): p.get("size"),
            _("Цена (€)"): p.get("price"),
            _("Картинка"): p.get("image_url"),
        })
    df = pd.DataFrame(filtered_products)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Products")
    output.seek(0)
    return send_file(
        output,
        download_name="products.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
from flask import jsonify

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
    log_action(g.user["id"], "edit", "category", cat_id, f'Переименована категория: {new_name}')
    return jsonify(success=True)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5111))
    app.run(debug=True, host="0.0.0.0", port=port)