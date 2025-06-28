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

# ——— Настройка Supabase-клиента —————————————————————————————
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
# ——————————————————————————————————————————————————————————

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret')

# переключаем SQLAlchemy на PostgreSQL по DATABASE_URL
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# папка для сохранения загруженных изображений (если используете)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
db = SQLAlchemy(app)

# ====== Описание моделей (ORM) ===================================
class Category(db.Model):
    __tablename__ = 'categories'
    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(100), nullable=False, unique=True)
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    __tablename__   = 'products'
    id             = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(120), nullable=False)
    description    = db.Column(db.Text, nullable=True)
    quantity       = db.Column(db.Integer, nullable=False)
    price          = db.Column(db.Float,   nullable=False)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    category_id    = db.Column(
        db.Integer,
        db.ForeignKey('categories.id'),
        nullable=True
    )
    image_filename = db.Column(db.String(255), nullable=True)
# =================================================================

# ——— Главная ————————————————————————————————————————————————
@app.route('/')
def index():
    # пример чтения через ORM
    products = Product.query.order_by(Product.created_at.desc()).all()

    # если захотите читать через Supabase API, то:
    # resp = supabase.table('products').select('*').order('created_at', desc=True).execute()
    # products = resp.data

    return render_template('index.html', products=products)

# ——— Создать товар —————————————————————————————————————————
@app.route('/create', methods=['GET', 'POST'])
def create():
    categories = Category.query.order_by(Category.name).all()

    if request.method == 'POST':
        name        = request.form['name']
        description = request.form.get('description', '')
        quantity    = request.form.get('quantity', '0')
        price       = request.form.get('price', '0')
        category_id = request.form.get('category_id')
        image_file  = request.files.get('image')

        # сохраняем картинку (если есть)
        image_filename = None
        if image_file and image_file.filename:
            image_filename = secure_filename(image_file.filename)
            dst = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
            image_file.save(dst)

        # проверяем обязательные поля
        if not name or not quantity or not price:
            flash('Заполните все обязательные поля!', 'warning')
            return redirect(url_for('create'))

        try:
            quantity = int(quantity)
            price    = float(price)
        except ValueError:
            flash('Количество должно быть целым числом, цена — числом.', 'warning')
            return redirect(url_for('create'))

        # ORM-вставка
        new_item = Product(
            name=name,
            description=description,
            quantity=quantity,
            price=price,
            category_id=category_id or None,
            image_filename=image_filename
        )
        db.session.add(new_item)
        db.session.commit()
        flash(f'Товар "{name}" добавлен!', 'success')
        return redirect(url_for('index'))

    return render_template('create.html', categories=categories)

# ——— Просмотр товара —————————————————————————————————————————
@app.route('/view/<int:product_id>')
def view(product_id):
    product = Product.query.get_or_404(product_id)
    return render_template('view.html', product=product)

# ——— Редактировать товар ——————————————————————————————————————
@app.route('/edit/<int:product_id>', methods=['GET', 'POST'])
def edit(product_id):
    product    = Product.query.get_or_404(product_id)
    categories = Category.query.order_by(Category.name).all()

    if request.method == 'POST':
        product.name        = request.form['name']
        product.description = request.form.get('description', '')
        product.quantity    = int(request.form.get('quantity', product.quantity))
        product.price       = float(request.form.get('price', product.price))
        cid = request.form.get('category_id')
        product.category_id = cid or None

        # если новая картинка
        image_file = request.files.get('image')
        if image_file and image_file.filename:
            filename = secure_filename(image_file.filename)
            dst = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image_file.save(dst)
            product.image_filename = filename

        db.session.commit()
        flash(f'Товар "{product.name}" обновлён!', 'success')
        return redirect(url_for('index'))

    return render_template(
        'edit.html',
        product=product,
        categories=categories
    )

# ——— Удалить товар ———————————————————————————————————————————
@app.route('/delete/<int:product_id>', methods=['POST'])
def delete(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash(f'Товар "{product.name}" удалён!', 'success')
    return redirect(url_for('index'))

# ——— Создать категорию ————————————————————————————————————————
@app.route('/add_category', methods=['GET', 'POST'])
def add_category():
    if request.method == 'POST':
        name = request.form['name']
        if not name:
            flash('Введите название категории.', 'warning')
            return redirect(url_for('add_category'))
        if Category.query.filter_by(name=name).first():
            flash('Такая категория уже есть.', 'warning')
            return redirect(url_for('add_category'))
        cat = Category(name=name)
        db.session.add(cat)
        db.session.commit()
        flash(f'Категория "{name}" создана!', 'success')
        return redirect(url_for('index'))

    return render_template('add_category.html')

# ——— Отчёты ————————————————————————————————————————————————
@app.route('/reports')
def reports():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('reports.html', products=products)

# ——— Экспорт в Excel ——————————————————————————————————————————
@app.route('/export_excel')
def export_excel():
    data = []
    for p in Product.query.all():
        data.append({
            'ID': p.id,
            'Название': p.name,
            'Описание': p.description or '',
            'Категория': p.category.name if p.category else '',
            'Кол-во': p.quantity,
            'Цена': p.price,
            'Дата': p.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        })

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Products')
    output.seek(0)

    return send_file(
        output,
        download_name='products.xlsx',
        as_attachment=True,
        mimetype=(
            'application/vnd.openxmlformats-officedocument'
            '.spreadsheetml.sheet'
        )
    )

# ————————————————————————————————————————————————————————————————

if __name__ == '__main__':
    # Для локальной разработки
    app.run(debug=True, host='0.0.0.0', port=5111)
