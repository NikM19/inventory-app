import os
from datetime import datetime
from io import BytesIO

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, send_file
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

import pandas as pd
from supabase import create_client

# ——————————————————————————————————————————————————————————
#              Настройки
# ——————————————————————————————————————————————————————————

# Если True, при локальной разработке будем сохранять в static/uploads
# и брать URL как "/static/uploads/…".
# В продакшене ставьте False, чтобы все файлы уезжали в Supabase Storage.
USE_LOCAL_UPLOADS = os.getenv("USE_LOCAL_UPLOADS", "False").lower() in ("1", "true", "yes")

# Flask app
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret")

# SQLAlchemy на PostgreSQL
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Папка для локальных загрузок (используется только если USE_LOCAL_UPLOADS=True)
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")

db = SQLAlchemy(app)

# Инициализация Supabase-клиента (используется если USE_LOCAL_UPLOADS=False)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ——————————————————————————————————————————————————————————
#              Модели
# ——————————————————————————————————————————————————————————

class Category(db.Model):
    __tablename__ = "categories"

    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    products = db.relationship("Product", backref="category", lazy=True)


class Product(db.Model):
    __tablename__ = "products"

    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(120), nullable=False)
    description    = db.Column(db.Text, nullable=True)
    quantity  = db.Column(db.Integer, nullable=False, default=0)
    price     = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at= db.Column(db.DateTime, default=datetime.utcnow)
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=True
    )

    # Храним публичный URL картинки
    image_url = db.Column(db.Text, nullable=True)


# ——————————————————————————————————————————————————————————
#              Хелпер: загрузка файла
# ——————————————————————————————————————————————————————————

def upload_image(file_storage) -> str | None:
    """
    Сохраняет картинку либо локально, либо в Supabase Storage.
    Возвращает публичный URL (или относительный путь).
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)

    if USE_LOCAL_UPLOADS:
        # Сохраняем в папку static/uploads
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        dst = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file_storage.save(dst)
        # возвращаем относительный URL для браузера
        return url_for("static", filename=f"uploads/{filename}", _external=False)

    # — Загрузка в Supabase Storage —
    bucket_name = "product-images"  # ваш бакет
    object_path = f"public/{filename}"

    # загружаем "сырые" байты
    file_storage.stream.seek(0)
    data = file_storage.stream.read()
    supabase.storage.from_(bucket_name).upload(object_path, data)

    # получаем публичный URL
    public = supabase.storage.from_(bucket_name).get_public_url(object_path)
    return public["publicURL"]


# ——————————————————————————————————————————————————————————
#               Маршруты
# ——————————————————————————————————————————————————————————

@app.route("/")
def index():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template("index.html", products=products)


@app.route("/create", methods=["GET", "POST"])
def create():
    categories = Category.query.order_by(Category.name).all()

    if request.method == "POST":
        name        = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        quantity    = request.form.get("quantity", "0").strip()
        price       = request.form.get("price", "0").strip()
        category_id = request.form.get("category_id") or None
        image_file  = request.files.get("image")

        # обязательные поля
        if not name:
            flash("Введите название товара", "warning")
            return redirect(url_for("create"))

        try:
            quantity = int(quantity)
            price    = float(price)
        except ValueError:
            flash("Количество должно быть целым, цена – числом", "warning")
            return redirect(url_for("create"))

        # загружаем картинку и получаем URL
        img_url = upload_image(image_file)

        new_item = Product(
            name=name,
            description=description,
            quantity=quantity,
            price=price,
            category_id=category_id,
            image_url=img_url,
        )
        db.session.add(new_item)
        db.session.commit()
        flash(f'Товар "{name}" добавлен!', "success")
        return redirect(url_for("index"))

    return render_template("create.html", categories=categories)


@app.route("/view/<int:product_id>")
def view(product_id):
    product = Product.query.get_or_404(product_id)
    return render_template("view.html", product=product)


@app.route("/edit/<int:product_id>", methods=["GET", "POST"])
def edit(product_id):
    product    = Product.query.get_or_404(product_id)
    categories = Category.query.order_by(Category.name).all()

    if request.method == "POST":
        product.name        = request.form.get("name", product.name).strip()
        product.description = request.form.get("description", product.description).strip()
        product.quantity    = int(request.form.get("quantity", product.quantity))
        product.price       = float(request.form.get("price", product.price))
        product.category_id = request.form.get("category_id") or None

        # если новая картинка
        new_file = request.files.get("image")
        if new_file and new_file.filename:
            product.image_url = upload_image(new_file)

        db.session.commit()
        flash(f'Товар "{product.name}" обновлён!', "success")
        return redirect(url_for("index"))

    return render_template("edit.html", product=product, categories=categories)


@app.route("/delete/<int:product_id>", methods=["POST"])
def delete(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash(f'Товар "{product.name}" удалён!', "success")
    return redirect(url_for("index"))


@app.route("/add_category", methods=["GET", "POST"])
def add_category():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Введите название категории.", "warning")
            return redirect(url_for("add_category"))
        if Category.query.filter_by(name=name).first():
            flash("Такая категория уже есть.", "warning")
            return redirect(url_for("add_category"))

        cat = Category(name=name)
        db.session.add(cat)
        db.session.commit()
        flash(f'Категория "{name}" создана!', "success")
        return redirect(url_for("index"))

    return render_template("add_category.html")


@app.route("/reports")
def reports():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template("reports.html", products=products)


@app.route("/export_excel")
def export_excel():
    data = []
    for p in Product.query.all():
        data.append({
            "ID":          p.id,
            "Название":    p.name,
            "Описание":    p.description or "",
            "Категория":   p.category.name if p.category else "",
            "Кол-во":      p.quantity,
            "Цена":        float(p.price),
            "Дата":        p.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "Изобр. URL":  p.image_url or "",
        })

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Products")
    output.seek(0)

    return send_file(
        output,
        download_name="products.xlsx",
        as_attachment=True,
        mimetype=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
    )


# ——————————————————————————————————————————————————————————
#               Точка входа
# ——————————————————————————————————————————————————————————
if __name__ == "__main__":
    # для локального запуска
    app.run(debug=True, host="0.0.0.0", port=5111)
