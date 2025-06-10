from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import os
import pandas as pd
from io import BytesIO
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
db = SQLAlchemy(app)

# Модели
class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)

# Главная страница
@app.route('/')
def index():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('index.html', products=products)

# Добавление товара
@app.route('/create', methods=['GET', 'POST'])
def create():
    categories = Category.query.order_by(Category.name).all()
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        quantity = request.form['quantity']
        price = request.form['price']
        category_id = request.form.get('category_id')
        image_file = request.files.get('image')

        image_filename = None
        if image_file and image_file.filename:
            image_filename = secure_filename(image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

        if not name or not quantity or not price:
            flash('Заполните все обязательные поля!', 'warning')
            return redirect(url_for('create'))

        try:
            quantity = int(quantity)
            price = float(price)
        except ValueError:
            flash('Количество должно быть целым числом, цена — числом.', 'warning')
            return redirect(url_for('create'))

        new_product = Product(
            name=name,
            description=description,
            quantity=quantity,
            price=price,
            category_id=category_id if category_id else None,
            image_filename=image_filename
        )
        db.session.add(new_product)
        db.session.commit()
        flash(f'Товар "{name}" добавлен!', 'success')
        return redirect(url_for('index'))

    return render_template('create.html', categories=categories)

# Просмотр товара
@app.route('/view/<int:product_id>')
def view(product_id):
    product = Product.query.get_or_404(product_id)
    return render_template('view.html', product=product)

# Редактирование товара
@app.route('/edit/<int:product_id>', methods=['GET', 'POST'])
def edit(product_id):
    product = Product.query.get_or_404(product_id)
    categories = Category.query.order_by(Category.name).all()

    if request.method == 'POST':
        product.name = request.form['name']
        product.description = request.form['description']
        product.quantity = int(request.form['quantity'])
        product.price = float(request.form['price'])
        category_id = request.form.get('category_id')
        product.category_id = category_id if category_id else None

        image_file = request.files.get('image')
        if image_file and image_file.filename:
            filename = secure_filename(image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            product.image_filename = filename

        db.session.commit()
        flash(f'Товар "{product.name}" обновлён!', 'success')
        return redirect(url_for('index'))

    return render_template('edit.html', product=product, categories=categories)

# Удаление товара
@app.route('/delete/<int:product_id>', methods=['POST'])
def delete(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash(f'Товар "{product.name}" удалён!', 'success')
    return redirect(url_for('index'))

# Добавление категории
@app.route('/add_category', methods=['GET', 'POST'])
def add_category():
    if request.method == 'POST':
        name = request.form['name']
        if not name:
            flash('Введите название категории.', 'warning')
            return redirect(url_for('add_category'))

        if Category.query.filter_by(name=name).first():
            flash('Категория с таким названием уже существует.', 'warning')
            return redirect(url_for('add_category'))

        new_category = Category(name=name)
        db.session.add(new_category)
        db.session.commit()
        flash(f'Категория "{name}" добавлена!', 'success')
        return redirect(url_for('index'))

    return render_template('add_category.html')

# Отчёты
@app.route('/reports')
def reports():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('reports.html', products=products)

# Экспорт в Excel
@app.route('/export_excel')
def export_excel():
    products = Product.query.all()
    data = [{
        'ID': p.id,
        'Название': p.name,
        'Описание': p.description,
        'Категория': p.category.name if p.category else '',
        'Кол-во': p.quantity,
        'Цена': p.price,
        'Дата': p.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for p in products]

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Products')

    output.seek(0)
    return send_file(output, download_name='products.xlsx', as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5111)
