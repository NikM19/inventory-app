import os
from datetime import datetime
from io import BytesIO

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, send_file, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pandas as pd
from supabase import create_client

# Настройки Supabase и Flask
USE_LOCAL_UPLOADS = os.getenv("USE_LOCAL_UPLOADS", "False").lower() in ("1", "true", "yes")
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =======================
#   Вспомогательные функции для пользователей
# =======================
def get_user_by_username(username):
    resp = supabase.table("users").select("*").eq("username", username).execute()
    users = resp.data or []
    return users[0] if users else None

def get_user_by_id(user_id):
    resp = supabase.table("users").select("*").eq("id", user_id).single().execute()
    return resp.data

def create_user(username, password, role="viewer"):
    password_hash = generate_password_hash(password)
    resp = supabase.table("users").insert({"username": username, "password_hash": password_hash, "role": role}).execute()
    return resp.data

@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = get_user_by_id(user_id) if user_id else None

def login_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view_func(**kwargs)
    return wrapped_view

def editor_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapped_view(**kwargs):
        if g.user is None or g.user.get("role") != "editor":
            flash("Требуются права редактора.", "danger")
            return redirect(url_for("index"))
        return view_func(**kwargs)
    return wrapped_view

# =======================
#   Аутентификация и регистрация
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
        create_user(username, password, role="viewer")
        flash("Вы успешно зарегистрировались! Теперь войдите.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        user = get_user_by_username(username)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Неверный логин или пароль!", "danger")
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
#   Работа с товарами и категориями через Supabase
# =======================

def get_categories():
    resp = supabase.table("categories").select("*").execute()
    return resp.data if resp.data else []

def get_products():
    resp = supabase.table("products").select("*").order("created_at", desc=True).execute()
    return resp.data if resp.data else []

def get_category_by_id(category_id):
    if not category_id: return None
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
#   Добавление товара
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
        if not name:
            flash("Введите название товара", "warning")
            return redirect(url_for("create"))
        try:
            quantity = float(quantity)
            price = float(price)
        except ValueError:
            flash("Некорректное число!", "warning")
            return redirect(url_for("create"))
        prod = {
            "name": name,
            "description": description,
            "quantity": quantity,
            "unit": unit,
            "size": size,
            "price": price,
            "category_id": int(category_id) if category_id else None,
            "created_at": datetime.utcnow().isoformat()
        }
        supabase.table("products").insert(prod).execute()
        flash(f'Товар "{name}" добавлен!', "success")
        return redirect(url_for("index"))
    return render_template("create.html", categories=categories)

# =======================
#   Редактирование товара
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
        try:
            quantity = float(quantity)
            price = float(price)
        except ValueError:
            flash("Некорректное число!", "warning")
            return redirect(url_for("edit", product_id=product_id))
        updated = {
            "name": name,
            "description": description,
            "quantity": quantity,
            "unit": unit,
            "size": size,
            "price": price,
            "category_id": int(category_id) if category_id else None
        }
        supabase.table("products").update(updated).eq("id", product_id).execute()
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
    supabase.table("products").delete().eq("id", product_id).execute()
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
        supabase.table("categories").insert({"name": name}).execute()
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
    return render_template("view.html", product=product, category=category)

# =======================
#   Экспорт в Excel
# =======================
@app.route("/export_excel")
@login_required
@editor_required
def export_excel():
    products = get_products()
    for p in products:
        if isinstance(p["created_at"], str):
            p["created_at"] = p["created_at"].replace("T", " ").split(".")[0]
    df = pd.DataFrame(products)
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
