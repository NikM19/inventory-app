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
    bucket = "upload"   # <= Везде используем существующий бакет!
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
            flash("Требуются права редактора.", "danger")
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
#   Аутентификация и регистрация с email-активацией
# =======================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if not username or not password:
            flash("Заполните все поля!", "warning")
            return redirect(url_for("register"))
        if get_user_by_username(username):
            flash("Пользователь уже существует!", "warning")
            return redirect(url_for("register"))
        _, activation_token = create_user(username, password, role="viewer")
        try:
            base_url = os.getenv("BASE_URL")
            if not base_url:
                base_url = request.url_root.strip("/")
            activation_link = f"{base_url}/activate/{activation_token}"
            msg = Message(
                subject="Активация аккаунта в системе учёта товаров",
                recipients=[username],
                html=f"""
                    <h3>Здравствуйте!</h3>
                    <p>Для завершения регистрации нажмите на кнопку ниже:</p>
                    <p><a href="{activation_link}" style="display:inline-block;padding:8px 14px;background:#4CAF50;color:white;text-decoration:none;border-radius:3px">Активировать аккаунт</a></p>
                    <p>Или перейдите по ссылке:<br>{activation_link}</p>
                    <hr>
                    <small>Если вы не регистрировались, просто проигнорируйте это письмо.</small>
                """
            )
            mail.send(msg)
            flash("На ваш email отправлено письмо для активации аккаунта.", "success")
        except Exception as e:
            print("Ошибка при отправке письма:", e)
            flash("Регистрация прошла, но письмо не отправлено. Обратитесь к администратору.", "warning")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/activate/<token>")
def activate_account(token):
    resp = supabase.table("users").select("*").eq("activation_token", token).execute()
    users = resp.data or []
    if not users:
        flash("Ссылка недействительна или уже использована.", "danger")
        return redirect(url_for("login"))
    user = users[0]
    if user.get("is_active"):
        flash("Аккаунт уже активирован.", "info")
        return redirect(url_for("login"))
    supabase.table("users").update({"is_active": True, "activation_token": None}).eq("id", user["id"]).execute()
    flash("Аккаунт успешно активирован! Теперь вы можете войти.", "success")
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        user = get_user_by_username(username)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Неверный логин или пароль!", "danger")
            return redirect(url_for("login"))
        if not user.get("is_active"):
            flash("Аккаунт не активирован! Проверьте email и перейдите по ссылке для активации.", "warning")
            return redirect(url_for("login"))
        session.clear()
        session["user_id"] = user["id"]
        flash("Вход выполнен!", "success")
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("login"))

# =======================
#   Логи — только для супер-админа
# =======================
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
            "Пользователь": l.get("user_id"),
            "Действие": l.get("action"),
            "Тип объекта": l.get("object_type"),
            "ID объекта": l.get("object_id"),
            "Дата и время": l.get("timestamp"),
            "Детали": l.get("details"),
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
    resp = supabase.table("products").select("*").eq("id", product_id).single().execute()
    return resp.data

# =======================
#   Главная страница
# =======================
@app.route("/")
@login_required
def index():
    products = get_products()
    categories = get_categories()
    cat_dict = {c["id"]: c["name"] for c in categories}
    for p in products:
        p["category_name"] = cat_dict.get(p["category_id"], "")
    total_products = len(products)
    total_categories = len(categories)
    recent_products = products[:5]
    return render_template("index.html",
        products=products,
        categories=categories,
        total_products=total_products,
        total_categories=total_categories,
        recent_products=recent_products
    )

# =======================
#   Добавление товара (Supabase Storage)
# =======================
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
            flash("Введите название товара", "warning")
            return redirect(url_for("create"))
        try:
            quantity = float(quantity)
            price = float(price)
        except ValueError:
            flash("Некорректное число! Введите, например, 10 или 10.5", "warning")
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
        log_action(g.user["id"], "create", "product", product_id, f'Добавлен товар: {name}')
        flash(f'Товар "{name}" добавлен!', "success")
        return redirect(url_for("index"))
    return render_template("create.html", categories=categories)

# =======================
#   Редактирование товара (Supabase Storage)
# =======================
@app.route("/edit/<int:product_id>", methods=["GET", "POST"])
@login_required
@editor_required
def edit(product_id):
    product = get_product_by_id(product_id)
    categories = get_categories()
    if not product:
        flash("Товар не найден!", "danger")
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
            flash("Некорректное число! Введите, например, 10 или 10.5", "warning")
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
        log_action(g.user["id"], "edit", "product", product_id, f'Обновлён товар: {name}')
        flash(f'Товар "{name}" обновлён!', "success")
        return redirect(url_for("index"))
    return render_template("edit.html", product=product, categories=categories)

# =======================
#   Удаление товара
# =======================
@app.route("/delete/<int:product_id>", methods=["POST"])
@login_required
@editor_required
def delete(product_id):
    product = get_product_by_id(product_id)
    supabase.table("products").delete().eq("id", product_id).execute()
    log_action(g.user["id"], "delete", "product", product_id, f'Удалён товар: {product["name"] if product else product_id}')
    flash("Товар удалён!", "success")
    return redirect(url_for("index"))

# =======================
#   Добавление категории
# =======================
@app.route("/add_category", methods=["GET", "POST"])
@login_required
@editor_required
def add_category():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Введите название категории.", "warning")
            return redirect(url_for("add_category"))
        if any(c["name"] == name for c in get_categories()):
            flash("Такая категория уже есть.", "warning")
            return redirect(url_for("add_category"))
        result = supabase.table("categories").insert({"name": name}).execute()
        cat_id = result.data[0]["id"] if result.data and "id" in result.data[0] else None
        log_action(g.user["id"], "create", "category", cat_id, f'Добавлена категория: {name}')
        flash(f'Категория "{name}" создана!', "success")
        return redirect(url_for("index"))
    return render_template("add_category.html")

# =======================
#   Просмотр товара
# =======================
@app.route("/view/<int:product_id>")
@login_required
def view(product_id):
    product = get_product_by_id(product_id)
    if not product:
        flash("Товар не найден!", "danger")
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

# =======================
#   Экспорт в Excel
# =======================
@app.route("/export_excel")
@login_required
@editor_required
def export_excel():
    products = get_products()
    filtered_products = []
    for p in products:
        filtered_products.append({
            "Название": p.get("name"),
            "Описание": p.get("description"),
            "Количество": p.get("quantity"),
            "Ед. изм.": p.get("unit"),
            "Размер": p.get("size"),
            "Цена (€)": p.get("price"),
            "Картинка": p.get("image_url"),
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5111))
    app.run(debug=True, host="0.0.0.0", port=port)